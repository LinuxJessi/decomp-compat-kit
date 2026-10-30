#!/usr/bin/env python3
"""Transfer function names from an unstripped binary to a stripped one by code shape.

Two PS2 games built with the same compiler family from the same SDK era share
verbatim objects: the SCE libraries, newlib, libgcc, and whatever engine code
survived between releases. In those objects only the relocated fields differ
(call targets, %hi/%lo and $gp immediates). Mask those fields, keep the opcode
and the register allocation, and a donor function's body becomes a signature
that can be searched for in another binary.

This is the "symbol donor" method used twice by hand in the Xenosaga work:
Xenosaga I's unstripped SLUS_204.69 named 428 functions in the stripped
Xenosaga III SLUS_213.89 and 255 SDK functions in Final Fantasy X's SLUS_203.12.
It is also how PS2Recomp's sce-symbol-scanner reaches ~99.8% precision on SDK
names. A hit is a *lead*: the bytes agree modulo relocations, which for SDK
and CRT code is as good as a name gets, and for game code deserves a look.

Method
  * donor functions: FUNC rows of the kit's symbols.csv (dump_symbols.py) for
    --donor-binary, size >= --min-size; bytes located through the donor ELF's
    program headers (never a hardcoded .text placement);
  * acceptor code: every PT_LOAD segment with the execute flag of an ELF, or
    the whole file at --acceptor-base for a flat image (an overlay, an inflated
    EE memory image);
  * masking (strict): the 26-bit field of j/jal; the 16-bit immediate of every
    load/store (incl. lq/sq/ld/sd/lwc1/swc1), of lui/addiu/daddiu/ori and of
    every branch; everything else (register fields, funct, shamt, MMI,
    COP1/COP2 forms) is kept. loose additionally masks slti/sltiu/andi/xori
    immediates: it recovers objects whose only change is a table bound or a
    constant (4 of the 428 XS1->XS3 names needed it) at the price of a weaker
    signature;
  * search: an index of the acceptor keyed by the first --anchor-words masked
    words at every 4-byte position, then a full-length masked compare; each
    hit records how many raw words are identical (relocations aside, a
    verbatim object scores total/total).

Usage
  transfer_symbols.py --donor SLUS_A --donor-symbols symbols.csv [--donor-binary SLUS_A]
                      --acceptor SLUS_B [--acceptor-base 0xVA] [--min-size 64]
                      [--anchor-words 8] [--min-identical 0.0] [--mask strict|loose]
                      -o matches.tsv [--splat acceptor_symbols.txt] [--all-hits]
                      [--compare reference.tsv]
  transfer_symbols.py --self-test

Output TSV: name, donor_va, size, acceptor_vas (comma-separated), identical,
total, confidence (high >= 0.9 raw-identical, med >= 0.6, low), ambiguous
(another donor name has the same masked body). --splat emits the acceptor's
names in splat's `name = 0xVA; // type:func size:0xN` form, skipping ambiguous
and multi-hit rows unless --all-hits. --compare takes a reference in the
4-column form `name<TAB>donor_va<TAB>size<TAB>acceptor_va[,acceptor_va]` and
reports reproduced / missed / new rows.

Exit codes: 0 ran (also when nothing matched), 2 usage or unreadable input.
"""
from __future__ import annotations

import argparse
import collections
import csv
import struct
import sys
import time
from pathlib import Path

PT_LOAD, PF_X = 1, 1
LOADSTORE_OPS = set(range(0x20, 0x2F)) | set(range(0x30, 0x40)) | {0x1E, 0x1F}   # incl. sd, lq, sq
IMM16_OPS = LOADSTORE_OPS | {0x0F, 0x09, 0x19, 0x0D}            # lui addiu daddiu ori
BRANCH_OPS = {0x01, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17}
LOOSE_OPS = {0x0A, 0x0B, 0x0C, 0x0E}                             # slti sltiu andi xori
JUMP_OPS = {0x02, 0x03}


# ------------------------------------------------------------------ images

def read_elf_segments(data: bytes):
    """[(va, file_off, filesz, memsz, flags)] of the PT_LOAD segments, or None."""
    if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
        return None
    phoff, = struct.unpack_from("<I", data, 0x1C)
    phent, phnum = struct.unpack_from("<HH", data, 0x2A)
    out = []
    for k in range(phnum):
        typ, off, va, _pa, filesz, memsz, flags = struct.unpack_from("<7I", data, phoff + k * phent)
        if typ == PT_LOAD and filesz:
            out.append((va, off, filesz, memsz, flags))
    return out


