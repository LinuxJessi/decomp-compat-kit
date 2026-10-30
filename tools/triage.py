#!/usr/bin/env python3
"""Triage every function of a PS2 EE binary by shape and difficulty, from its bytes.

A matching decomp spends most of its effort deciding *what to try next*. The
bytes of the original already answer the questions that dominate a failure
ledger: how big is the function, does it call out, is there a switch table, does
it touch the FPU or COP2/VU, is a 128-bit `lq`/`sq` copy in it, does it address
small data through `$gp` (so the TU was built with `-G8`) or address an 8-byte
object absolutely (a witness for `-G0`). This tool derives those facts with a
narrow bit-field decoder (no objdump, capstone or rabbitizer), scores each
function and sorts the list easiest first, so a project (or an agent scheduler)
can hand out work by tier.

The scoring and the six classes reproduce the tractability classifier of the
xsg-i-decomp KOS-MOS pack (`tools/function_context.py`, `classify()`); the
metrics are the same ones its `metrics_for_span()` computes. Nothing here is
matching evidence: a class is a lead about effort, never a verdict about code.

  class     meaning
  trivial   <= 8 instructions, leaf, no branch, no FPU: an accessor
  easy      <= 32 instructions, <= 2 callees, no switch table
  medium    <= 128 instructions, no switch table
  hard      everything else (a switch table lands here at any size)
  cop2      COP2/VU macro-mode instructions: plain C cannot reproduce them
  huge      >= 512 instructions or >= 24 distinct callees

Flags carried per function: `quad` (an `lq`/`sq`: a 16-byte copy the stock kit
cannot express as C), `switch`, `fpu`, `tail` (a `j` out of the body),
`nosibcall` (a final `jal` kept its own epilogue), `pool` (a float literal
loaded from memory), `g_hint` (`-G8` when `$gp` is used; `-G0` when an object
of <= 8 bytes is addressed with a `lui`/`%lo` pair, which small-data compilation
would have put on `$gp`; `?` when neither is seen).

Usage:
  triage.py --elf SLUS_xxx.xx --symbols symbols.csv [--binary NAME] [--summary]
  triage.py --elf OV11.OVL --symbols symbols.csv --binary OV11.OVL --json out.json
  triage.py --elf SLUS_xxx.xx --splat config/symbols/main.txt
  printf 'name,0xVA,size\\n' | triage.py --elf SLUS_xxx.xx --funcs -
  triage.py --elf SLUS_xxx.xx --symbols symbols.csv --name xglThreadRotate
  triage.py --self-test

`--symbols` is the CSV `tools/dump_symbols.py` writes; `--binary` selects its
rows (default: the ELF's file name). Segments come from the ELF program headers;
a binary without PT_LOAD entries falls back to its executable sections. Output is
a TSV on stdout (easiest first); `--json FILE` writes every record in full.
Exit 0 on success, 1 when no function could be triaged, 2 on bad input.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import struct
import sys
from pathlib import Path

GP, SP, RA = 28, 29, 31
OP_SPECIAL, OP_REGIMM, OP_J, OP_JAL = 0x00, 0x01, 0x02, 0x03
OP_COP1, OP_COP2, OP_COP1X = 0x11, 0x12, 0x13
OP_ADDIU, OP_ORI, OP_LUI = 0x09, 0x0D, 0x0F
OP_LW, OP_LQ, OP_SQ = 0x23, 0x1E, 0x1F
BRANCH_OPS = frozenset((0x01, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17))
FPU_OPS = frozenset((0x11, 0x13, 0x31, 0x35, 0x39, 0x3D))
COP2_OPS = frozenset((0x12, 0x36, 0x3E))
LOAD_STORE_OPS = frozenset((0x1A, 0x1B, 0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25,
                            0x26, 0x27, 0x28, 0x29, 0x2A, 0x2B, 0x2E, 0x2F, 0x31, 0x33,
                            0x35, 0x36, 0x37, 0x39, 0x3D, 0x3E, 0x3F))
FLOAT_LOAD_OPS = frozenset((0x31, 0x35))              # lwc1, ldc1
RT_DEST_OPS = frozenset((0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x18, 0x19,
                         0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x1A, 0x1B,
                         0x1E, 0x37, 0x33))
CALLEE_SAVED = frozenset(range(16, 24)) | {30}
CLASS_ORDER = ("trivial", "easy", "medium", "hard", "cop2", "huge")
TSV_COLUMNS = ("name", "va", "size", "class", "score", "instr", "callees", "switch",
               "cop2", "quad", "g_hint", "flags")


def signed16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


# ------------------------------------------------------------------- the image

class Image:
    """A 32-bit little-endian ELF: loaded segments, va <-> file offset."""

    def __init__(self, path: Path):
        d = path.read_bytes()
        if d[:4] != b"\x7fELF" or d[4] != 1 or d[5] != 1:
            raise ValueError(f"{path}: not a 32-bit little-endian ELF")
        self.data = d
        phoff, shoff = struct.unpack_from("<II", d, 0x1C)
        phent, phnum, shent, shnum, shstrndx = struct.unpack_from("<HHHHH", d, 0x2A)
        self.segments = []                       # (va, off, filesz, memsz)
        for k in range(phnum):
            typ, off, va, _pa, filesz, memsz = struct.unpack_from("<6I", d, phoff + k * phent)
            if typ == 1:
                self.segments.append((va, off, filesz, memsz))
        if not self.segments:                    # no PT_LOAD: executable sections
            for i in range(shnum):
                _n, typ, flags, addr, off, size = struct.unpack_from("<6I", d, shoff + i * shent)
                if typ == 1 and flags & 0x4:
                    self.segments.append((addr, off, size, size))
        if not self.segments:
            raise ValueError(f"{path}: no loadable segment")

    def offset(self, va: int, size: int = 4):
        for sva, off, filesz, _memsz in self.segments:
            if sva <= va and va + size <= sva + filesz:
                return off + va - sva
        return None

    def loaded(self, va: int) -> bool:
        return any(sva <= va < sva + memsz for sva, _o, _f, memsz in self.segments)

    def words(self, va: int, size: int):
        off = self.offset(va, size)
        if off is None or size % 4:
            return None
        return list(struct.unpack_from(f"<{size // 4}I", self.data, off))


# ----------------------------------------------------------------- symbol lists

def read_symbols_csv(path: Path, binary: str):
    funcs, objects = [], {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["binary"] != binary or not r["name"]:
                continue
            va, size = int(r["va"], 16), int(r["size"] or 0)
            if r["type"] == "FUNC" and size:
                funcs.append((r["name"], va, size))
            elif r["type"] == "OBJECT":
                objects.setdefault(va, (r["name"], size))
    return funcs, objects


SPLAT_RE = re.compile(r"^\s*(\S+)\s*=\s*(0x[0-9A-Fa-f]+)\s*;\s*//(.*)$")


def read_splat(path: Path):
    funcs, objects = [], {}
    for line in path.read_text().splitlines():
        m = SPLAT_RE.match(line)
        if not m:
            continue
        name, va, attrs = m.group(1), int(m.group(2), 16), m.group(3)
        typ = re.search(r"type:(\w+)", attrs)
        size = re.search(r"size:(0x[0-9A-Fa-f]+|\d+)", attrs)
        size = int(size.group(1), 0) if size else 0
        if typ and typ.group(1) == "func" and size:
            funcs.append((name, va, size))
        elif typ and typ.group(1) == "object":
            objects.setdefault(va, (name, size))
    return funcs, {}


def read_funcs_lines(fh):
    funcs = []
    for line in fh:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            raise ValueError(f"--funcs line is not name,va,size: {line!r}")
        funcs.append((parts[0], int(parts[1], 0), int(parts[2], 0)))
    return funcs, {}


# --------------------------------------------------------------------- metrics

def written_register(word: int):
    """Integer destination register; None for none; -1 for unknown (clobbers all)."""
    op = word >> 26
    rs, rt, rd = (word >> 21) & 31, (word >> 16) & 31, (word >> 11) & 31
    if op == OP_SPECIAL:
        funct = word & 63
        if funct in (0x08, 0x0C, 0x0D, 0x0F):            # jr, syscall, break, sync
            return None
        return rd
    if op == OP_REGIMM:
        return RA if rt in (0x10, 0x11) else None
    if op == OP_J or op in BRANCH_OPS:
        return None
    if op == OP_JAL:
        return RA
    if op in (OP_COP1, OP_COP2, OP_COP1X):
        return rt if rs in (0x00, 0x01, 0x02) else None    # mfc / dmfc / cfc
    if op == 0x1C:                                       # MMI
        return rd if (word & 63) not in (0x18, 0x19, 0x1A, 0x1B) else None
    if op in RT_DEST_OPS:
        return rt
    if op in LOAD_STORE_OPS:
        return None
    return -1


def metrics(words: list, va: int, image=None, objects=None, names=None) -> dict:
    """Deterministic facts for one function body, decoded word by word."""
    end = va + 4 * len(words)
    m = dict(instruction_count=len(words), branch_count=0, jal_count=0, indirect_calls=0,
             fpu_count=0, cop2_count=0, quad_count=0, gp_count=0, float_load_count=0,
             frame_size=0, has_ra_save=False, float_regs_saved=0)
    callee_vas, tail_targets, indirect_jumps, saved = [], [], [], set()
    last_jal = None
    hi: dict = {}                     # register -> lui value, for %lo pairs
    small_abs = []                    # (va, name, size) objects <= 8 bytes addressed absolutely
    for i, w in enumerate(words):
        at = va + 4 * i
        op = w >> 26
        rs, rt = (w >> 21) & 31, (w >> 16) & 31
        imm = w & 0xFFFF
        if op in BRANCH_OPS or (op in (OP_COP1, OP_COP2) and rs == 0x08):
            m["branch_count"] += 1
        if op == OP_JAL:
            m["jal_count"] += 1
            last_jal = i
            callee_vas.append(((at + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2))
        elif op == OP_J:
            target = ((at + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
            if not (va <= target < end):
                tail_targets.append(target)
        elif op == OP_SPECIAL and (w & 63) == 0x09:
            m["indirect_calls"] += 1
        elif op == OP_SPECIAL and (w & 63) == 0x08 and rs != RA:
            indirect_jumps.append(i)
        if op in FPU_OPS:
            m["fpu_count"] += 1
        if op in COP2_OPS:
            m["cop2_count"] += 1
        if op in (OP_LQ, OP_SQ):
            m["quad_count"] += 1
        if op == OP_ADDIU and rs == SP and rt == SP and signed16(imm) < 0:
            m["frame_size"] = max(m["frame_size"], -signed16(imm))
        if op in (0x2B, 0x3F) and rs == SP:                 # sw / sd into the frame
            if rt == RA:
                m["has_ra_save"] = True
            elif rt in CALLEE_SAVED:
                saved.add(rt)
        if op in (0x39, 0x3D) and rs == SP and rt >= 20:    # swc1 / sdc1 of $f20+
            m["float_regs_saved"] += 1
        if rs == GP and (op in LOAD_STORE_OPS or op in (OP_ADDIU, OP_ORI)):
            m["gp_count"] += 1
        if op in FLOAT_LOAD_OPS and rs != SP:
            m["float_load_count"] += 1
        # lui/%lo pairs: an absolute address of a small object witnesses -G0.
        if op == OP_LUI:
            hi[rt] = imm << 16
        elif rs in hi and (op in LOAD_STORE_OPS or op in (OP_ADDIU, OP_ORI)):
            addr = (hi[rs] + signed16(imm)) & 0xFFFFFFFF if op != OP_ORI else hi[rs] | imm
            if objects and addr in objects and 0 < objects[addr][1] <= 8 \
                    and (image is None or image.loaded(addr)):
                small_abs.append((addr,) + objects[addr])
        dest = written_register(w)
        if dest == -1:
            hi.clear()
        elif dest is not None and dest in hi and not (op == OP_LUI and dest == rt):
            del hi[dest]
    switch_jumps = []
    for i in indirect_jumps:
        if any((x >> 26) == OP_LW for x in words[max(0, i - 8):i]):
            switch_jumps.append(va + 4 * i)
    nosibcall = False
    if last_jal is not None:
        tail = words[last_jal + 2:]
        writes_result = any(written_register(x) in (2, 3) for x in tail)
        returns = any((x >> 26) == OP_SPECIAL and (x & 63) == 0x08 and ((x >> 21) & 31) == RA
                      for x in tail)
        nosibcall = bool(tail) and returns and not writes_result
    callees = sorted(set(callee_vas))
    m.update(callees=[names.get(c, f"{c:#x}") if names else f"{c:#x}" for c in callees],
             callee_count=len(callees), tail_targets=sorted(set(tail_targets)),
             switch_jumps=switch_jumps, callee_saved=sorted(saved),
             leaf=m["jal_count"] == 0 and m["indirect_calls"] == 0 and not tail_targets,
             tail_call=bool(tail_targets), nosibcall_candidate=nosibcall,
             small_absolute=[dict(va=f"{a:#x}", name=n, size=s) for a, n, s in small_abs[:4]])
    m["g_hint"] = "-G8" if m["gp_count"] else ("-G0" if small_abs else "?")
    return m


def classify(m: dict) -> tuple:
    """The KOS-MOS pack's tractability classifier; returns (class, score, flags)."""
    instr, branches, callees = m["instruction_count"], m["branch_count"], m["callee_count"]
    indirect, fpu, cop2 = m["indirect_calls"], m["fpu_count"], m["cop2_count"]
    pool, saved, frame = m["float_load_count"], len(m["callee_saved"]), m["frame_size"]
    switch, leaf = bool(m["switch_jumps"]), m["leaf"]
    score = (instr + 3 * branches + 6 * callees + 12 * indirect + 2 * fpu + 8 * pool
             + 6 * cop2 + 4 * saved + frame // 16 + (40 if switch else 0)
             + (150 if cop2 else 0) - (20 if leaf else 0))
    score = max(0, int(score))
    if cop2:
        kind = "cop2"
    elif instr >= 512 or callees >= 24:
        kind = "huge"
    elif instr <= 8 and leaf and branches == 0 and not fpu:
        kind = "trivial"
    elif instr <= 32 and callees <= 2 and not switch:
        kind = "easy"
    elif instr <= 128 and not switch:
        kind = "medium"
    else:
        kind = "hard"
    flags = [f for f, on in (("quad", m["quad_count"]), ("switch", switch), ("fpu", fpu),
                             ("pool", pool), ("tail", m["tail_call"]),
                             ("nosibcall", m["nosibcall_candidate"]),
                             ("indirect", indirect), ("leaf", leaf)) if on]
    return kind, score, flags


