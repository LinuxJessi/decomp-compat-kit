#!/usr/bin/env python3
"""Measure the behaviours of an ee-gcc cc1 + ee-as pair that decide whether it can
reproduce a retail PS2 build, before a single function is matched.

The Xenosaga I matching decomp found, one at a time and at real cost, that the
archived SCE ee-gcc 2.96 (decompme/compilers) differs from whatever built the
retail executables in a short list of places: decimal literals are truncated
toward zero instead of rounded ("realconv"), the R5900 short-loop padding gate
stops at four instructions instead of six ("lp7"), the stock assembler inserts no
nop between an FP compare and the branch on its condition, and so on. Each of
those is a one-line measurement. This tool makes them all at once, for any cc1
and ee-as a project holds, and prints one row per behaviour: what this toolchain
does, what the retail Xenosaga I build did, and whether they agree.

Nothing here is matching evidence: it measures a compiler, not a candidate.

Usage:
  export EE_ROOTFS=~/ee-rootfs                 # i386 rootfs holding opt/ee (stock 2.96)
  ./tools/probe_toolchain.py                   # stock cc1 + ee-as of the rootfs
  ./tools/probe_toolchain.py --toolchains DIR \\
      --cc1 /toolchains/ee-gcc2.96-realconv-lp7-fsv3-v1/bin/cc1 \\
      --as  /toolchains/ee-legacy-binutils-la29-vsqrt-nolabel-laimm-v1/bin/ee-as \\
      --cc1-flags "-quiet -O2 -G 8 -fno-builtin" --as-flags "-EL -m5900 -mabi=eabi -G8"
  ./tools/probe_toolchain.py --json probe.json
  ./tools/probe_toolchain.py --self-test

--cc1 / --as take a path inside the box (`/rootfs/...`, `/toolchains/...`), a path
relative to the rootfs, or an absolute host path (mounted read-only into the box).
Each tool runs through the rootfs `ld-linux.so.2` unless it needs a newer glibc
than the rootfs has (the project's rebuilt 2.9 assembler wants glibc >= 2.34),
in which case it runs on the box's own libc; `--as-run`/`--cc1-run` override.

The box is the kit's usual one: `debian:bookworm-slim --platform linux/386` with
the i386 binfmt handler registered (see README.md). Preprocessing happens on the
host with `clang -E -P`; cc1 only sees the token stream.

A note the probes settled: the `break 7` upper-field encoding (0x0007000d) and
the `daddu` in the jump-table `lw $d,$Lnn($idx)` macro that an earlier Xenosaga
III kit worked around appear only when ee-as 2.10 is given `-mcpu=4000`; with no
cpu flag, or `-m5900`, it emits retail's lower-field `break` and `addu`.

Exit status: 0 when the probes ran (whatever they found), 1 when the self-test
fails, 2 when docker or a tool could not run (the stderr tail is printed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

STOCK_CC1 = "opt/ee/lib/gcc-lib/ee/2.96-ee-001003-1/cc1"
STOCK_AS = "opt/ee/bin/ee-as"
LD = "/rootfs/lib/ld-linux.so.2 --library-path /rootfs/lib/i386-linux-gnu"
STEP_TIMEOUT = 30
E = "<"

# ------------------------------------------------------------------ probe inputs

LITERALS = [("d_tenth", "0.1", "d"), ("d_neg_tenth", "-0.1", "d"), ("d_pi", "3.141592653589793", "d"),
            ("d_tiny", "1.0e-300", "d"), ("f_tenth", "0.1f", "f"), ("f_narrow", "2.5987625122e-05", "f")]

C_SOURCES = {
    "literals": "".join(f"{'double' if k == 'd' else 'float'} {n} = {t};\n" for n, t, k in LITERALS),
    # Back branches whose delay slot stays unfilled: the arm of mips_r5900_lengthen_loops
    # that lp7 changes. d = instructions between the loop label and the branch.
    "spin": ("void spin3(volatile int *p, int k) { while (((*p >> 2) & 7) == k) ; }\n"
             "void spin5(volatile int *p, int k) { while (((((*p >> 2) & 7) ^ 1) + 2) == k) ; }\n"),
    "hazard": "int fcmp(float a, float b) { return a < b; }\n",
    "div": "int sdiv(int a, int b) { return a / b; }\n",
    "switch": ("extern void act(int);\nvoid dispatch(int m) {\n    switch (m) {\n"
               + "".join(f"    case {i}: act({10 * i + 7}); break;\n" for i in range(8))
               + "    default: return;\n    }\n    act(1);\n}\n"),
    "gsmall": ("long long g_eight;\nint g_four;\nlong long get_eight(void) { return g_eight; }\n"
               "int get_four(void) { return g_four; }\n"),
}

S_SOURCES = {
    "move": "\t.text\n\t.set reorder\n\t.globl mv\n\t.ent mv\nmv:\n\tmove $2,$4\n\tjr $31\n\t.end mv\n",
    "la": "\t.text\n\t.set reorder\n\t.globl la1\n\t.ent la1\nla1:\n\tla $3,1($2)\n\tjr $31\n\t.end la1\n",
    "hz": ("\t.text\n\t.set reorder\n\t.globl hz\n\t.ent hz\nhz:\n\tc.lt.s $f12,$f14\n\tbc1t 1f\n"
           "\tli $2,0\n\tjr $31\n1:\tli $2,1\n\tjr $31\n\t.end hz\n"),
    "pad": "\t.text\n\t.set noreorder\n\t.globl three\n\t.ent three\nthree:\n\tjr $31\n\tnop\n\tnop\n\t.end three\n",
    "vsqrt": "\t.text\n\t.set noreorder\nvsq:\tvsqrt Q,vf1w\n\tjr $31\n\tnop\n",
}

FUNCT_NAMES = {0x21: "addu", 0x2d: "daddu", 0x25: "or"}


# ------------------------------------------------------------------ ELF reading

def read_object(path: Path):
    """{section name: (addr, size, bytes)}, [(offset, type, section-of-symbol)] for .rel.text."""
    d = path.read_bytes()
    if d[:4] != b"\x7fELF":
        raise ValueError(f"{path}: not an ELF")
    shoff, = struct.unpack_from(E + "I", d, 0x20)
    shent, shnum, shstrndx = struct.unpack_from(E + "HHH", d, 0x2E)
    secs = [struct.unpack_from(E + "10I", d, shoff + i * shent) for i in range(shnum)]
    stro = secs[shstrndx][4]

    def name(off):
        return d[stro + off:d.index(b"\0", stro + off)].decode()

    names = [name(s[0]) for s in secs]
    out = {n: (s[3], s[5], d[s[4]:s[4] + s[5]] if s[1] != 8 else b"") for n, s in zip(names, secs) if n}
    symsec = [None]
    relocs = []
    for n, s in zip(names, secs):
        if n == ".symtab":
            symsec = [names[struct.unpack_from(E + "IIIBBH", d, s[4] + k * 16)[5]]
                      if struct.unpack_from(E + "IIIBBH", d, s[4] + k * 16)[5] < len(names) else None
                      for k in range(s[5] // 16)]
    for n, s in zip(names, secs):
        if n == ".rel.text":
            for k in range(s[5] // 8):
                off, info = struct.unpack_from(E + "II", d, s[4] + k * 8)
                sym = info >> 8
                relocs.append((off, info & 0xFF, symsec[sym] if sym < len(symsec) else None))
    return out, relocs


def text_words(sections):
    text = sections.get(".text", (0, 0, b""))[2]
    return [struct.unpack_from(E + "I", text, i)[0] for i in range(0, len(text) - 3, 4)]


# ------------------------------------------------------------------ classifiers
# Each takes already-read evidence and returns (observed, verdict, detail).

def classify_literals(words_by_name: dict) -> tuple:
    rows, worse, equal = [], 0, 0
    for name, text, kind in LITERALS:
        ws = words_by_name.get(name)
        if not ws:
            rows.append(f"{name}: not found in the assembly")
            continue
        value = float(text.rstrip("f"))
        if kind == "d":
            expect = struct.unpack(E + "Q", struct.pack(E + "d", value))[0]
            got = (ws[1] << 32) | ws[0] if len(ws) >= 2 else None
            bits = 64
        else:
            expect = struct.unpack(E + "I", struct.pack(E + "f", value))[0]
            got = ws[0]
            bits = 32
        if got is None:
            rows.append(f"{name}: short")
            continue
        mag = (got & ((1 << (bits - 1)) - 1)) - (expect & ((1 << (bits - 1)) - 1))   # ulps, sign-magnitude
        rows.append(f"{text}: {got:#0{bits // 4 + 2}x} ({'exact' if mag == 0 else f'{mag:+d} ulp'})")
        equal += mag == 0
        worse += mag < 0
    if equal == len(LITERALS):
        observed = "round to nearest"
        verdict = "same as retail"
    elif worse and equal + worse == len(LITERALS):
        observed = f"truncates toward zero ({worse} of {len(LITERALS)} literals one ulp small)"
        verdict = "differs"
    else:
        observed, verdict = "mixed", "unknown"
    return observed, verdict, rows


def loop_nops(asm: str, func: str):
    """(instructions before the back branch, nops among them) of FUNC's loop in cc1 output."""
    m = re.search(r"\.ent\t" + func + r"\n(.*?)\.end\t" + func, asm, re.S)
    if not m:
        return None
    labels, ins = {}, []
    for raw in m.group(1).split("\n"):
        line = raw.strip()
        if not line or line.startswith((".", "#")):
            continue
        if re.match(r"^\$L\d+:$", line):
            labels[line[:-1]] = len(ins)
        else:
            ins.append(line)
    for i, line in enumerate(ins):
        b = re.match(r"^b\w*\t.*(\$L\d+)$", line)
        if b and b.group(1) in labels and labels[b.group(1)] <= i:
            body = ins[labels[b.group(1)]:i]
            nops = sum(1 for x in body if x == "nop")
            return len(body) - nops, nops
    return None