class Image:
    """Loaded bytes of an ELF (by program headers) or of a flat file at a base."""

    def __init__(self, path: Path, base=None, exec_only=False):
        self.data = path.read_bytes()
        segs = read_elf_segments(self.data)
        if segs is None:
            if base is None:
                raise ValueError(f"{path}: not an ELF; pass --acceptor-base for a flat image")
            self.segments = [(base, 0, len(self.data), len(self.data), PF_X)]
        else:
            if exec_only and any(f & PF_X for *_, f in segs):
                segs = [s for s in segs if s[4] & PF_X]
            self.segments = [s[:5] for s in segs]

    def read(self, va: int, n: int):
        for sva, off, filesz, _memsz, _f in self.segments:
            if sva <= va and va + n <= sva + filesz:
                return self.data[off + va - sva: off + va - sva + n]
        return None

    def code_runs(self):
        """(va, bytes) per executable segment, word-aligned."""
        for sva, off, filesz, _memsz, _f in self.segments:
            n = filesz & ~3
            yield sva, self.data[off: off + n]


# ----------------------------------------------------------------- masking

def mask_word(w: int, loose: bool) -> int:
    op = w >> 26
    if op in JUMP_OPS:
        return w & 0xFC000000
    if op in IMM16_OPS or op in BRANCH_OPS or (loose and op in LOOSE_OPS):
        return w & 0xFFFF0000
    if op == 0x11 and ((w >> 21) & 0x1F) == 0x08:                # bc1t/bc1f family
        return w & 0xFFFF0000
    return w


def masked(words, loose: bool):
    return [mask_word(w, loose) for w in words]


def unpack(b: bytes):
    return struct.unpack(f"<{len(b) // 4}I", b[: len(b) & ~3])


# ------------------------------------------------------------------- donor

def donor_functions(csv_path: Path, binary: str, min_size: int):
    funcs = []
    with open(csv_path, newline="") as fh:
        for r in csv.DictReader(fh):
            if r["binary"] != binary or r["type"] != "FUNC":
                continue
            size = int(r["size"] or 0)
            if size >= min_size and r["name"]:
                funcs.append((r["name"], int(r["va"], 16), size & ~3))
    return sorted(set(funcs), key=lambda f: f[1])


# ------------------------------------------------------------------ search

class Index:
    def __init__(self, image: Image, anchor_words: int, loose: bool):
        self.anchor = anchor_words
        self.loose = loose
        self.runs = []            # (va, raw_words, masked_words)
        self.table = collections.defaultdict(list)
        for va, blob in image.code_runs():
            raw = unpack(blob)
            msk = masked(raw, loose)
            run = len(self.runs)
            self.runs.append((va, raw, msk))
            n = len(msk) - anchor_words + 1
            table = self.table
            for i in range(max(n, 0)):
                table[tuple(msk[i:i + anchor_words])].append((run, i))

    def find(self, words):
        """[(acceptor_va, identical_count)] for a donor's raw words."""
        msk = masked(words, self.loose)
        if len(msk) < self.anchor:
            return []
        hits = []
        for run, i in self.table.get(tuple(msk[: self.anchor]), ()):
            va, raw, rmsk = self.runs[run]
            end = i + len(msk)
            if end > len(rmsk) or rmsk[i:end] != msk:
                continue
            identical = sum(1 for a, b in zip(raw[i:end], words) if a == b)
            hits.append((va + 4 * i, identical))
        return hits


def confidence(identical: int, total: int) -> str:
    frac = identical / total if total else 0.0
    return "high" if frac >= 0.9 else "med" if frac >= 0.6 else "low"


def transfer(donor: Image, funcs, acceptor: Image, anchor_words=8, loose=False, min_identical=0.0):
    index = Index(acceptor, anchor_words, loose)
    bodies = collections.defaultdict(list)
    rows = []
    for name, va, size in funcs:
        blob = donor.read(va, size)
        if blob is None or len(blob) < 4 * anchor_words:
            continue
        words = unpack(blob)
        bodies[tuple(masked(words, loose))].append(name)
        hits = index.find(words)
        if not hits:
            continue
        total = len(words)
        best = max(h[1] for h in hits)
        if best / total < min_identical:
            continue
        rows.append(dict(name=name, donor_va=va, size=size, hits=sorted(hits), identical=best, total=total,
                         confidence=confidence(best, total)))
    for row in rows:
        blob = donor.read(row["donor_va"], row["size"])
        names = bodies[tuple(masked(unpack(blob), loose))]
        row["ambiguous"] = sorted(n for n in names if n != row["name"])
    return rows