def triage(image: Image, funcs: list, objects: dict) -> tuple:
    names = {va: name for name, va, _size in funcs}
    records, skipped = [], []
    for name, va, size in funcs:
        words = image.words(va, size)
        if words is None:
            skipped.append((name, va, size))
            continue
        m = metrics(words, va, image, objects, names)
        kind, score, flags = classify(m)
        records.append(dict(name=name, va=f"{va:#x}", size=size, cls=kind, score=score,
                            flags=flags, **m))
    records.sort(key=lambda r: (CLASS_ORDER.index(r["cls"]), r["score"], r["name"]))
    return records, skipped


def tsv_row(r: dict) -> str:
    return "\t".join(str(x) for x in (
        r["name"], r["va"], r["size"], r["cls"], r["score"], r["instruction_count"],
        r["callee_count"], len(r["switch_jumps"]), r["cop2_count"], r["quad_count"],
        r["g_hint"], ",".join(r["flags"])))


def summary(records: list, skipped: list) -> str:
    hist = collections.Counter(r["cls"] for r in records)
    ghint = collections.Counter(r["g_hint"] for r in records)
    quad = sum(1 for r in records if r["quad_count"])
    lines = [f"functions: {len(records)} triaged, {len(skipped)} outside the loaded image"]
    lines.append("class:   " + "  ".join(f"{c}={hist.get(c, 0)}" for c in CLASS_ORDER))
    lines.append("g_hint:  " + "  ".join(f"{k}={v}" for k, v in sorted(ghint.items())))
    lines.append(f"quad (lq/sq): {quad}   switch: {sum(1 for r in records if r['switch_jumps'])}"
                 f"   fpu: {sum(1 for r in records if r['fpu_count'])}")
    return "\n".join(lines)