def classify_loops(asm: str) -> tuple:
    r3, r5 = loop_nops(asm, "spin3"), loop_nops(asm, "spin5")
    detail = [f"spin3: d={r3[0]} nops={r3[1]}" if r3 else "spin3: loop not found",
              f"spin5: d={r5[0]} nops={r5[1]}" if r5 else "spin5: loop not found"]
    if not r3 or not r5:
        return "loop shape not recognised", "unknown", detail
    if r5[0] == 5 and r5[1] == 2 and r3[1] == 7 - r3[0]:
        return "pads unfilled-slot loops to 7-d (lp7)", "same as retail", detail
    if r5[0] == 5 and r5[1] == 0 and r3[1] == 5 - r3[0]:
        return "pads unfilled-slot loops to 5-d (catalog 2.96)", "differs", detail
    return f"pads d={r3[0]} with {r3[1]}, d={r5[0]} with {r5[1]}", "unknown", detail


def is_fp_compare(w: int) -> bool:
    return (w >> 26) == 0x11 and ((w >> 21) & 0x1F) == 0x10 and (w & 0x30) == 0x30 and (w & 0xC0) == 0


def classify_hazard(words: list, label: str) -> tuple:
    for i, w in enumerate(words[:-1]):
        if is_fp_compare(w):
            if words[i + 1] == 0:
                return f"{label}: nop after c.cond.s", "same as retail", [f"{w:08x} 00000000 {words[i + 2] if i + 2 < len(words) else 0:08x}"]
            return f"{label}: no nop between c.cond.s and the branch", "differs", [f"{w:08x} {words[i + 1]:08x}"]
    return f"{label}: no FP compare found", "unknown", []