# ------------------------------------------------------------------ output

def write_tsv(rows, out):
    out.write("name\tdonor_va\tsize\tacceptor_vas\tidentical\ttotal\tconfidence\tambiguous\n")
    for r in rows:
        out.write("\t".join([r["name"], f"0x{r['donor_va']:x}", str(r["size"]),
                             ",".join(f"0x{va:x}" for va, _ in r["hits"]), str(r["identical"]),
                             str(r["total"]), r["confidence"], ",".join(r["ambiguous"])]) + "\n")


def write_splat(rows, out, all_hits=False):
    seen = set()
    for r in sorted(rows, key=lambda r: r["hits"][0][0]):
        if (r["ambiguous"] or len(r["hits"]) > 1) and not all_hits:
            continue
        for va, _ in r["hits"]:
            if va in seen:
                continue
            seen.add(va)
            out.write(f"{r['name']} = 0x{va:08X}; // type:func size:0x{r['size']:X}\n")


def read_reference(path: Path):
    ref = {}
    for line in path.read_text().splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 4 or parts[0].startswith("#"):
            continue
        ref[(parts[0], int(parts[1], 16))] = {int(v, 16) for v in parts[3].split(",") if v}
    return ref


def compare(rows, ref):
    ours = {(r["name"], r["donor_va"]): {va for va, _ in r["hits"]} for r in rows}
    same = partial = missed = 0
    missed_rows, partial_rows = [], []
    for key, vas in ref.items():
        got = ours.get(key)
        if got is None:
            missed += 1
            missed_rows.append(key)
        elif got == vas:
            same += 1
        else:
            partial += 1
            partial_rows.append((key, vas, got))
    new = [k for k in ours if k not in ref]
    return dict(reference=len(ref), reproduced=same, different_addresses=partial, missed=missed, new=len(new),
                missed_rows=missed_rows, partial_rows=partial_rows, new_rows=new)


def summary(rows, funcs, elapsed):
    hist = collections.Counter(r["confidence"] for r in rows)
    multi = sum(1 for r in rows if len(r["hits"]) > 1)
    amb = sum(1 for r in rows if r["ambiguous"])
    print(f"donors tried: {len(funcs)}   matched: {len(rows)}   unique hit: {len(rows) - multi}   "
          f"multiple hits: {multi}   ambiguous donors: {amb}")
    print(f"confidence: high {hist['high']}  med {hist['med']}  low {hist['low']}   ({elapsed:.1f} s)")


# --------------------------------------------------------------- self-test

def self_test() -> int:
    def ins(op, rs=0, rt=0, imm=0):
        return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)

    def jal(target):
        return (3 << 26) | ((target >> 2) & 0x03FFFFFF)

    body = [ins(0x09, 29, 29, -0x20), ins(0x3F, 29, 31, 0x10), ins(0x0F, 0, 2, 0x1234), jal(0x201000),
            ins(0x23, 2, 4, 0x5678), ins(0x09, 4, 4, 1), jal(0x201200), ins(0x2B, 2, 4, 0x5678),
            ins(0x37, 29, 31, 0x10), 0x03E00008, ins(0x09, 29, 29, 0x20), 0]
    twin = list(body)
    twin[2] = ins(0x0F, 0, 2, 0x4321); twin[3] = jal(0x301000); twin[4] = ins(0x23, 2, 4, 0x1111)
    twin[6] = jal(0x301200); twin[7] = ins(0x2B, 2, 4, 0x1111)
    other = list(body)
    other[5] = ins(0x09, 5, 5, 1)                                  # a different register: never a match
    pad = [0x00000025] * 6

    class Flat(Image):
        def __init__(self, words, base):
            self.data = struct.pack(f"<{len(words)}I", *words)
            self.segments = [(base, 0, len(self.data), len(self.data), PF_X)]

    donor = Flat(pad + body + pad + other + pad, 0x200000)
    acceptor = Flat(pad * 3 + twin + pad, 0x300000)
    funcs = [("fn", 0x200000 + 4 * len(pad), 4 * len(body)),
             ("other", 0x200000 + 4 * (2 * len(pad) + len(body)), 4 * len(other))]
    rows = transfer(donor, funcs, acceptor, anchor_words=8)
    checks = {
        "one-match": [r["name"] for r in rows] == ["fn"],
        "address": bool(rows) and rows[0]["hits"] == [(0x300000 + 4 * 3 * len(pad), len(body) - 5)],
        "confidence": bool(rows) and rows[0]["confidence"] == "low" and rows[0]["total"] == 12,
        "not-ambiguous": bool(rows) and rows[0]["ambiguous"] == [],
        "mask-keeps-funct": mask_word(0x03E00008, False) == 0x03E00008 and mask_word(jal(0x100), False) == 0x0C000000,
        "loose": mask_word(ins(0x0C, 2, 2, 0xFF), True) == ins(0x0C, 2, 2, 0) and mask_word(ins(0x0C, 2, 2, 0xFF), False) != ins(0x0C, 2, 2, 0),
    }
    ref = {("fn", funcs[0][1]): {rows[0]["hits"][0][0]} if rows else set(), ("ghost", 0x1): {0x2}}
    c = compare(rows, ref)
    checks["compare"] = c["reproduced"] == 1 and c["missed"] == 1 and c["new"] == 0
    ok = all(checks.values())
    print("self-test", "ok" if ok else "FAIL", checks)
    return 0 if ok else 1