# ------------------------------------------------------------------- self-test

def _ins(op, rs=0, rt=0, imm=0):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def _jr_ra():
    return (RA << 21) | 0x08


def self_test() -> int:
    checks = {}
    va = 0x100000
    # a leaf accessor: lw v0,0(a0); jr ra; nop
    acc = [_ins(OP_LW, 4, 2, 0), _jr_ra(), 0]
    k, _s, flags = classify(metrics(acc, va))
    checks["trivial"] = k == "trivial" and "leaf" in flags
    # the same with a COP2 op in it
    k, _s, _f = classify(metrics(acc[:1] + [_ins(OP_COP2, 0x10, 1, 0)] + acc[1:], va))
    checks["cop2"] = k == "cop2"
    # a switch: sltiu; bne (a range check); lw v0,0(v1); jr v0: the table makes it hard
    sw = [_ins(0x0B, 4, 2, 6), _ins(0x05, 2, 0, 3), 0, _ins(OP_LW, 3, 2, 0), (2 << 21) | 0x08, 0,
          _jr_ra(), 0]
    k, _s, flags = classify(metrics(sw, va))
    checks["switch-hard"] = k == "hard" and "switch" in flags
    # $gp use -> -G8
    m = metrics([_ins(OP_LW, GP, 2, 0x8000), _jr_ra(), 0], va)
    checks["g8"] = m["g_hint"] == "-G8"
    # lui/lw of an 8-byte object -> -G0 witness; of a 64-byte object -> no witness
    objs = {0x00400010: ("gSmall", 8), 0x00400100: ("gBig", 64)}
    pair = lambda lo: [_ins(OP_LUI, 0, 2, 0x40), _ins(OP_LW, 2, 2, lo), _jr_ra(), 0]
    checks["g0"] = metrics(pair(0x10), va, None, objs)["g_hint"] == "-G0"
    checks["g0-big"] = metrics(pair(0x100), va, None, objs)["g_hint"] == "?"
    # a write to the lui register in between drops the pair
    stale = [_ins(OP_LUI, 0, 2, 0x40), _ins(OP_LW, 4, 2, 0), _ins(OP_LW, 2, 3, 0x10), _jr_ra(), 0]
    checks["g0-stale"] = metrics(stale, va, None, objs)["g_hint"] == "?"
    # quad flag, callee naming, nosibcall and huge
    q = [_ins(OP_LQ, 4, 2, 0), _jr_ra(), 0]
    checks["quad"] = "quad" in classify(metrics(q, va))[2]
    body = [_ins(OP_ADDIU, SP, SP, -0x10), _ins(0x3F, SP, RA, 0), (OP_JAL << 26) | (0x200000 >> 2), 0,
            _ins(0x37, SP, RA, 0), _jr_ra(), _ins(OP_ADDIU, SP, SP, 0x10)]
    m = metrics(body, va, None, None, {0x200000: "callee"})
    checks["callee-name"] = m["callees"] == ["callee"] and m["nosibcall_candidate"] and m["has_ra_save"]
    checks["huge"] = classify(metrics([0] * 512 + [_jr_ra(), 0], va))[0] == "huge"
    checks["easy"] = classify(metrics([_ins(OP_ADDIU, 4, 2, 1)] * 20 + [_jr_ra(), 0], va))[0] == "easy"
    checks["splat"] = SPLAT_RE.match("f = 0x00200008; // type:func size:0xAC").group(2) == "0x00200008"
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