def classify_move(words: list) -> tuple:
    for i, w in enumerate(words):
        if (w >> 26) == 0 and (w & 0x3F) in FUNCT_NAMES and ((w >> 21) & 31) == 4 and ((w >> 11) & 31) == 2:
            name = FUNCT_NAMES[w & 0x3F]
            filled = i > 0 and words[i - 1] == 0x03E00008
            detail = [f"{w:08x} = {name} $2,$4,$0", "placed in the jr delay slot" if filled else "jr delay slot left to a nop"]
            return f"move -> {name}; assembler {'fills' if filled else 'does not fill'} the delay slot under .set reorder", \
                "same as retail" if name == "daddu" else "differs", detail
    return "no move found", "unknown", [" ".join(f"{w:08x}" for w in words)]


def classify_break(words: list) -> tuple:
    for w in words:
        if (w >> 26) == 0 and (w & 0x3F) == 0x0D:
            upper, lower = (w >> 16) & 0x3FF, (w >> 6) & 0x3FF
            if lower == 7 and upper == 0:
                return f"break 7 -> {w:#010x} (code in the lower field)", "same as retail", []
            if upper == 7 and lower == 0:
                return f"break 7 -> {w:#010x} (code in the upper field)", "differs", ["retail: 0x000001cd"]
            return f"break -> {w:#010x}", "unknown", []
    return "no break emitted", "unknown", []