# -------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--donor", type=Path, help="the unstripped ELF")
    ap.add_argument("--donor-symbols", type=Path, help="symbols.csv from tools/dump_symbols.py")
    ap.add_argument("--donor-binary", help="`binary` column to use (default: the donor's file name)")
    ap.add_argument("--acceptor", type=Path, help="the stripped ELF, or a flat code image")
    ap.add_argument("--acceptor-base", type=lambda s: int(s, 0), help="load address of a flat acceptor image")
    ap.add_argument("--min-size", type=int, default=64, help="smallest donor function in bytes (default 64)")
    ap.add_argument("--anchor-words", type=int, default=8)
    ap.add_argument("--min-identical", type=float, default=0.0,
                    help="drop hits whose raw-identical fraction is below this (default 0)")
    ap.add_argument("--mask", choices=("strict", "loose"), default="strict")
    ap.add_argument("-o", "--out", type=Path, help="TSV of matches (default: stdout)")
    ap.add_argument("--splat", type=Path, help="write the acceptor's names in splat symbol format")
    ap.add_argument("--all-hits", action="store_true", help="--splat also emits ambiguous and multi-hit rows")
    ap.add_argument("--compare", type=Path, help="reference matches (name, donor_va, size, acceptor_vas)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not (args.donor and args.donor_symbols and args.acceptor):
        ap.error("--donor, --donor-symbols and --acceptor are required (or --self-test)")
    try:
        donor = Image(args.donor)
        acceptor = Image(args.acceptor, base=args.acceptor_base, exec_only=True)
        funcs = donor_functions(args.donor_symbols, args.donor_binary or args.donor.name, args.min_size)
    except (OSError, ValueError, KeyError) as e:
        print(f"transfer_symbols: {e}", file=sys.stderr)
        return 2
    if not funcs:
        print(f"no FUNC rows for binary {args.donor_binary or args.donor.name} in {args.donor_symbols}", file=sys.stderr)
        return 2
    t0 = time.time()
    rows = transfer(donor, funcs, acceptor, args.anchor_words, args.mask == "loose", args.min_identical)
    rows.sort(key=lambda r: r["donor_va"])
    if args.out:
        with open(args.out, "w") as fh:
            write_tsv(rows, fh)
    else:
        write_tsv(rows, sys.stdout)
    if args.splat:
        with open(args.splat, "w") as fh:
            write_splat(rows, fh, args.all_hits)
    summary(rows, funcs, time.time() - t0)
    if args.compare:
        c = compare(rows, read_reference(args.compare))
        print(f"compare: reference {c['reference']}  reproduced {c['reproduced']}  "
              f"different addresses {c['different_addresses']}  missed {c['missed']}  new {c['new']}")
        for key in c["missed_rows"][:20]:
            print(f"  missed  {key[0]} 0x{key[1]:x}")
        for key, vas, got in c["partial_rows"][:20]:
            print(f"  differ  {key[0]} 0x{key[1]:x}: ref {[hex(v) for v in sorted(vas)]} ours {[hex(v) for v in sorted(got)]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