# ------------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--elf", type=Path, help="the original executable or overlay")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--symbols", type=Path, help="symbols.csv from tools/dump_symbols.py")
    src.add_argument("--splat", type=Path, help="a splat symbol_addrs file")
    src.add_argument("--funcs", help="file of name,va,size lines ('-' for stdin)")
    ap.add_argument("--binary", help="CSV rows to use (default: the ELF's file name)")
    ap.add_argument("--name", action="append", help="triage only this function (repeatable)")
    ap.add_argument("--va", action="append", help="triage only the function at this address")
    ap.add_argument("--json", type=Path, help="write every record here")
    ap.add_argument("--summary", action="store_true", help="print the class histogram instead of rows")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.elf or not (args.symbols or args.splat or args.funcs):
        ap.error("--elf and one of --symbols/--splat/--funcs are required (or --self-test)")
    try:
        image = Image(args.elf)
        if args.symbols:
            funcs, objects = read_symbols_csv(args.symbols, args.binary or args.elf.name)
        elif args.splat:
            funcs, objects = read_splat(args.splat)
        else:
            fh = sys.stdin if args.funcs == "-" else open(args.funcs)
            funcs, objects = read_funcs_lines(fh)
    except (OSError, ValueError) as e:
        print(f"triage: {e}", file=sys.stderr)
        return 2
    if args.name or args.va:
        wanted_va = {int(v, 0) for v in (args.va or [])}
        funcs = [f for f in funcs if f[0] in (args.name or []) or f[1] in wanted_va]
    if not funcs:
        print("triage: no function to triage (check --binary, --name/--va)", file=sys.stderr)
        return 1
    records, skipped = triage(image, funcs, objects)
    if not records:
        print(f"triage: none of the {len(funcs)} functions lies in the loaded image", file=sys.stderr)
        return 1
    if args.json:
        args.json.write_text(json.dumps(dict(elf=args.elf.name, functions=records,
                                             skipped=[dict(name=n, va=f"{v:#x}", size=s) for n, v, s in skipped]),
                                        indent=1))
    if args.summary:
        print(summary(records, skipped))
    else:
        print("\t".join(TSV_COLUMNS))
        for r in records:
            print(tsv_row(r))
    if skipped and not args.summary:
        print(f"triage: {len(skipped)} function(s) outside the loaded image skipped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
