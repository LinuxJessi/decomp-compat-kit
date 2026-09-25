#!/usr/bin/env python3
"""Independently byte-verify a matching-decomp checkout against the retail SLUS.

Compiles each C translation unit of an upstream decomp tree with a *stock,
unpatched* ee-gcc 2.96 + ee-as, resolves relocations from our own symbol table,
and compares every function it defines against the original bytes.

Why this exists: a decomp's own whole-file gate proves its source reproduces
the image under *its* toolchain, which may be patched. This checks the same
claim from the outside, with an independently obtained compiler and an
independently derived symbol table, one function at a time. It also classifies
every mismatch, which turns "40 functions differ" into a statement about which
toolchain difference is responsible.

Mismatch classes reported:
  hazard-nop   the original has extra nop(s); ours matches otherwise.  A
               patched assembler inserts R5900 hazard nops (notably between an
               FP compare and a branch on its condition) that stock ee-as omits.
  reorder      identical instruction multiset, different order -- an
               alias-analysis difference in cc1 lets memory references move.
  mixed        both of the above in one function.
  immediate    same opcodes, different immediate -- suspect decimal-literal
               rounding (a "realconv"-class cc1 difference).
  unexplained  anything else.  These are the only ones worth reading.

Known limitation: the "mixed" test masks branch and jump target fields, because
inserting one nop shifts every later instruction and so changes every branch
that spans it. That masking is what makes nop insertion legible, but it means a
genuinely wrong call target inside an otherwise-correct function would be
classified "mixed" rather than flagged. Treat "mixed" as "placement differs",
not as "verified equivalent", and read the instructions when it matters.

Usage:
  ./verify_decomp.py --upstream ~/src/some-decomp --unit main --out report.json
  ./verify_decomp.py --upstream ~/src/some-decomp --tu main/tu086 -v

Requires: the upstream checkout, our out/symbols.csv and out/browse/code/, and
an i386 ee-gcc 2.96 reachable through Docker (see README.md for
the macOS/arm64 recipe).
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

# Where your own extracted originals and symbol table live. Set DECOMP_GAME_DIR,
# or pass --symbols/--binary explicitly. Nothing game-derived ships with this kit.
GAME = Path(os.environ.get("DECOMP_GAME_DIR", "."))

# SLUS_204.69 .text placement (out/TOOLCHAIN.md)
TEXT_VA, TEXT_OFF = 0x200000, 0x1000

R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16, R_MIPS_GPREL16 = 4, 5, 6, 7
STT_FUNC, STT_SECTION = 2, 3
NOP = b"\x00\x00\x00\x00"
E = "<"


# ---------------------------------------------------------------- symbol table

class Symbols:
    """Our own ELF symbol table, from out/symbols.csv."""

    def __init__(self, csv_path: Path, binary: str = "SLUS_204.69"):
        self.exact: dict[str, tuple[int, int]] = {}
        self.stripped: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
        with open(csv_path) as fh:
            for r in csv.DictReader(fh):
                if r["binary"] != binary:
                    continue
                va, size = int(r["va"], 16), int(r["size"])
                self.exact.setdefault(r["name"], (va, size))
                # GCC 2.x suffixes file-local statics: sSemaParam -> sSemaParam.0
                base = re.sub(r"\.\d+$", "", r["name"])
                if base != r["name"]:
                    self.stripped[base].append((va, size))
        self.gp = self.exact["_gp"][0]

    def value(self, name: str) -> int:
        if name in self.exact:
            return self.exact[name][0]
        cands = self.stripped.get(name, [])
        if len(cands) == 1:
            return cands[0][0]
        if cands:
            raise KeyError(f"{name} (ambiguous: {len(cands)} local candidates)")
        raise KeyError(name)

    def size(self, name: str) -> int:
        return self.exact.get(name, (0, 0))[1]


# ------------------------------------------------------------------ ELF reader

def read_elf(path: Path):
    d = path.read_bytes()
    if d[:4] != b"\x7fELF":
        raise ValueError(f"{path}: not an ELF")
    shoff, = struct.unpack_from(E + "I", d, 0x20)
    shent, shnum, shstrndx = struct.unpack_from(E + "HHH", d, 0x2E)
    keys = ("nameoff", "typ", "flags", "addr", "off", "size", "link", "info", "align", "entsz")
    secs = [dict(zip(keys, struct.unpack_from(E + "10I", d, shoff + i * shent)))
            for i in range(shnum)]

    def name_at(off, tab):
        b = tab + off
        return d[b:d.index(b"\0", b)].decode()

    for s in secs:
        s["name"] = name_at(s["nameoff"], secs[shstrndx]["off"])
    return d, secs


# ----------------------------------------------------------------- relocation

class Object:
    """One assembled .o, relocated against real addresses."""

    def __init__(self, path: Path, syms: Symbols):
        self.syms = syms
        d, secs = read_elf(path)
        self.text_idx = next(i for i, s in enumerate(secs) if s["name"] == ".text")
        text = secs[self.text_idx]
        self.text = bytearray(d[text["off"]:text["off"] + text["size"]])

        st = next(s for s in secs if s["name"] == ".symtab")
        strt = secs[st["link"]]

        def name_at(off):
            b = strt["off"] + off
            return d[b:d.index(b"\0", b)].decode()

        self.symbols = []
        for k in range(st["size"] // 16):
            no, val, size, info, other, shndx = struct.unpack_from(
                E + "IIIBBH", d, st["off"] + k * 16)
            self.symbols.append(dict(name=name_at(no), val=val, size=size,
                                     typ=info & 0xF, shndx=shndx))

        self.funcs, self.unknown = [], []
        for s in self.symbols:
            if s["typ"] != STT_FUNC or s["shndx"] != self.text_idx:
                continue
            try:
                va = syms.value(s["name"])
            except KeyError as e:
                self.unknown.append((s["name"], str(e)))
                continue
            size = syms.size(s["name"])
            if size:
                self.funcs.append(dict(name=s["name"], off=s["val"], va=va, size=size))
        self.funcs.sort(key=lambda f: f["off"])

        rel = next((s for s in secs if s["name"] == ".rel.text"), None)
        self.relocs = ([struct.unpack_from(E + "II", d, rel["off"] + k * 8)
                        for k in range(rel["size"] // 8)] if rel else [])
        self.sections = secs
        self.unresolved: list[tuple[int, str]] = []
        self._relocate()

    def _off_to_va(self, off: int):
        for f in self.funcs:
            if f["off"] <= off < f["off"] + f["size"]:
                return f["va"] + (off - f["off"])
        return None

    def _word(self, off):
        return struct.unpack_from(E + "I", self.text, off)[0]

    def _put(self, off, val):
        struct.pack_into(E + "I", self.text, off, val & 0xFFFFFFFF)

    def _relocate(self):
        for i, (off, info) in enumerate(self.relocs):
            rtype, si = info & 0xFF, info >> 8
            sym = self.symbols[si]
            w = self._word(off)

            if sym["typ"] == STT_SECTION:
                sec = self.sections[sym["shndx"]]["name"]
                if sec != ".text" or rtype != R_MIPS_26:
                    self.unresolved.append((off, f"{sec} reloc type {rtype}"))
                    continue
                target = (w & 0x03FFFFFF) << 2
                va = self._off_to_va(target)
                if va is None:
                    self.unresolved.append((off, f"text offset {target:#x} outside known funcs"))
                    continue
                self._put(off, (w & 0xFC000000) | ((va >> 2) & 0x03FFFFFF))
                continue

            try:
                value = self.syms.value(sym["name"])
            except KeyError as e:
                self.unresolved.append((off, str(e)))
                continue

            if rtype == R_MIPS_26:
                addend = (w & 0x03FFFFFF) << 2
                self._put(off, (w & 0xFC000000) | (((value + addend) >> 2) & 0x03FFFFFF))
            elif rtype == R_MIPS_HI16:
                # REL format: the addend is split across this HI16 and the LO16
                # that follows it, and %hi must carry the LO16's sign.
                ahi = (w & 0xFFFF) << 16
                noff, ninfo = self.relocs[i + 1]
                if (ninfo & 0xFF) != R_MIPS_LO16:
                    self.unresolved.append((off, "HI16 without paired LO16"))
                    continue
                lo = self._word(noff) & 0xFFFF
                alo = lo - 0x10000 if lo & 0x8000 else lo
                v = value + ahi + alo
                low = v & 0xFFFF
                low = low - 0x10000 if low & 0x8000 else low
                self._put(off, (w & 0xFFFF0000) | (((v - low) >> 16) & 0xFFFF))
            elif rtype in (R_MIPS_LO16, R_MIPS_GPREL16):
                a = w & 0xFFFF
                a = a - 0x10000 if a & 0x8000 else a
                base = self.syms.gp if rtype == R_MIPS_GPREL16 else 0
                self._put(off, (w & 0xFFFF0000) | ((value + a - base) & 0xFFFF))
            else:
                self.unresolved.append((off, f"reloc type {rtype}"))

    def bytes_for(self, f) -> bytes:
        return bytes(self.text[f["off"]:f["off"] + f["size"]])

    def is_clean(self, f) -> bool:
        lo, hi = f["off"], f["off"] + f["size"]
        return not any(lo <= o < hi for o, _ in self.unresolved)


# ------------------------------------------------------------- classification

def words(b: bytes) -> list[bytes]:
    return [b[i:i + 4] for i in range(0, len(b) - 3, 4)]


def mask_branch(w: bytes) -> bytes:
    """Zero the target field of a branch or jump.

    Inserting a nop shifts every later instruction, so each branch that spans
    the insertion point gets a different offset even though the instruction is
    the same one. Masking the target lets nop insertion and reordering be
    recognised together instead of looking like unrelated corruption.
    """
    v, = struct.unpack(E + "I", w)
    op = v >> 26
    if op in (0x02, 0x03):                                  # j, jal
        v &= ~0x03FFFFFF
    elif op in (0x01, 0x04, 0x05, 0x06, 0x07,               # regimm, beq..bgtz
                0x14, 0x15, 0x16, 0x17):                    # beql..bgtzl
        v &= ~0xFFFF
    elif op == 0x11 and ((v >> 21) & 0x1F) == 0x08:          # bc1t/bc1f family
        v &= ~0xFFFF
    return struct.pack(E + "I", v)


def classify(ours: bytes, orig: bytes) -> tuple[str, str]:
    """Explain a mismatch in terms of a toolchain difference, if it is one."""
    a, b = words(ours), words(orig)
    if a == b:
        return "match", ""

    # The original is `ours` with nops inserted, then truncated to the same
    # length -- walk both and let the original spend extra slots on nops.
    i = j = ins = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        elif b[j] == NOP:
            j += 1
            ins += 1
        else:
            break
    if j == len(b) and ins:
        return "hazard-nop", f"{ins} nop(s) inserted"

    if collections.Counter(a) == collections.Counter(b):
        return "reorder", "same instruction multiset"

    # Mixed: nop insertion *and* reordering in one function. Drop the nops and
    # mask branch targets (which shift as a consequence of the insertion); if
    # every instruction the original uses is one we also emit, the only
    # differences are placement, not content. The original spends slots on nops
    # so our tail runs past the window -- hence subset, not equality.
    def body(ws):
        return collections.Counter(mask_branch(w) for w in ws if w != NOP)

    if not (body(b) - body(a)):
        n_nop = sum(1 for w in b if w == NOP) - sum(1 for w in a if w == NOP)
        return "mixed", f"nop insertion + reordering ({n_nop:+d} nop)"

    same_len = min(len(a), len(b))
    ndiff = sum(1 for x, y in zip(a, b) if x != y)
    if ndiff == 1:
        return "immediate", "single differing word"
    return "unexplained", f"{ndiff}/{same_len} words differ"


# -------------------------------------------------------------------- driver

def docker_build(work: Path, rootfs: Path, jobs: list[dict], image: str) -> list[str]:
    """Run stock cc1 + ee-as over every preprocessed TU inside a linux/386 box."""
    script = work / "build.sh"
    script.write_text(
        '#!/bin/sh\n'
        'LD="/rootfs/lib/ld-linux.so.2 --library-path /rootfs/lib/i386-linux-gnu"\n'
        'CC1=/rootfs/opt/ee/lib/gcc-lib/ee/2.96-ee-001003-1/cc1\n'
        'AS=/rootfs/opt/ee/bin/ee-as\n'
        'cd /work\n'
        'for i in pp/*.i; do\n'
        '  tu=$(basename "$i" .i)\n'
        '  G=$(cat flags/$tu 2>/dev/null); [ -z "$G" ] && G=-G8\n'
        '  $LD $CC1 "$i" -quiet -O2 $G -o "asm/$tu.s" 2>"asm/$tu.cc1err" \\\n'
        '    || { echo "CC1FAIL $tu"; continue; }\n'
        '  $LD $AS $G -o "obj/$tu.o" "asm/$tu.s" 2>"asm/$tu.aserr" \\\n'
        '    || echo "ASFAIL $tu"\n'
        'done\n')
    out = subprocess.run(
        ["docker", "run", "--rm", "--platform", "linux/386",
         "-v", f"{rootfs}:/rootfs:ro", "-v", f"{work}:/work",
         image, "/bin/sh", "/work/build.sh"],
        capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l.startswith(("CC1FAIL", "ASFAIL"))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upstream", required=True, type=Path,
                    help="path to the upstream decomp checkout")
    ap.add_argument("--unit", default="main", help="unit to verify (default: main)")
    ap.add_argument("--tu", action="append", help="verify only this TU id (repeatable)")
    ap.add_argument("--symbols", type=Path, default=GAME / "symbols.csv",
                    help="symbol table CSV (see tools/dump_symbols.py)")
    ap.add_argument("--binary", type=Path, default=GAME / "SLUS_204.69",
                    help="the original ELF to compare against")
    ap.add_argument("--rootfs", type=Path,
                    default=Path(os.environ.get("EE_ROOTFS", Path.home() / "ee-rootfs")),
                    help="i386 rootfs holding opt/ee (stock ee-gcc 2.96)")
    ap.add_argument("--work", type=Path, default=Path.home() / ".cache/decomp-verify",
                    help="scratch dir; must be under $HOME for Docker mounts")
    ap.add_argument("--image", default="debian:bookworm-slim")
    ap.add_argument("--out", type=Path, help="write a JSON report here")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    up = args.upstream
    manifest = up / f"config/objects/{args.unit}.compile.json"
    if not manifest.exists():
        print(f"no compile manifest at {manifest}", file=sys.stderr)
        return 2

    syms = Symbols(args.symbols)
    slus = args.binary.read_bytes()

    work = args.work
    for sub in ("pp", "asm", "obj", "flags"):
        (work / sub).mkdir(parents=True, exist_ok=True)

    tus = json.loads(manifest.read_text())["tus"]
    jobs, skipped = [], []
    for key, v in tus.items():
        if v.get("mode") != "c" or not v.get("in_scope"):
            continue
        if not str(v.get("compiler_profile", "")).startswith("ee-gcc2_96"):
            continue
        if args.tu and v["id"] not in args.tu:
            continue
        src = up / v["path"]
        if not src.exists():
            continue
        tag = v["id"].replace("/", "__")
        # SKIP_ASM drops the INCLUDE_ASM/ACCEPTED_ASM stubs, so a partly
        # migrated TU still yields exactly its C functions.
        cpp = subprocess.run(
            ["clang", "-E", "-P", "-DSKIP_ASM",
             "-I", str(up / "include"), "-I", str(up / f"src/{args.unit}"), "-I", str(up),
             str(src), "-o", str(work / "pp" / f"{tag}.i")],
            capture_output=True, text=True)
        if cpp.returncode:
            skipped.append((v["id"], "preprocess", cpp.stderr.strip().splitlines()[:1]))
            continue
        gflag = next((f for f in v.get("flags", []) if f.startswith("-G")), "-G8")
        (work / "flags" / tag).write_text(gflag)
        jobs.append(dict(tu=v["id"], tag=tag, path=v["path"], flags=v.get("flags", [])))

    print(f"preprocessed {len(jobs)} TU(s); {len(skipped)} skipped")
    build_fails = docker_build(work, args.rootfs, jobs, args.image)
    failed = {l.split()[1] for l in build_fails}
    for line in build_fails:
        kind, tag = line.split()
        err = (work / "asm" / f"{tag}.{'cc1err' if kind == 'CC1FAIL' else 'aserr'}")
        first = err.read_text().strip().splitlines()[:1] if err.exists() else []
        skipped.append((tag, kind.lower(), first))

    totals = collections.Counter()
    results, per_tu = [], {}
    for job in jobs:
        if job["tag"] in failed:
            per_tu[job["tu"]] = {"build": "fail"}
            totals["build-fail"] += 1
            continue
        obj = work / "obj" / f"{job['tag']}.o"
        if not obj.exists():
            totals["build-fail"] += 1
            continue
        o = Object(obj, syms)
        counts = collections.Counter()
        for f in o.funcs:
            if not o.is_clean(f):
                counts["skip"] += 1
                totals["skip"] += 1
                continue
            ours = o.bytes_for(f)
            off = f["va"] - TEXT_VA + TEXT_OFF
            orig = slus[off:off + f["size"]]
            if len(ours) != f["size"]:
                counts["skip"] += 1
                totals["skip"] += 1
                continue
            kind, detail = classify(ours, orig)
            counts[kind] += 1
            totals[kind] += 1
            if kind != "match":
                results.append(dict(tu=job["tu"], func=f["name"], va=f"{f['va']:#x}",
                                    size=f["size"], kind=kind, detail=detail))
        counts["nosym"] += len(o.unknown)
        totals["nosym"] += len(o.unknown)
        per_tu[job["tu"]] = dict(counts)

    print("=" * 74)
    print("Independent byte verification -- stock ee-gcc 2.96 + stock ee-as")
    print("=" * 74)
    for tu in sorted(per_tu, key=lambda t: -per_tu[t].get("match", 0)):
        c = per_tu[tu]
        if c.get("build") == "fail":
            print(f"  {tu:<14} BUILD FAILED")
            continue
        line = "  ".join(f"{k}={v}" for k, v in sorted(c.items()) if v)
        print(f"  {tu:<14} {line}")
    print("-" * 74)
    n_match = totals["match"]
    n_diff = sum(totals[k] for k in ("hazard-nop", "reorder", "mixed", "immediate", "unexplained"))
    denom = n_match + n_diff
    pct = (100.0 * n_match / denom) if denom else 0.0
    print(f"  identical: {n_match}/{denom}  ({pct:.1f}%)   differing: {n_diff}"
          f"   unverifiable: {totals['skip']}   unknown symbol: {totals['nosym']}"
          f"   build failures: {totals['build-fail']}")
    if n_diff:
        print("\n  mismatches by cause:")
        for k in ("hazard-nop", "reorder", "mixed", "immediate", "unexplained"):
            if totals[k]:
                print(f"    {k:<12} {totals[k]}")
        unex = [r for r in results if r["kind"] == "unexplained"]
        if unex:
            print("\n  UNEXPLAINED (the only ones worth reading):")
            for r in unex:
                print(f"    {r['tu']:<14} {r['func']:<30} {r['detail']}")
    if args.verbose and skipped:
        print("\n  skipped TUs:")
        for tu, why, msg in skipped:
            print(f"    {tu:<14} {why:<11} {' '.join(msg)}")

    if args.out:
        args.out.write_text(json.dumps(
            dict(totals=dict(totals), per_tu=per_tu, mismatches=results,
                 skipped=[dict(tu=t, why=w, msg=m) for t, w, m in skipped]), indent=1))
        print(f"\nwrote {args.out}")
    return 0 if totals["unexplained"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