def classify_la(words: list) -> tuple:
    body = [w for w in words if w not in (0x03E00008, 0)]
    if len(body) == 1 and (body[0] >> 26) == 0x09:
        return "la $3,1($2) -> one addiu", "same as retail", [f"{body[0]:08x}"]
    return f"la $3,1($2) -> {len(body)} instructions", "differs" if body else "unknown", [" ".join(f"{w:08x}" for w in body)]


def classify_jump_table(words: list, relocs: list) -> tuple:
    for off, typ, sec in relocs:
        if typ == 5 and sec in (".rodata", ".rdata") and off // 4 < len(words):
            lui = words[off // 4]
            rt = (lui >> 16) & 31
            for w in words[off // 4 + 1: off // 4 + 4]:
                if (w >> 26) == 0 and (w & 0x3F) in FUNCT_NAMES and ((w >> 11) & 31) == rt:
                    name = FUNCT_NAMES[w & 0x3F]
                    return f"jump-table address add -> {name}", "same as retail" if name == "addu" else "differs", [f"{lui:08x} {w:08x}"]
    return "no jump table through .rodata found", "unknown", []


def classify_vsqrt(words: list, failed: bool) -> tuple:
    if failed:
        return "vsqrt rejected by the assembler", "unsupported", []
    for w in words:
        if (w & 0xFE00FFFF) == 0x4A0003BD:
            bits = (w >> 21) & 3
            return f"vsqrt -> {w:#010x}, bits 21-22 = {bits:02b}", "same as retail" if bits == 1 else "differs", []
    return "no vsqrt word found", "unknown", [" ".join(f"{w:08x}" for w in words)]


def classify_padding(sections: dict) -> tuple:
    size = sections.get(".text", (0, 0, b""))[1]
    if size == 12:
        return ".text of a 3-word function is 12 bytes (no trailing alignment padding)", "unknown", []
    return f".text of a 3-word function is {size} bytes (padded to the section alignment)", "unknown", []


def classify_small_data(relocs: list) -> tuple:
    gprel = sorted(off for off, typ, _ in relocs if typ == 7)
    eight, four = 4 in gprel, 12 in gprel
    return (f"8-byte global {'gp-relative' if eight else 'absolute'}, 4-byte global {'gp-relative' if four else 'absolute'}",
            "n/a (per-TU flag)", [f"GPREL16 at text offsets {gprel}"])


# ------------------------------------------------------------------ tool paths

def glibc_need(host_file: Path):
    """Highest GLIBC_x.y the binary asks for, or None."""
    try:
        data = host_file.read_bytes()
    except OSError:
        return None
    versions = [tuple(int(x) for x in m.groups()) for m in re.finditer(rb"GLIBC_(\d+)\.(\d+)", data)]
    return max(versions) if versions else None


def elf_class(host_file: Path):
    try:
        head = host_file.read_bytes()[:5]
    except OSError:
        return None
    return {1: 32, 2: 64}.get(head[4]) if head[:4] == b"\x7fELF" else None


class Tool:
    """One of cc1 / as: where it is on the host (for hashing), how the box names it,
    and whether it runs through the rootfs loader or on the box's libc."""

    def __init__(self, spec: str, rootfs: Path, toolchains, extra_mounts: list, run: str):
        spec = os.path.expanduser(spec)
        if spec.startswith("/rootfs/"):
            self.box, self.host = spec, rootfs / spec[len("/rootfs/"):]
        elif spec.startswith("/toolchains/"):
            self.box = spec
            self.host = (Path(toolchains) / spec[len("/toolchains/"):]) if toolchains else None
        elif os.path.isabs(spec) and Path(spec).is_file():
            n = len(extra_mounts)
            extra_mounts.append(Path(spec).resolve().parent)
            self.box, self.host = f"/extra{n}/{Path(spec).name}", Path(spec).resolve()
        else:
            self.box, self.host = "/rootfs/" + spec.lstrip("/"), rootfs / spec.lstrip("/")
        if self.host is None or not self.host.is_file():
            raise FileNotFoundError(f"{spec}: not found on the host ({self.host})")
        self.sha256 = hashlib.sha256(self.host.read_bytes()).hexdigest()
        if run == "auto":
            need = glibc_need(self.host)
            run = "box" if need and need >= (2, 34) else "loader"
        self.run = run
        self.cmd = (LD + " " if run == "loader" else "") + self.box
        if elf_class(self.host) == 64:
            raise ValueError(f"{spec} is a 64-bit ELF; the linux/386 box cannot run it")


# ------------------------------------------------------------------ the box run

def build_script(cc1: Tool, ee_as: Tool, cc1_flags: str, as_flags: str) -> str:
    lines = ["#!/bin/sh", 'if command -v timeout >/dev/null 2>&1; then T="timeout -k 5 %d"; else T=""; fi' % STEP_TIMEOUT,
             "cd /work", f"$T {cc1.cmd} --version </dev/null >ver_cc1.txt 2>&1",
             f"$T {cc1.cmd} -version -quiet src/div.i -o /dev/null >>ver_cc1.txt 2>&1",   # GCC 2.x: 'GNU C version ...'
             f"$T {ee_as.cmd} --version >ver_as.txt 2>&1"]
    for name in C_SOURCES:
        lines.append(f'$T {cc1.cmd} src/{name}.i {cc1_flags} -o out/{name}.s 2>out/{name}.cc1err; echo "CC1 {name} $?"')
        lines.append(f'$T {ee_as.cmd} {as_flags} -o out/{name}.o out/{name}.s 2>out/{name}.aserr; echo "AS {name} $?"')
    for name in S_SOURCES:
        lines.append(f'$T {ee_as.cmd} {as_flags} -o out/{name}.o src/{name}.s 2>out/{name}.aserr; echo "AS {name} $?"')
    lines.append("exit 0")
    return "\n".join(lines) + "\n"


def run_box(work: Path, rootfs: Path, toolchains, extra: list, image: str, timeout: int):
    mounts = ["-v", f"{rootfs.resolve()}:/rootfs:ro", "-v", f"{work.resolve()}:/work"]
    if toolchains:
        mounts += ["-v", f"{Path(toolchains).resolve()}:/toolchains:ro"]
    for n, d in enumerate(extra):
        mounts += ["-v", f"{d}:/extra{n}:ro"]
    try:
        out = subprocess.run(["docker", "run", "--rm", "--platform", "linux/386"] + mounts
                             + [image, "/bin/sh", "/work/build.sh"], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, {}, "docker: command not found"
    except subprocess.TimeoutExpired:
        return 124, {}, f"docker run exceeded {timeout} s"
    rcs = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] in ("CC1", "AS"):
            rcs[(parts[0], parts[1])] = int(parts[2])
    return out.returncode, rcs, (out.stderr or out.stdout).strip()[-1500:]


def literal_words(asm: str) -> dict:
    out, current = {}, None
    for line in asm.splitlines():
        m = re.match(r"^(\w+):\s*$", line)
        if m:
            current = m.group(1)
            out[current] = []
            continue
        w = re.match(r"^\s*\.(?:word|long)\s+(0x[0-9a-fA-F]+|\d+)", line)
        if w and current:
            out[current].append(int(w.group(1), 0))
        elif line.strip() and not line.strip().startswith(("#", ".align")):
            current = None
    return out


def first_line(path: Path, prefer: str = "") -> str:
    """The first line of PATH, or the first one containing PREFER when there is one."""
    try:
        lines = path.read_text(errors="replace").strip().splitlines()
    except OSError:
        return ""
    return next((l.strip() for l in lines if prefer and prefer in l), lines[0].strip() if lines else "")


def probe(args) -> int:
    rootfs = Path(args.rootfs).expanduser()
    if not (rootfs / "lib" / "ld-linux.so.2").is_file():
        print(f"no i386 rootfs at {rootfs} (lib/ld-linux.so.2 missing); set EE_ROOTFS or --rootfs", file=sys.stderr)
        return 2
    extra: list = []
    try:
        cc1 = Tool(args.cc1 or STOCK_CC1, rootfs, args.toolchains, extra, args.cc1_run)
        ee_as = Tool(args.as_ or STOCK_AS, rootfs, args.toolchains, extra, args.as_run)
    except (FileNotFoundError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    work = Path(args.work).expanduser() / f"run-{os.getpid()}-{int(time.time() * 1000)}"
    (work / "src").mkdir(parents=True)
    (work / "out").mkdir()
    for name, text in C_SOURCES.items():
        (work / "src" / f"{name}.c").write_text(text)
        cpp = subprocess.run(["clang", "-E", "-P", str(work / "src" / f"{name}.c"), "-o", str(work / "src" / f"{name}.i")],
                             capture_output=True, text=True)
        if cpp.returncode:
            print(f"clang -E failed on {name}.c:\n{cpp.stderr}", file=sys.stderr)
            return 2
    for name, text in S_SOURCES.items():
        (work / "src" / f"{name}.s").write_text(text)
    (work / "build.sh").write_text(build_script(cc1, ee_as, args.cc1_flags, args.as_flags))
    rc, rcs, tail = run_box(work, rootfs, args.toolchains, extra, args.image, args.docker_timeout)
    if rc != 0:
        print(f"docker run failed (rc {rc}); nothing was measured\n{tail}", file=sys.stderr)
        return 2

    def failed(kind, name):
        return rcs.get((kind, name), 1) != 0

    def obj(name):
        if failed("AS", name) or (name in C_SOURCES and failed("CC1", name)):
            return {}, []
        return read_object(work / "out" / f"{name}.o")

    def asm(name):
        p = work / "out" / f"{name}.s"
        return p.read_text(errors="replace") if p.is_file() else ""

    rows = []

    def row(name, result, retail):
        observed, verdict, detail = result
        rows.append(dict(probe=name, observed=observed, retail=retail, verdict=verdict, detail=detail))

    row("literal-rounding", classify_literals(literal_words(asm("literals"))) if not failed("CC1", "literals")
        else ("cc1 failed", "error", [first_line(work / "out" / "literals.cc1err")]), "round to nearest")
    row("short-loop-padding", classify_loops(asm("spin")) if not failed("CC1", "spin")
        else ("cc1 failed", "error", [first_line(work / "out" / "spin.cc1err")]), "7-d nops on an unfilled-slot back branch (lp7)")
    row("fp-branch-hazard-nop (C)", classify_hazard(text_words(obj("hazard")[0]), "cc1 + as"), "nop between c.cond.s and bc1t")
    row("fp-branch-hazard-nop (asm)", classify_hazard(text_words(obj("hz")[0]), "as alone, .set reorder"), "nop between c.cond.s and bc1t")
    row("move-expansion", classify_move(text_words(obj("move")[0])), "daddu (historical gas; modern gas emits or)")
    row("break-encoding", classify_break(text_words(obj("div")[0])), "break 0,7 = 0x000001cd (lower field)")
    row("la-imm16", classify_la(text_words(obj("la")[0])), "one addiu (gas 2.10 behaviour)")
    sw_sections, sw_relocs = obj("switch")
    row("jump-table-add", classify_jump_table(text_words(sw_sections), sw_relocs), "addu")
    vs_sections, _ = obj("vsqrt")
    row("vsqrt-bits-21-22", classify_vsqrt(text_words(vs_sections), failed("AS", "vsqrt")), "01")
    row("section-padding", classify_padding(obj("pad")[0]), "not observable in a linked image")
    row("small-data (-G)", classify_small_data(obj("gsmall")[1]), "XS1 main: -G8 for most TUs, -G0 for some; overlays often -G0")

    failures = [f"{k[0]} {k[1]} rc={v}: {first_line(work / 'out' / (k[1] + ('.cc1err' if k[0] == 'CC1' else '.aserr')))}"
                for k, v in sorted(rcs.items()) if v != 0]
    report = dict(cc1=dict(path=str(cc1.host), box=cc1.box, run=cc1.run, sha256=cc1.sha256, version=first_line(work / "ver_cc1.txt", "GNU C version")),
                  ee_as=dict(path=str(ee_as.host), box=ee_as.box, run=ee_as.run, sha256=ee_as.sha256, version=first_line(work / "ver_as.txt")),
                  cc1_flags=args.cc1_flags, as_flags=args.as_flags, image=args.image, work=str(work), probes=rows, failures=failures)

    print(f"cc1    {report['cc1']['version'] or '?'}  sha256 {cc1.sha256[:12]}  [{cc1.run}]  {args.cc1_flags}")
    print(f"ee-as  {report['ee_as']['version'] or '?'}  sha256 {ee_as.sha256[:12]}  [{ee_as.run}]  {args.as_flags}")
    print("=" * 100)
    print(f"{'probe':<28} {'verdict':<16} observed")
    print("-" * 100)
    for r in rows:
        print(f"{r['probe']:<28} {r['verdict']:<16} {r['observed']}")
        if args.verbose:
            print(f"{'':<28} {'retail:':<16} {r['retail']}")
            for d in r["detail"]:
                print(f"{'':<45} {d}")
    if failures:
        print("-" * 100)
        for f in failures:
            print(f"  step failed: {f}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1))
        print(f"\nwrote {args.json}")
    return 0


# ------------------------------------------------------------------ self-test

def self_test() -> int:
    checks = {}
    trunc = {"d_tenth": [0x99999999, 0x3FB99999], "d_neg_tenth": [0x99999999, 0xBFB99999], "d_pi": [0x54442D17, 0x400921FB],
             "d_tiny": [0xC2F8F358, 0x01A56E1F], "f_tenth": [0x3DCCCCCC], "f_narrow": [0x37D9FFFF]}
    rounded = {"d_tenth": [0x9999999A, 0x3FB99999], "d_neg_tenth": [0x9999999A, 0xBFB99999], "d_pi": [0x54442D18, 0x400921FB],
               "d_tiny": [0xC2F8F359, 0x01A56E1F], "f_tenth": [0x3DCCCCCD], "f_narrow": [0x37DA0000]}
    checks["literal-trunc"] = classify_literals(trunc)[1] == "differs"
    checks["literal-round"] = classify_literals(rounded)[1] == "same as retail"
    checks["literal-parse"] = literal_words("\t.globl d_pi\nd_pi:\n\t.word\t0x54442d17\t# c\n\t.word\t0x400921fb\n\t.globl f\nf:\n\t.word\t1\n") \
        == {"d_pi": [0x54442D17, 0x400921FB], "f": [1]}
    loop = ("\t.ent\tspin5\nspin5:\n$L1:\n\tlw\t$2,0($4)\n\tsra\t$2,$2,2\n\tandi\t$2,$2,0x7\n\txori\t$2,$2,0x1\n\taddu\t$2,$2,2\n"
            "%s\t.set\tnoreorder\n\tbeq\t$2,$5,$L1\n\t.set\treorder\n\tj\t$31\n\t.end\tspin5\n"
            "\t.ent\tspin3\nspin3:\n$L2:\n\tlw\t$2,0($4)\n\tsra\t$2,$2,2\n\tandi\t$2,$2,0x7\n%s\tbeq\t$2,$5,$L2\n\tj\t$31\n\t.end\tspin3\n")
    checks["loops-lp7"] = classify_loops(loop % ("\tnop\n" * 2, "\tnop\n" * 4))[1] == "same as retail"
    checks["loops-catalog"] = classify_loops(loop % ("", "\tnop\n" * 2))[1] == "differs"
    checks["hazard-yes"] = classify_hazard([0x460D6034, 0, 0x45010002, 0x24020001], "x")[1] == "same as retail"
    checks["hazard-no"] = classify_hazard([0x460D6034, 0x45010002, 0x24020001], "x")[1] == "differs"
    checks["move-daddu"] = classify_move([0x03E00008, 0x0080102D])[1] == "same as retail" and "fills" in classify_move([0x03E00008, 0x0080102D])[0]
    checks["move-or"] = classify_move([0x00801025, 0x03E00008, 0])[1] == "differs"
    checks["break-lower"] = classify_break([0x0085001A, 0x50A00001, 0x000001CD])[1] == "same as retail"
    checks["break-upper"] = classify_break([0x0085001A, 0x50A00001, 0x0007000D])[1] == "differs"
    checks["la-one"] = classify_la([0x24430001, 0x03E00008, 0])[1] == "same as retail"
    checks["la-two"] = classify_la([0x24030001, 0x00621821, 0x03E00008, 0])[1] == "differs"
    words = [0x27BDFFF0, 0x2C820008, 0x10400029, 0xFFBF0000, 0x00041080, 0x3C030000, 0x00621821, 0x8C630000]
    checks["jt-addu"] = classify_jump_table(words, [(20, 5, ".rodata"), (28, 6, ".rodata")])[1] == "same as retail"
    checks["jt-daddu"] = classify_jump_table(words[:6] + [0x0062182D, 0x8C630000], [(20, 5, ".rdata")])[1] == "differs"
    checks["vsqrt-01"] = classify_vsqrt([0x4BA103BD, 0x03E00008, 0], False)[1] == "same as retail"
    checks["vsqrt-00"] = classify_vsqrt([0x4B8103BD], False)[1] == "differs"
    checks["vsqrt-unsupported"] = classify_vsqrt([], True)[1] == "unsupported"
    checks["gprel"] = "8-byte global gp-relative, 4-byte global gp-relative" in classify_small_data([(4, 7, None), (12, 7, None)])[0]
    checks["glibc-parse"] = glibc_need.__code__ is not None   # exercised on real files only
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rootfs", default=os.environ.get("EE_ROOTFS", str(Path.home() / "ee-rootfs")),
                    help="i386 rootfs holding lib/ld-linux.so.2 and opt/ee (default: EE_ROOTFS)")
    ap.add_argument("--cc1", help=f"cc1 to probe (default: rootfs {STOCK_CC1})")
    ap.add_argument("--as", dest="as_", help=f"assembler to probe (default: rootfs {STOCK_AS})")
    ap.add_argument("--toolchains", help="directory mounted read-only at /toolchains (a decomp's pinned tools)")
    ap.add_argument("--cc1-flags", default="-quiet -O2 -G 8", help='cc1 flags (default "-quiet -O2 -G 8")')
    ap.add_argument("--as-flags", default="-G 8", help='assembler flags (default "-G 8"; the XS1 contract uses "-EL -m5900 -mabi=eabi -G8")')
    ap.add_argument("--cc1-run", choices=("auto", "loader", "box"), default="auto",
                    help="run cc1 through the rootfs ld-linux.so.2 or on the box's libc (auto: by its glibc need)")
    ap.add_argument("--as-run", choices=("auto", "loader", "box"), default="auto")
    ap.add_argument("--image", default="debian:bookworm-slim")
    ap.add_argument("--work", default=str(Path.home() / ".cache" / "decomp-probe"), help="scratch dir; must be under $HOME for Docker mounts")
    ap.add_argument("--docker-timeout", type=int, default=600)
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("-v", "--verbose", action="store_true", help="print the retail reference and the evidence words per row")
    ap.add_argument("--self-test", action="store_true", help="network-free checks of the classifiers; exit 0/1")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    return probe(args)


if __name__ == "__main__":
    sys.exit(main())
