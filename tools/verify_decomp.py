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
  delay-slot   the object is shorter by the nops the original keeps in branch
               delay slots: GNU as 2.10 fills them, the retail assembler did not.
  size-diff    the object's symbol size differs from the original's (a longer or
               shorter body); the original-sized window is never judged alone.
  unexplained  anything else.  These are the only ones worth reading.

Exit policy: under `stock` only unexplained and size-diff (and build failures,
regressions) fail the run, the other classes are the known toolchain differences;
under `contract` every class fails, because the same compiler must reproduce the
bytes. Assembler warnings are reported as ASWARN: a build failure under contract,
informational under stock.

Known limitation: the "mixed" test masks branch and jump target fields, because
inserting one nop shifts every later instruction and so changes every branch
that spans it. That masking is what makes nop insertion legible, but it means a
genuinely wrong call target inside an otherwise-correct function would be
classified "mixed" rather than flagged. Treat "mixed" as "placement differs",
not as "verified equivalent", and read the instructions when it matters.

Usage:
  ./verify_decomp.py --upstream ~/src/some-decomp --unit main --out report.json
  ./verify_decomp.py --upstream ~/src/some-decomp --tu main/tu086 -v
  ./verify_decomp.py --upstream ~/src/some-decomp --compiler contract --toolchains DIR
  ./verify_decomp.py --src src/*.c -I include -G 0 --binary ORIGINAL.ELF --symbols symbols.csv
  ./verify_decomp.py --self-test

Target layout: --binary is any 32-bit LE ELF (a boot executable or an overlay ELF);
its program headers give the text placement and its basename selects the rows of
--symbols. Trees without the xsg-i-decomp config layout name their C files with
--src (include roots -I, one -G for all). The contract toolchain layout is read from
<upstream>/config/toolchain-identity.json when present (--contract-name picks a
contract), and --contract-cc1/--contract-as or DECOMP_CONTRACT_CC1/DECOMP_CONTRACT_AS
override it; the defaults below are the fallback.

Compilers (--compiler):
  stock     the unpatched ee-gcc 2.96 cc1 + ee-as 2.10 of the i386 rootfs (EE_ROOTFS):
            the independent, outside view.
  contract  the decomp's own pinned toolchain: DIR/ee-gcc2.96-realconv-lp7-fsv3-v1/bin/cc1
            (cc1 driven exactly as the ee-gcc driver does: -G<n> -quiet -O2 -fno-builtin)
            and DIR/ee-legacy-binutils-la29-vsqrt-nolabel-laimm-v1/bin/ee-as
            (-EL -m5900 -mabi=eabi -G<n> -I .), both i386 ELFs mounted read-only into
            the same linux/386 box next to the rootfs. cc1 runs through the rootfs
            loader; the la29 ee-as needs glibc >= 2.34 and runs on the box's own libc.
            The default when DIR (--toolchains, DECOMP_TOOLCHAINS, or
            <upstream>/.work/toolchains) holds both tools.

Fail-closed: docker rc != 0 -> exit 2 (stderr tail printed); every run builds in a fresh
--work/run-<pid>-<ms>/ tree (the report records `work`; only trees whose pid is dead or
older than the docker timeout plus a margin are removed, so concurrent runs under one
--work never delete each other); a build failure or zero jobs -> exit 1; --baseline
REPORT.json -> exit 1 when any function that matched in that report no longer matches.

TUs come from config/tu-build.json (unit, path, in_scope) joined with the per-TU ledger
config/tu/<unit>/tuNNN.json (mode, contract flags: the -G flag); the compile manifest
config/objects/<unit>.compile.json is only a fallback, announced on stderr, because it
goes stale. Preprocessing runs on the host with `clang -E -P -DSKIP_ASM` (not cpp0):
only the token stream reaches cc1, which is what -O2 without -g depends on.

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
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

# Where your own extracted originals and symbol table live. Set DECOMP_GAME_DIR,
# or pass --symbols/--binary explicitly. Nothing game-derived ships with this kit.
GAME = Path(os.environ.get("DECOMP_GAME_DIR", "."))

# The original's text placement is read from its program headers (image_segments /
# image_offset below), never assumed: SLUS_204.69 has .text at VA 0x200000 / file
# 0x1000, an overlay or another game's boot ELF does not.

# A decomp's pinned compile contract, relative to its toolchains directory. These are
# the defaults for a tree that has no config/toolchain-identity.json; when the
# upstream carries one, resolve_contract() reads the tool layout from it instead, and
# --contract-cc1 / --contract-as (or DECOMP_CONTRACT_CC1 / DECOMP_CONTRACT_AS) override
# both. Both tools are i386 ELFs.
CONTRACT_CC1 = "ee-gcc2.96-realconv-lp7-fsv3-v1/bin/cc1"
CONTRACT_AS = "ee-legacy-binutils-la29-vsqrt-nolabel-laimm-v1/bin/ee-as"
CONTRACT_CC1_FLAGS = "-fno-builtin"                 # what the ee-gcc driver adds (driver_flags)
CONTRACT_AS_FLAGS = "-EL -m5900 -mabi=eabi -I ."    # contract assembler_argv, -G<n> per TU
STOCK_CC1 = "/rootfs/opt/ee/lib/gcc-lib/ee/2.96-ee-001003-1/cc1"
STOCK_AS = "/rootfs/opt/ee/bin/ee-as"
STEP_TIMEOUT = 30                                    # seconds per cc1 / as step inside the box

R_MIPS_32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16, R_MIPS_GPREL16 = 2, 4, 5, 6, 7
STT_OBJECT, STT_FUNC, STT_SECTION = 1, 2, 3
NOP = b"\x00\x00\x00\x00"
E = "<"


# ------------------------------------------------------------- image layout

def image_segments(image: bytes) -> list:
    """[(va, file offset, filesz, memsz)] of every PT_LOAD of a 32-bit LE ELF."""
    phoff, = struct.unpack_from(E + "I", image, 0x1C)
    ent, num = struct.unpack_from(E + "HH", image, 0x2A)
    segs = []
    for k in range(num):
        typ, off, va, _pa, filesz, memsz = struct.unpack_from(E + "6I", image, phoff + k * ent)
        if typ == 1:
            segs.append((va, off, filesz, memsz))
    return segs


def image_offset(segments: list, va: int):
    """File offset of VA inside a loaded segment's file-backed part, else None."""
    for sva, soff, filesz, _memsz in segments:
        if sva <= va < sva + filesz:
            return soff + va - sva
    return None


# ---------------------------------------------------------------- symbol table

class Symbols:
    """Our own ELF symbol table, from out/symbols.csv."""

    def __init__(self, csv_path: Path, binary: str = "SLUS_204.69"):
        self.exact: dict[str, tuple[int, int]] = {}
        self.stripped: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
        # The resident image calls into overlays loaded at fixed addresses (e.g.
        # main's Game -> OV01 entryFirstInit). A name absent from `binary` may
        # resolve to an overlay symbol, but only when exactly one address is
        # defined for it across the overlays: never guess between candidates.
        self.foreign: dict[str, set[tuple[int, int]]] = collections.defaultdict(set)
        with open(csv_path) as fh:
            for r in csv.DictReader(fh):
                if r["binary"] != binary:
                    if r["name"] and r["type"] in ("FUNC", "OBJECT") and r["bind"] == "GLOBAL":
                        self.foreign[r["name"]].add((int(r["va"], 16), int(r["size"])))
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
        foreign = self.foreign.get(name, set())
        if len(foreign) == 1:
            return next(iter(foreign))[0]
        if foreign:
            raise KeyError(f"{name} (defined at {len(foreign)} overlay addresses)")
        raise KeyError(name)

    def size(self, name: str) -> int:
        return self.exact.get(name, (0, 0))[1]

    def known(self, name: str) -> bool:
        """True when the table holds the name at all (exactly, as a suffixed local, or in
        an overlay): a reference to an unknown name is a defect of the C, not of the table."""
        return name in self.exact or name in self.stripped or name in self.foreign


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

    def __init__(self, path: Path, syms: Symbols, image: "bytes | None" = None):
        """`image` (the original executable) lets section-relative references be
        checked: a jump table in .rodata resolves only if every entry points at
        the same code as the original's table. Without it they stay unresolved."""
        self.syms = syms
        self.image = image
        d, secs = read_elf(path)
        self._raw = d
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
                # `size` is the original's; `obj_size` what the assembler emitted. They are
                # compared by the callers: a longer or shorter body is never a match.
                self.funcs.append(dict(name=s["name"], off=s["val"], va=va, size=size, obj_size=s["size"]))
        self.funcs.sort(key=lambda f: f["off"])

        rel = next((s for s in secs if s["name"] == ".rel.text"), None)
        self.relocs = ([struct.unpack_from(E + "II", d, rel["off"] + k * 8)
                        for k in range(rel["size"] // 8)] if rel else [])
        self.sections = secs
        self._pending_lo: dict[int, int] = {}
        self.placeholders: list[tuple[int, str]] = []
        self._segments = self._image_segments() if image else []
        self.unresolved: list[tuple[int, str]] = []
        # (offset, name) of every reference to a name the table does not hold at all:
        # the C named something the binary never had (a typo, an invented name).
        self.undeclared: list[tuple[int, str]] = []
        self._relocate()

    def _off_to_va(self, off: int):
        for f in self.funcs:
            if f["off"] <= off < f["off"] + max(f["size"], f.get("obj_size") or 0):
                return f["va"] + (off - f["off"])
        return None

    def _word(self, off):
        return struct.unpack_from(E + "I", self.text, off)[0]

    def _put(self, off, val):
        struct.pack_into(E + "I", self.text, off, val & 0xFFFFFFFF)

    # ---- section-relative references (file-local code, statics, jump tables)

    def _image_segments(self):
        return [(va, off, filesz) for va, off, filesz, _memsz in image_segments(self.image)]

    def _image_word(self, va):
        for sva, soff, size in self._segments:
            if sva <= va < sva + size:
                return struct.unpack_from(E + "I", self.image, soff + va - sva)[0]
        return None

    def _pair_addend(self, i, off, w):
        """Full REL addend of a HI16 at relocs[i] and the offset of its LO16.

        The MIPS ABI lets several HI16 relocations share the next LO16 against
        the same symbol (GNU as emits them when one %hi feeds several %lo), so
        look ahead past other HI16s instead of requiring the very next entry.
        """
        si = self.relocs[i][1] >> 8
        for noff, ninfo in self.relocs[i + 1:]:
            if (ninfo >> 8) != si:
                continue
            if (ninfo & 0xFF) == R_MIPS_LO16:
                lo = self._word(noff) & 0xFFFF
                return ((w & 0xFFFF) << 16) + (lo - 0x10000 if lo & 0x8000 else lo), noff
            if (ninfo & 0xFF) != R_MIPS_HI16:
                break
        return None, None

    def _local_symbol_va(self, shndx, addend):
        """The address of section+addend through a named object-file symbol that covers
        it, looked up in our table. The symbol may be OBJECT, FUNC or NOTYPE: ee-as emits
        a TU's `.lcomm`/`.comm` statics (`static int x;` in .sbss/.bss, a function-local
        `static` as `name.N`) as NOTYPE, and the retail symtab has them the same way.
        ADDEND may fall inside the object (a field of a static struct, an element of a
        static array): the covering symbol is the last one at or below it whose extent
        (its size, else the gap to the next named symbol or the section end) reaches it."""
        named = sorted((s["val"], s) for s in self.symbols
                       if s["shndx"] == shndx and s["name"] and s["typ"] in (STT_OBJECT, STT_FUNC, 0))
        for k, (val, s) in enumerate(named):
            if val > addend:
                break
            end = val + s["size"] if s["size"] else (named[k + 1][0] if k + 1 < len(named)
                                                      else self.sections[shndx]["size"])
            if addend < max(end, val + 1):
                try:
                    return self.syms.value(s["name"]) + (addend - val)
                except KeyError:
                    return None
        return None

    def _gp0(self):
        """ri_gp_value of the object's .reginfo: the gp the assembler assumed for its own
        gp-relative data references. A GPREL16 against a section symbol stores
        offset - gp0 in the field (0xc000 for .sbss+0 with the usual 0x4000)."""
        reg = next((s for s in self.sections if s["name"] == ".reginfo"), None)
        if reg is None or reg["size"] < 24:
            return 0
        return struct.unpack_from(E + "I", self._raw, reg["off"] + 20)[0]

    def _rodata_table_va(self, shndx, addend, hi_off, lo_off):
        """Anchor a jump table: take the address the original encodes at this
        HI16/LO16 pair, then accept it only if our table (its R_MIPS_32 entries
        into .text) points every entry at the same code as the original table."""
        if not self.image:
            return None, "no original image to anchor .rodata against"
        hi_va, lo_va = self._off_to_va(hi_off), self._off_to_va(lo_off)
        if hi_va is None or lo_va is None:
            return None, "HI16/LO16 outside known functions"
        ohi, olo = self._image_word(hi_va), self._image_word(lo_va)
        olo16 = olo & 0xFFFF
        table = ((ohi & 0xFFFF) << 16) + (olo16 - 0x10000 if olo16 & 0x8000 else olo16)
        relname = ".rel" + self.sections[shndx]["name"]
        rel = next((s for s in self.sections if s["name"] == relname), None)
        if rel is None:
            return None, f"no {relname} for the table at +{addend:#x}"
        sec = self.sections[shndx]
        entries = {}
        for k in range(rel["size"] // 8):
            roff, rinfo = struct.unpack_from(E + "II", self._raw, rel["off"] + k * 8)
            rsym = self.symbols[rinfo >> 8]
            if (rinfo & 0xFF) == R_MIPS_32 and rsym["typ"] == STT_SECTION and rsym["shndx"] == self.text_idx:
                entries[roff] = struct.unpack_from(E + "I", self._raw, sec["off"] + roff)[0]
        n = 0
        while addend + 4 * n in entries:
            target = self._off_to_va(entries[addend + 4 * n])
            if target is None or target != self._image_word(table + 4 * n):
                return None, f"jump table entry {n} differs from the original table at {table:#x}"
            n += 1
        if n == 0:
            return None, f"{self.sections[shndx]['name']}+{addend:#x} is not a jump table"
        return table, None

    def _section_hi(self, i, off, w, sym):
        """HI16 against a section symbol: resolve, write %hi, remember the LO16."""
        sec = self.sections[sym["shndx"]]["name"]
        addend, noff = self._pair_addend(i, off, w)
        if addend is None:
            return f"{sec} HI16 without paired LO16"
        if sec == ".text":
            va = self._off_to_va(addend)
            why = None if va is not None else f"text offset {addend:#x} outside known funcs"
        elif sec.startswith(".rodata"):
            va = self._local_symbol_va(sym["shndx"], addend)
            why = None
            if va is None:
                va, why = self._rodata_table_va(sym["shndx"], addend, off, noff)
                if va is None and why and why.startswith(("jump table entry", "HI16/LO16 outside")):
                    # Not provable yet (the code around the table does not line up with
                    # the original). Leave the address as 0 and still compare: the
                    # original never holds a table at 0, so this can only report a
                    # mismatch with a usable diff, never a false match.
                    self.placeholders.append((off, why))
                    self._pending_lo[noff] = 0
                    return None
        else:
            va = self._local_symbol_va(sym["shndx"], addend)
            why = None if va is not None else f"no named symbol at {sec}+{addend:#x}"
        if va is None:
            return why
        low = va & 0xFFFF
        low = low - 0x10000 if low & 0x8000 else low
        self._put(off, (w & 0xFFFF0000) | (((va - low) >> 16) & 0xFFFF))
        self._pending_lo[noff] = va
        return None

    def _relocate(self):
        for i, (off, info) in enumerate(self.relocs):
            rtype, si = info & 0xFF, info >> 8
            sym = self.symbols[si]
            w = self._word(off)

            if sym["typ"] == STT_SECTION:
                sec = self.sections[sym["shndx"]]["name"]
                if rtype == R_MIPS_HI16:
                    why = self._section_hi(i, off, w, sym)
                    if why:
                        self.unresolved.append((off, why))
                    continue
                if rtype == R_MIPS_LO16 and off in self._pending_lo:
                    self._put(off, (w & 0xFFFF0000) | (self._pending_lo.pop(off) & 0xFFFF))
                    continue
                if rtype == R_MIPS_GPREL16 and sec != ".text":
                    a = w & 0xFFFF
                    a = (a - 0x10000 if a & 0x8000 else a) + self._gp0()      # section offset
                    va = self._local_symbol_va(sym["shndx"], a)
                    if va is None:
                        self.unresolved.append((off, f"no named symbol for {sec}+{a:#x} GPREL16"))
                    else:
                        self._put(off, (w & 0xFFFF0000) | ((va - self.syms.gp) & 0xFFFF))
                    continue
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
                if not self.syms.known(sym["name"]):
                    self.undeclared.append((off, sym["name"]))
                    self.unresolved.append((off, f"unknown symbol {sym['name']} (not in the symbol table)"))
                else:
                    self.unresolved.append((off, e.args[0] if e.args else str(e)))
                continue

            if rtype == R_MIPS_26:
                addend = (w & 0x03FFFFFF) << 2
                self._put(off, (w & 0xFC000000) | (((value + addend) >> 2) & 0x03FFFFFF))
            elif rtype == R_MIPS_HI16:
                # REL format: the addend is split across this HI16 and the LO16
                # that follows it, and %hi must carry the LO16's sign.
                addend, _noff = self._pair_addend(i, off, w)
                if addend is None:
                    self.unresolved.append((off, "HI16 without paired LO16"))
                    continue
                v = value + addend
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
        """The original-sized window of .text at the function: what classify() reads."""
        return bytes(self.text[f["off"]:f["off"] + f["size"]])

    def object_bytes(self, f) -> bytes:
        """What the object actually holds for the function (its own symbol size)."""
        return bytes(self.text[f["off"]:f["off"] + (f.get("obj_size") or f["size"])])

    def span(self, f):
        return f["off"], f["off"] + max(f["size"], f.get("obj_size") or 0)

    def is_clean(self, f) -> bool:
        lo, hi = self.span(f)
        return not any(lo <= o < hi for o, _ in self.unresolved)

    def undeclared_in(self, f) -> list:
        """Names the function refers to that the symbol table does not hold, sorted."""
        lo, hi = self.span(f)
        return sorted({n for o, n in self.undeclared if lo <= o < hi})


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


DIFF_KINDS = ("hazard-nop", "reorder", "mixed", "immediate", "delay-slot", "size-diff", "unexplained")
FAILING_KINDS = {"stock": ("size-diff", "unexplained"), "contract": DIFF_KINDS}


def delay_slot(obj: bytes, orig: bytes) -> bool:
    """The object is shorter than the original only because the assembler moved
    instructions into branch delay slots the original left as nops: with the nops
    removed both sides hold the same instruction multiset. GNU as 2.10 fills delay
    slots (`mips_optimize` 2); the 2.9-era assembler the retail objects came from,
    as the Xenosaga I project runs it (`-g`), does not. A known stock-vs-retail
    difference, never a match."""
    if len(obj) >= len(orig) or (len(orig) - len(obj)) % 4:
        return False
    a = [w for w in words(obj) if w != NOP]
    b = [w for w in words(orig) if w != NOP]
    return len(a) == len(b) and collections.Counter(a) == collections.Counter(b) and \
        orig.count(NOP) - obj.count(NOP) >= (len(orig) - len(obj)) // 4


def judge_sizes(kind: str, detail: str, obj_size: int, size: int, compiler: str):
    """Fold the object-vs-original size comparison into a classification: a size
    difference is `size-diff` (with both sizes) unless the stock lane explains it as
    missing hazard nops; under contract nothing explains it."""
    if obj_size == size:
        return kind, detail
    if compiler != "contract" and kind in ("hazard-nop", "mixed"):
        return kind, detail
    note = f" ({kind}: {detail})" if kind not in ("match",) and detail else ""
    return "size-diff", f"size {obj_size} B vs {size} B original{note}"


def run_failed(totals, compiler: str, regressions) -> bool:
    """The exit-1 rule: build failures and regressions always; the mismatch classes of
    FAILING_KINDS[compiler] (every class under contract, only the unexplained ones
    under stock)."""
    return bool(totals.get("build-fail") or regressions
                or any(totals.get(k) for k in FAILING_KINDS.get(compiler, DIFF_KINDS)))


# -------------------------------------------------------------------- driver

def default_toolchains(upstream):
    """DECOMP_TOOLCHAINS, else <upstream>/.work/toolchains when it exists, else None."""
    env = os.environ.get("DECOMP_TOOLCHAINS")
    if env:
        return Path(env).expanduser()
    local = Path(upstream) / ".work" / "toolchains"
    return local if local.is_dir() else None


def contract_from_identity(upstream, name=None):
    """The compile contract a decomp pins in <upstream>/config/toolchain-identity.json
    (schema toolchain-identity/1: `tools` with `dir`/`path`, `contracts` with `compiler`,
    `assembler`, `driver_flags`, `assembler_argv`), as {cc1, ee_as, cc1_flags, as_flags,
    contract}: the tool paths relative to the toolchains root, the cc1 codegen flags the
    driver adds (-f*/-m*; -nostdinc is a preprocessor flag), the assembler argv without
    its per-TU -G. NAME picks a contract; otherwise the first one whose compiler is a
    GCC 2.96-family cc1. None when the file or a usable contract is missing."""
    if not upstream:
        return None
    try:
        d = json.loads((Path(upstream) / "config" / "toolchain-identity.json").read_text())
    except (OSError, ValueError):
        return None
    tools, contracts = d.get("tools") or {}, d.get("contracts") or {}
    for cname, c in contracts.items():
        if not isinstance(c, dict) or not c.get("compiler") or not c.get("assembler"):
            continue
        if name and cname != name:
            continue
        comp, asm = tools.get(c["compiler"]) or {}, tools.get(c["assembler"]) or {}
        if not name and "2.96" not in str(comp.get("version", "")) + c["compiler"]:
            continue
        cc1 = comp.get("dir") + "/cc1" if comp.get("dir") else comp.get("path")
        ee_as = asm.get("path") or (asm.get("dir") + "/ee-as" if asm.get("dir") else None)
        if not cc1 or not ee_as:
            continue
        return dict(cc1=cc1, ee_as=ee_as, contract=cname,
                    cc1_flags=" ".join(f for f in c.get("driver_flags") or [] if f.startswith(("-f", "-m"))),
                    as_flags=" ".join(f for f in c.get("assembler_argv") or [] if not f.startswith("-G")))
    return None


def resolve_contract(toolchains, upstream=None, overrides=None, name=None):
    """The contract toolchain to drive, or None. Candidates in order: explicit
    overrides (--contract-cc1/--contract-as, or DECOMP_CONTRACT_CC1/DECOMP_CONTRACT_AS),
    the upstream's toolchain-identity.json, then this file's defaults; the first whose
    two tools exist under TOOLCHAINS wins. The result carries absolute `cc1_path` /
    `as_path` next to the relative `cc1` / `ee_as` and the flags."""
    if not toolchains:
        return None
    overrides = dict(overrides or {})
    for key, env in (("cc1", "DECOMP_CONTRACT_CC1"), ("ee_as", "DECOMP_CONTRACT_AS"),
                     ("cc1_flags", "DECOMP_CONTRACT_CC1_FLAGS"), ("as_flags", "DECOMP_CONTRACT_AS_FLAGS")):
        if overrides.get(key) is None and os.environ.get(env) is not None:
            overrides[key] = os.environ[env]
    defaults = dict(cc1=CONTRACT_CC1, ee_as=CONTRACT_AS, cc1_flags=CONTRACT_CC1_FLAGS,
                    as_flags=CONTRACT_AS_FLAGS, contract="kit-default")
    candidates = []
    if overrides.get("cc1") or overrides.get("ee_as"):
        candidates.append(dict(defaults, contract="override", **{k: v for k, v in overrides.items() if v is not None}))
    identity = contract_from_identity(upstream, name)
    if identity:
        candidates.append(dict(identity, **{k: v for k, v in overrides.items()
                                            if k in ("cc1_flags", "as_flags") and v is not None}))
    candidates.append(defaults)
    for cand in candidates:
        cc1, ee_as = Path(toolchains) / cand["cc1"], Path(toolchains) / cand["ee_as"]
        if cc1.is_file() and ee_as.is_file():
            return dict(cand, cc1_path=cc1, as_path=ee_as)
    return None


def contract_tools(toolchains, upstream=None, overrides=None):
    """(cc1, ee-as) absolute paths of the resolved contract, or None when incomplete."""
    c = resolve_contract(toolchains, upstream, overrides)
    return (c["cc1_path"], c["as_path"]) if c else None


def build_script(compiler: str, contract=None) -> str:
    """The shell run inside the linux/386 box. Stock tools run through the rootfs
    loader; the contract cc1 does too, while the contract ee-as (glibc >= 2.34) runs
    on the box's own libc. Every step has a 30 s timeout; the script always exits 0
    so a per-TU failure is reported as CC1FAIL/ASFAIL, never as a docker failure.
    ASWARN names a TU whose assembler wrote to stderr yet produced an object."""
    if compiler == "contract":
        c = contract or dict(cc1=CONTRACT_CC1, ee_as=CONTRACT_AS, cc1_flags=CONTRACT_CC1_FLAGS,
                             as_flags=CONTRACT_AS_FLAGS)
        cc1, cc1_extra = "$LD /toolchains/" + c["cc1"], c["cc1_flags"]
        as_run, as_flags = "/toolchains/" + c["ee_as"], c["as_flags"]
    else:
        cc1, cc1_extra = "$LD " + STOCK_CC1, ""
        as_run, as_flags = "$LD " + STOCK_AS, ""
    return (
        '#!/bin/sh\n'
        'LD="/rootfs/lib/ld-linux.so.2 --library-path /rootfs/lib/i386-linux-gnu"\n'
        'if command -v timeout >/dev/null 2>&1; then T="timeout -k 5 %d"; else T=""; fi\n'
        'cd /work\n'
        'for i in pp/*.i; do\n'
        '  [ -f "$i" ] || continue\n'
        '  tu=$(basename "$i" .i)\n'
        '  G=$(cat flags/$tu 2>/dev/null); [ -z "$G" ] && G=-G8\n'
        '  $T %s "$i" $G -quiet -O2 %s -o "asm/$tu.s" 2>"asm/$tu.cc1err" \\\n'
        '    || { echo "CC1FAIL $tu rc=$?"; continue; }\n'
        '  $T %s %s $G -o "obj/$tu.o" "asm/$tu.s" 2>"asm/$tu.aserr" \\\n'
        '    || { echo "ASFAIL $tu rc=$?"; continue; }\n'
        '  [ -s "asm/$tu.aserr" ] && echo "ASWARN $tu"\n'
        'done\n'
        'exit 0\n' % (STEP_TIMEOUT, cc1, cc1_extra, as_run, as_flags))


def docker_build(work: Path, rootfs: Path, jobs: list, image: str, compiler: str = "stock",
                 toolchains=None, timeout: int = 900, contract=None):
    """Run cc1 + as over every preprocessed TU inside a linux/386 box.

    Returns (docker rc, [CC1FAIL/ASFAIL lines], stderr tail). A non-zero rc means
    the box itself failed (no docker, no binfmt, a bad mount): the caller must
    treat the run as unverified, never as "0 functions match"."""
    script = work / "build.sh"
    script.write_text(build_script(compiler, contract))
    mounts = ["-v", f"{rootfs}:/rootfs:ro", "-v", f"{work}:/work"]
    if compiler == "contract":
        mounts += ["-v", f"{Path(toolchains).resolve()}:/toolchains:ro"]
    try:
        out = subprocess.run(
            ["docker", "run", "--rm", "--platform", "linux/386"] + mounts
            + [image, "/bin/sh", "/work/build.sh"],
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, [], "docker: command not found"
    except subprocess.TimeoutExpired:
        return 124, [], f"docker run exceeded {timeout} s"
    fails = [l for l in out.stdout.splitlines() if l.startswith(("CC1FAIL", "ASFAIL", "ASWARN"))]
    return out.returncode, fails, (out.stderr or out.stdout).strip()[-1500:]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def stale_runs(work: Path, timeout: int, now=None, alive=pid_alive) -> list:
    """The run-<pid>-<ms> trees under WORK nobody can still be using: their pid is dead,
    or they are older than the docker timeout plus a margin. Live trees of concurrent
    runs are kept."""
    now = time.time() if now is None else now
    out = []
    for tree in work.glob("run-*"):
        m = re.match(r"run-(\d+)-(\d+)$", tree.name)
        if not m:
            continue
        pid, started = int(m.group(1)), int(m.group(2)) / 1000.0
        if not alive(pid) or now - started > timeout + 60:
            out.append(tree)
    return sorted(out)


def discover_tus(up: Path, unit: str, warn=None) -> list:
    """The unit's TUs as [{id, path, mode, in_scope, flags, compiler_profile}]: from
    config/tu-build.json (unit, path, in_scope) joined with the per-TU ledger
    config/tu/<unit>/tuNNN.json (mode, contract flags). The compile manifest
    config/objects/<unit>.compile.json is only a fallback, reported through `warn`,
    because it goes stale (63 C TUs listed against 158 built on 2026-09-27)."""
    up = Path(up)
    out = []
    try:
        entries = json.loads((up / "config" / "tu-build.json").read_text()).get("tus") or []
    except (OSError, ValueError, AttributeError):
        entries = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("unit") != unit or not entry.get("id") or not entry.get("path"):
            continue
        try:
            ledger = json.loads((up / "config" / "tu" / unit / (entry["id"].split("/")[-1] + ".json")).read_text())
        except (OSError, ValueError):
            ledger = {}
        contract = ledger.get("contract") or entry.get("contract") or {}
        out.append(dict(id=entry["id"], path=entry["path"], mode=ledger.get("mode"),
                        in_scope=bool(ledger.get("in_scope", entry.get("in_scope"))),
                        flags=list(contract.get("flags") or []), compiler_profile=contract.get("compiler_profile") or ""))
    if out:
        return sorted(out, key=lambda e: e["id"])
    manifest = up / "config" / "objects" / f"{unit}.compile.json"
    try:
        tus = json.loads(manifest.read_text())["tus"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if warn:
        warn(f"{up / 'config' / 'tu-build.json'} holds no {unit} TU: falling back to the compile manifest "
             f"{manifest}, which may be stale (its mode and in_scope lag the per-TU ledgers)")
    return sorted((dict(id=v["id"], path=v["path"], mode=v.get("mode"), in_scope=bool(v.get("in_scope")),
                        flags=list(v.get("flags") or []), compiler_profile=str(v.get("compiler_profile") or ""))
                   for v in tus.values() if isinstance(v, dict) and v.get("id") and v.get("path")),
                  key=lambda e: e["id"])


def direct_tus(sources: list, gflag: str = "-G8") -> list:
    """TU entries for C files named on the command line (--src), for a tree that has no
    xsg-i-decomp config layout: id = the file's stem (numbered when repeated), absolute
    path, mode c, in scope, the one -G flag for all of them."""
    out, seen = [], collections.Counter()
    for src in sources:
        path = Path(src).expanduser().resolve()
        stem = path.stem
        seen[stem] += 1
        tu_id = stem if seen[stem] == 1 else f"{stem}~{seen[stem]}"
        out.append(dict(id=tu_id, path=str(path), mode="c", in_scope=True, flags=["-O2", gflag],
                        compiler_profile="ee-gcc2_96"))
    return out


def baseline_regressions(baseline: dict, per_tu: dict, matched: dict) -> list:
    """Functions (or, for an old report without a `matched` map, TUs) that matched in
    `baseline` and no longer do. Only TUs verified in this run are compared."""
    out = []
    old_matched = baseline.get("matched") or {}
    for tu in per_tu:
        if tu in old_matched:
            for name in old_matched[tu]:
                if name not in matched.get(tu, []):
                    out.append(f"{tu} {name}")
            continue
        before = (baseline.get("per_tu") or {}).get(tu, {}).get("match")
        now = per_tu[tu].get("match", 0)
        if isinstance(before, int) and now < before:
            out.append(f"{tu} match count {now} < {before}")
    return out


def self_test() -> int:
    """Network-free checks of the classifier, the build scripts and the baseline rule."""
    def w(*vals):
        return b"".join(struct.pack(E + "I", v) for v in vals)
    checks = {}
    checks["match"] = classify(w(1, 2, 3), w(1, 2, 3))[0] == "match"
    checks["hazard-nop"] = classify(w(1, 2, 3, 4), w(1, 0, 2, 3))[0] == "hazard-nop"
    checks["reorder"] = classify(w(1, 2, 3), w(2, 1, 3))[0] == "reorder"
    checks["immediate"] = classify(w(1, 2, 3), w(1, 9, 3))[0] == "immediate"
    checks["unexplained"] = classify(w(1, 2, 3, 4), w(5, 6, 7, 8))[0] == "unexplained"
    checks["mask-jal"] = mask_branch(struct.pack(E + "I", 0x0C0001FF)) == struct.pack(E + "I", 0x0C000000)
    stock, contract = build_script("stock"), build_script("contract")
    checks["stock-script"] = STOCK_CC1 in stock and STOCK_AS in stock and "-fno-builtin" not in stock
    checks["contract-script"] = (CONTRACT_CC1 in contract and CONTRACT_AS in contract
                                 and CONTRACT_CC1_FLAGS in contract and "-mabi=eabi" in contract
                                 and "$LD /toolchains/" + CONTRACT_AS not in contract)
    checks["timeout"] = f"timeout -k 5 {STEP_TIMEOUT}" in contract and contract.rstrip().endswith("exit 0")
    checks["aswarn-script"] = 'echo "ASWARN $tu"' in contract and 'echo "ASWARN $tu"' in stock
    checks["size-diff"] = (judge_sizes("match", "", 68, 64, "contract")[0] == "size-diff"
                           and "68 B vs 64 B" in judge_sizes("match", "", 68, 64, "contract")[1]
                           and judge_sizes("hazard-nop", "1 nop", 60, 64, "stock") == ("hazard-nop", "1 nop")
                           and judge_sizes("hazard-nop", "1 nop", 60, 64, "contract")[0] == "size-diff"
                           and judge_sizes("match", "", 64, 64, "stock") == ("match", ""))
    # delay-slot: the object lacks exactly the nops the original keeps in delay slots
    checks["delay-slot"] = (delay_slot(w(1, 2, 3), w(1, 0, 2, 3)) and delay_slot(w(2, 1, 3), w(1, 0, 2, 3))
                            and not delay_slot(w(1, 2, 3), w(1, 2, 3)) and not delay_slot(w(1, 2, 9), w(1, 0, 2, 3))
                            and not delay_slot(w(1, 2, 3, 4), w(1, 2, 3)))
    checks["exit-policy"] = (run_failed({"delay-slot": 1}, "stock", []) is False and run_failed({"delay-slot": 1}, "contract", [])
                             and run_failed({"reorder": 1}, "stock", []) is False and run_failed({"reorder": 1}, "contract", []) is True
                             and run_failed({"hazard-nop": 1}, "contract", []) and run_failed({"immediate": 1}, "contract", [])
                             and run_failed({"mixed": 1}, "contract", []) and run_failed({"size-diff": 1}, "stock", [])
                             and run_failed({"match": 3}, "contract", []) is False and run_failed({}, "stock", ["x"]))
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        now = 1_000_000.0
        for name in ("run-11-998000000", "run-22-999999000", "run-33-999999000", "junk-1"):
            (work / name).mkdir()          # 11: alive but 2000 s old; 22: alive, fresh; 33: dead
        stale = stale_runs(work, 900, now=now, alive=lambda pid: pid != 33)
        checks["stale-runs"] = [d.name for d in stale] == ["run-11-998000000", "run-33-999999000"]
        up = work / "up"
        (up / "config" / "tu" / "main").mkdir(parents=True)
        (up / "config" / "tu-build.json").write_text(json.dumps(dict(tus=[
            dict(id="main/tu001", unit="main", path="src/main/a.c", in_scope=True, kind="gcc"),
            dict(id="main/tu002", unit="main", path="src/main/b.c", in_scope=True, kind="gcc"),
            dict(id="ov01/tu000", unit="ov01", path="src/ov01/z.c", in_scope=True, kind="gcc")])))
        for tu, mode in (("tu001", "c"), ("tu002", "asm")):
            (up / "config" / "tu" / "main" / f"{tu}.json").write_text(json.dumps(dict(
                mode=mode, in_scope=True, contract=dict(flags=["-O2", "-G0"], compiler_profile="ee-gcc2_96-realconv"))))
        found = discover_tus(up, "main")
        checks["discover-ledger"] = ([(e["id"], e["mode"], e["flags"]) for e in found]
                                     == [("main/tu001", "c", ["-O2", "-G0"]), ("main/tu002", "asm", ["-O2", "-G0"])])
        warned = []
        (up / "config" / "objects").mkdir()
        (up / "config" / "objects" / "main.compile.json").write_text(json.dumps(dict(tus=dict(
            a=dict(id="main/tu003", path="src/main/c.c", mode="c", in_scope=True, flags=["-G8"], compiler_profile="ee-gcc2_96-realconv-lp7")))))
        (up / "config" / "tu-build.json").unlink()
        fallback = discover_tus(up, "main", warn=warned.append)
        checks["discover-fallback"] = ([(e["id"], e["mode"], e["flags"]) for e in fallback] == [("main/tu003", "c", ["-G8"])]
                                       and len(warned) == 1 and "stale" in warned[0])
        # text placement from program headers: two PT_LOADs, one of them with a .bss tail
        elf = bytearray(0x34 + 2 * 0x20)
        elf[:4] = b"\x7fELF"
        struct.pack_into(E + "I", elf, 0x1C, 0x34)
        struct.pack_into(E + "HH", elf, 0x2A, 0x20, 2)
        struct.pack_into(E + "8I", elf, 0x34, 1, 0x1000, 0x200000, 0x200000, 0x138570, 0x138570, 5, 0x10)
        struct.pack_into(E + "8I", elf, 0x54, 1, 0x140000, 0x340000, 0x340000, 0x100, 0x1000, 6, 0x10)
        segs = image_segments(bytes(elf))
        checks["segments"] = (image_offset(segs, 0x2188a0) == 0x198a0 and image_offset(segs, 0x340010) == 0x140010
                              and image_offset(segs, 0x340200) is None and image_offset(segs, 0x1fffff) is None
                              and segs[1][3] == 0x1000)
        # a contract read from a decomp's toolchain-identity.json, then resolved on disk
        ident = work / "ident"
        (ident / "config").mkdir(parents=True)
        (ident / "config" / "toolchain-identity.json").write_text(json.dumps(dict(
            tools={"sdk-cc1": dict(version="gcc 2.9-ee-991111", dir="old/bin"),
                   "game-cc1": dict(version="gcc 2.96-ee-001003-1 (cc1), patched", dir="new-v9/bin"),
                   "as-x": dict(path="as-x/bin/ee-as")},
            contracts={"sdk": dict(compiler="sdk-cc1", assembler="as-x", driver_flags=["-nostdinc"],
                                   assembler_argv=["-EL", "-G0"]),
                       "game-G0-g": dict(compiler="game-cc1", assembler="as-x", driver_flags=["-nostdinc", "-fno-builtin"],
                                         assembler_argv=["-EL", "-m5900", "-mabi=eabi", "-G0", "-g", "-I", "."])})))
        c = contract_from_identity(ident)
        checks["identity"] = (c == dict(cc1="new-v9/bin/cc1", ee_as="as-x/bin/ee-as", contract="game-G0-g",
                                        cc1_flags="-fno-builtin", as_flags="-EL -m5900 -mabi=eabi -g -I .")
                              and contract_from_identity(ident, "sdk")["cc1"] == "old/bin/cc1"
                              and contract_from_identity(work) is None)
        tc = work / "tc"
        for rel in ("new-v9/bin/cc1", "as-x/bin/ee-as", "mine/cc1", "mine/as"):
            (tc / rel).parent.mkdir(parents=True, exist_ok=True)
            (tc / rel).write_bytes(b"x")
        r = resolve_contract(tc, ident)
        checks["resolve-identity"] = r is not None and r["contract"] == "game-G0-g" and r["cc1_path"] == tc / "new-v9/bin/cc1"
        r = resolve_contract(tc, ident, dict(cc1="mine/cc1", ee_as="mine/as", cc1_flags=None, as_flags="-EL"))
        checks["resolve-override"] = r is not None and r["contract"] == "override" and r["as_flags"] == "-EL" \
            and r["cc1_flags"] == CONTRACT_CC1_FLAGS
        checks["resolve-missing"] = resolve_contract(tc, work) is None and resolve_contract(None, ident) is None
        script = build_script("contract", r)
        checks["contract-script-custom"] = "$LD /toolchains/mine/cc1" in script and "/toolchains/mine/as -EL $G" in script
        (work / "a.c").write_text("int a;"); (work / "sub").mkdir(); (work / "sub" / "a.c").write_text("int b;")
        d = direct_tus([str(work / "a.c"), str(work / "sub" / "a.c")], "-G0")
        checks["direct-tus"] = [t["id"] for t in d] == ["a", "a~2"] and d[0]["flags"] == ["-O2", "-G0"] \
            and Path(d[1]["path"]).is_absolute() and d[0]["mode"] == "c"
    # covering-symbol rule for section-relative references (NOTYPE statics, inner offsets)
    stub = Object.__new__(Object)
    stub.sections = [dict(name=".bss", size=0x60, off=0), dict(name=".reginfo", size=24, off=0)]
    stub.symbols = [dict(name="", val=0, size=0, typ=STT_SECTION, shndx=0),
                    dict(name="sSemaParam.0", val=0, size=0, typ=0, shndx=0),
                    dict(name="sThreadParam.1", val=0x18, size=0, typ=0, shndx=0),
                    dict(name="asActiveThreadList", val=0x48, size=0, typ=0, shndx=0),
                    dict(name="tbl", val=0x10, size=8, typ=STT_OBJECT, shndx=1)]
    class _T:
        def value(self, name):
            return {"sSemaParam.0": 0x880fa0, "sThreadParam.1": 0x880fb8, "asActiveThreadList": 0x880fe8,
                    "tbl": 0x400000}[name]
    stub.syms = _T()
    checks["local-notype"] = stub._local_symbol_va(0, 0) == 0x880fa0 and stub._local_symbol_va(0, 0x18) == 0x880fb8
    checks["local-inner"] = (stub._local_symbol_va(0, 0x4c) == 0x880fe8 + 4 and stub._local_symbol_va(0, 0x1c) == 0x880fb8 + 4
                             and stub._local_symbol_va(1, 0x14) == 0x400004 and stub._local_symbol_va(1, 0x18) is None
                             and stub._local_symbol_va(0, 0x60) is None)
    stub._raw = b"\0" * 20 + struct.pack(E + "I", 0x4000) + b"\0" * 4
    stub.sections[1]["off"] = 0
    checks["gp0"] = stub._gp0() == 0x4000
    checks["known"] = Symbols.known.__doc__ is not None
    base = dict(matched={"main/tu1": ["a", "b"]}, per_tu={"main/tu1": {"match": 2}, "main/tu2": {"match": 3}})
    checks["baseline-fn"] = baseline_regressions(base, {"main/tu1": {"match": 1}}, {"main/tu1": ["a"]}) == ["main/tu1 b"]
    checks["baseline-count"] = baseline_regressions(base, {"main/tu2": {"match": 2}}, {"main/tu2": ["x", "y"]}) == ["main/tu2 match count 2 < 3"]
    checks["baseline-ok"] = baseline_regressions(base, {"main/tu1": {"match": 2}}, {"main/tu1": ["a", "b"]}) == []
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upstream", type=Path,
                    help="path to the upstream decomp checkout (its config/ names the TUs and, when it "
                         "carries config/toolchain-identity.json, the contract toolchain layout)")
    ap.add_argument("--src", nargs="+", type=Path, metavar="FILE",
                    help="verify these C files directly instead of the upstream's TU list (any tree); "
                         "give include roots with --include and the small-data flag with -G")
    ap.add_argument("--include", "-I", action="append", type=Path, default=[], metavar="DIR",
                    help="include root for --src files (repeatable; default: each file's directory)")
    ap.add_argument("-G", dest="gflag", default="8", help="small-data threshold for --src files (default 8)")
    ap.add_argument("--define", "-D", action="append", default=[], metavar="NAME[=VAL]",
                    help="extra preprocessor define (SKIP_ASM is always defined)")
    ap.add_argument("--contract-name", help="pick this contract from the upstream's toolchain-identity.json")
    ap.add_argument("--contract-cc1", help="contract cc1, relative to --toolchains (overrides the identity file)")
    ap.add_argument("--contract-as", help="contract assembler, relative to --toolchains")
    ap.add_argument("--contract-cc1-flags", help="codegen flags the contract driver adds to cc1 (e.g. -fno-builtin)")
    ap.add_argument("--contract-as-flags", help="contract assembler argv without -G (e.g. '-EL -m5900 -mabi=eabi -I .')")
    ap.add_argument("--compiler", choices=("stock", "contract"),
                    help="stock rootfs ee-gcc, or the decomp's pinned contract toolchain "
                         "(default: contract when --toolchains holds both tools, else stock); under "
                         "contract every mismatch class and an assembler warning fail the run (exit 1)")
    ap.add_argument("--toolchains", type=Path,
                    help="directory holding the contract cc1 and ee-as "
                         "(default: DECOMP_TOOLCHAINS, else <upstream>/.work/toolchains)")
    ap.add_argument("--baseline", type=Path,
                    help="an earlier --out report: exit 1 if any function it matched no longer matches")
    ap.add_argument("--docker-timeout", type=int, default=900, help="seconds for the whole docker run")
    ap.add_argument("--self-test", action="store_true", help="network-free checks of this tool; exit 0/1")
    ap.add_argument("--unit", default="main", help="unit to verify (default: main)")
    ap.add_argument("--tu", action="append", help="verify only this TU id (repeatable)")
    ap.add_argument("--symbols", type=Path, default=GAME / "symbols.csv",
                    help="symbol table CSV (see tools/dump_symbols.py)")
    ap.add_argument("--binary", type=Path, default=GAME / "SLUS_204.69",
                    help="the original ELF to compare against; its basename selects the rows of "
                         "--symbols and its program headers give the text placement")
    ap.add_argument("--rootfs", type=Path,
                    default=Path(os.environ.get("EE_ROOTFS", Path.home() / "ee-rootfs")),
                    help="i386 rootfs holding opt/ee (stock ee-gcc 2.96)")
    ap.add_argument("--work", type=Path, default=Path.home() / ".cache/decomp-verify",
                    help="scratch dir; must be under $HOME for Docker mounts")
    ap.add_argument("--image", default="debian:bookworm-slim")
    ap.add_argument("--out", type=Path, help="write a JSON report here")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.upstream is None and not args.src:
        ap.error("--upstream or --src is required (or --self-test)")

    up = args.upstream
    if args.src:
        tus = direct_tus([str(s) for s in args.src], "-G" + str(args.gflag).lstrip("-G"))
        includes = [Path(d).expanduser().resolve() for d in args.include] or sorted(
            {Path(s).expanduser().resolve().parent for s in args.src})
        if up is None:
            up = Path(os.path.commonpath([str(d) for d in includes]))
    else:
        tus = discover_tus(up, args.unit, warn=lambda m: print(m, file=sys.stderr))
        includes = [up / "include", up / f"src/{args.unit}", up]
        if not tus:
            print(f"no TU list for {args.unit} under {up}: neither config/tu-build.json (with the per-TU ledgers"
                  f" config/tu/{args.unit}/) nor config/objects/{args.unit}.compile.json; "
                  f"for another tree layout name the C files with --src", file=sys.stderr)
            return 2

    toolchains = args.toolchains or default_toolchains(up)
    overrides = dict(cc1=args.contract_cc1, ee_as=args.contract_as,
                     cc1_flags=args.contract_cc1_flags, as_flags=args.contract_as_flags)
    contract = resolve_contract(toolchains, up, overrides, args.contract_name)
    tools = (contract["cc1_path"], contract["as_path"]) if contract else None
    compiler = args.compiler or ("contract" if tools else "stock")
    if compiler == "contract" and not tools:
        print(f"contract toolchain not found under {toolchains}: no candidate layout has both tools "
              f"(tried the overrides, {up}/config/toolchain-identity.json, and the defaults "
              f"{CONTRACT_CC1} + {CONTRACT_AS})", file=sys.stderr)
        return 2
    if compiler == "contract":
        print(f"contract: {contract['contract']}  cc1={contract['cc1']}  as={contract['ee_as']}  "
              f"cc1-flags='{contract['cc1_flags']}'  as-flags='{contract['as_flags']}'")
    if not (args.rootfs / "lib" / "ld-linux.so.2").is_file():
        print(f"no i386 rootfs at {args.rootfs} (lib/ld-linux.so.2 missing); set EE_ROOTFS or --rootfs",
              file=sys.stderr)
        return 2
    baseline = None
    if args.baseline:
        try:
            baseline = json.loads(args.baseline.read_text())
        except (OSError, ValueError) as e:
            print(f"--baseline {args.baseline}: {e}", file=sys.stderr)
            return 2

    syms = Symbols(args.symbols, binary=args.binary.name)
    slus = args.binary.read_bytes()
    if slus[:4] != b"\x7fELF":
        print(f"{args.binary}: not an ELF (a flat overlay image needs its ELF, or a wrapper that "
              f"carries program headers)", file=sys.stderr)
        return 2
    segments = image_segments(slus)
    if not segments:
        print(f"{args.binary}: no PT_LOAD segment; cannot place functions", file=sys.stderr)
        return 2

    # One fresh tree per run, never reused: a stale object is never judged, and a
    # path the box has never seen cannot be served from colima's mount cache (reusing
    # work/obj across runs made the box write into a directory the host no longer had).
    # Only trees no live run can own go: a concurrent run under the same --work keeps its.
    args.work.mkdir(parents=True, exist_ok=True)
    for stale in stale_runs(args.work, args.docker_timeout):
        shutil.rmtree(stale, ignore_errors=True)
    for legacy in ("pp", "asm", "obj", "flags", "build.sh"):       # the pre-run-tree layout
        path = args.work / legacy
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file():
            path.unlink()
    work = args.work / f"run-{os.getpid()}-{int(time.time() * 1000)}"
    for sub in ("pp", "asm", "obj", "flags"):
        (work / sub).mkdir(parents=True)
    print(f"work: {work}")

    jobs, skipped = [], []
    for v in tus:
        if v.get("mode") != "c" or not v.get("in_scope"):
            continue
        if not str(v.get("compiler_profile", "")).startswith("ee-gcc2_96"):
            continue
        if args.tu and v["id"] not in args.tu:
            continue
        src = Path(v["path"]) if Path(v["path"]).is_absolute() else up / v["path"]
        if not src.exists():
            continue
        tag = v["id"].replace("/", "__")
        # SKIP_ASM drops the INCLUDE_ASM/ACCEPTED_ASM stubs, so a partly
        # migrated TU still yields exactly its C functions.
        cpp = subprocess.run(
            ["clang", "-E", "-P", "-DSKIP_ASM"] + [f"-D{d}" for d in args.define]
            + [a for d in includes for a in ("-I", str(d))]
            + [str(src), "-o", str(work / "pp" / f"{tag}.i")],
            capture_output=True, text=True)
        if cpp.returncode:
            skipped.append((v["id"], "preprocess", cpp.stderr.strip().splitlines()[:1]))
            continue
        gflag = next((f for f in v.get("flags", []) if f.startswith("-G")), "-G8")
        (work / "flags" / tag).write_text(gflag)
        jobs.append(dict(tu=v["id"], tag=tag, path=v["path"], flags=v.get("flags", [])))

    print(f"preprocessed {len(jobs)} TU(s); {len(skipped)} skipped  [compiler: {compiler}]")
    if not jobs:
        print("no TU to verify (nothing in scope, or the --tu ids do not exist)", file=sys.stderr)
        return 1
    rc, build_fails, tail = docker_build(work, args.rootfs, jobs, args.image, compiler,
                                         toolchains, args.docker_timeout, contract)
    if rc != 0:
        print(f"docker run failed (rc {rc}); nothing was verified\n{tail}", file=sys.stderr)
        return 2
    failed = {l.split()[1] for l in build_fails if not l.startswith("ASWARN")}
    warned = {l.split()[1] for l in build_fails if l.startswith("ASWARN")}
    warnings = []
    for line in build_fails:
        kind, tag = line.split()[:2]
        status = line.split()[2] if len(line.split()) > 2 else ""
        err = (work / "asm" / f"{tag}.{'cc1err' if kind == 'CC1FAIL' else 'aserr'}")
        first = err.read_text().strip().splitlines()[:1] if err.exists() else []
        if kind == "ASWARN":
            # the assembler spoke: under contract that is a build failure (the same
            # assembler must stay silent on the published source), under stock a note
            warnings.append(dict(tu=tag.replace("__", "/"), msg=" ".join(first)))
            if compiler == "contract":
                skipped.append((tag, "aswarn", first))
            continue
        skipped.append((tag, kind.lower() + (f" {status}" if status else ""), first))
    if compiler == "contract":
        failed |= warned

    totals = collections.Counter()
    results, per_tu, matched = [], {}, {}
    for job in jobs:
        if job["tag"] in failed:
            per_tu[job["tu"]] = {"build": "fail"}
            totals["build-fail"] += 1
            continue
        obj = work / "obj" / f"{job['tag']}.o"
        if not obj.exists():
            totals["build-fail"] += 1
            continue
        o = Object(obj, syms, image=slus)
        counts = collections.Counter()
        for f in o.funcs:
            if not o.is_clean(f):
                counts["skip"] += 1
                totals["skip"] += 1
                continue
            ours = o.bytes_for(f)
            off = image_offset(segments, f["va"])
            if off is None:                       # outside every loaded segment of this image
                counts["skip"] += 1
                totals["skip"] += 1
                continue
            orig = slus[off:off + f["size"]]
            if len(ours) != f["size"]:
                counts["skip"] += 1
                totals["skip"] += 1
                continue
            kind, detail = judge_sizes(*classify(ours, orig), f.get("obj_size") or f["size"], f["size"], compiler)
            if kind == "size-diff" and delay_slot(o.object_bytes(f), orig):
                kind, detail = "delay-slot", f"{(f['size'] - f['obj_size']) // 4} delay slot(s) filled by the assembler"
            counts[kind] += 1
            totals[kind] += 1
            if kind == "match":
                matched.setdefault(job["tu"], []).append(f["name"])
            if kind != "match":
                results.append(dict(tu=job["tu"], func=f["name"], va=f"{f['va']:#x}",
                                    size=f["size"], kind=kind, detail=detail))
        counts["nosym"] += len(o.unknown)
        totals["nosym"] += len(o.unknown)
        per_tu[job["tu"]] = dict(counts)

    print("=" * 74)
    title = ("Independent byte verification -- stock ee-gcc 2.96 + stock ee-as" if compiler == "stock"
             else f"Contract byte verification -- {contract['cc1']} + {contract['ee_as']}")
    print(title)
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
    n_diff = sum(totals[k] for k in DIFF_KINDS)
    denom = n_match + n_diff
    pct = (100.0 * n_match / denom) if denom else 0.0
    print(f"  identical: {n_match}/{denom}  ({pct:.1f}%)   differing: {n_diff}"
          f"   unverifiable: {totals['skip']}   unknown symbol: {totals['nosym']}"
          f"   build failures: {totals['build-fail']}")
    if n_diff:
        print("\n  mismatches by cause:")
        for k in DIFF_KINDS:
            if totals[k]:
                print(f"    {k:<12} {totals[k]}")
        unex = [r for r in results if r["kind"] in ("unexplained", "size-diff")]
        if unex:
            print("\n  UNEXPLAINED / SIZE-DIFF (the only ones worth reading under stock):")
            for r in unex:
                print(f"    {r['tu']:<14} {r['func']:<30} {r['detail']}")
    if warnings:
        print("\n  assembler warnings (ASWARN; " + ("a build failure under contract" if compiler == "contract"
                                                     else "informational under stock") + "):")
        for wrn in warnings:
            print(f"    {wrn['tu']:<14} {wrn['msg']}")
    if args.verbose and skipped:
        print("\n  skipped TUs:")
        for tu, why, msg in skipped:
            print(f"    {tu:<14} {why:<11} {' '.join(msg)}")

    regressions = baseline_regressions(baseline, per_tu, matched) if baseline else []
    if regressions:
        print("\n  REGRESSION against --baseline:")
        for line in regressions:
            print(f"    {line}")

    if args.out:
        args.out.write_text(json.dumps(
            dict(compiler=compiler, work=str(work), toolchains=str(toolchains) if compiler == "contract" else None,
                 contract=contract["contract"] if compiler == "contract" else None,
                 binary=str(args.binary), segments=[dict(va=f"{va:#x}", off=off, filesz=fs, memsz=ms)
                                                    for va, off, fs, ms in segments],
                 tools=dict(cc1=str(tools[0]) if tools and compiler == "contract" else STOCK_CC1,
                            ee_as=str(tools[1]) if tools and compiler == "contract" else STOCK_AS),
                 rootfs=str(args.rootfs), jobs=len(jobs), totals=dict(totals), per_tu=per_tu,
                 matched=matched, mismatches=results, regressions=regressions, warnings=warnings,
                 skipped=[dict(tu=t, why=w, msg=m) for t, w, m in skipped]), indent=1))
        print(f"\nwrote {args.out}")
    if totals["build-fail"]:
        print(f"\n  {totals['build-fail']} TU(s) failed to build: not verified", file=sys.stderr)
    return 1 if run_failed(totals, compiler, regressions) else 0


if __name__ == "__main__":
    sys.exit(main())
