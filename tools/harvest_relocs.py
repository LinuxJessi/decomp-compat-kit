#!/usr/bin/env python3
"""Read symbol addresses out of a retail binary at functions proven identical modulo relocations.

When a compiled function equals the original word for word except in the fields its
relocations fill in (a jal target, a %hi/%lo pair, a $gp offset), each of those fields in
the original holds the address the linker gave the symbol the relocation names. Read them
back and every symbol the function refers to gets its address in the original: globals by
name, file-local statics by name, a TU's sections by base. One function proves little about
a symbol it alone references; a symbol referenced from several sites or functions is
checked, because every site must agree. Conflicts are reported, never resolved.

Only functions that pass the field-masked identity check are read: same size, every word
identical once the relocated FIELD is masked (j/jal: low 26 bits; HI16, LO16, GPREL16,
LITERAL: low 16 bits; anything else: the whole word, and the function is flagged).

  harvest_relocs.py --obj tu.o [--obj ...] --binary ORIGINAL.ELF --placements names.tsv \\
                    [--gp 0xVA] [-o harvested.csv] [--report harvest.json]
  harvest_relocs.py --self-test

--placements is a TSV of `name<TAB>0xVA[<TAB>size]` (a transfer_symbols.py TSV works too:
its columns name, donor_va, size, acceptor_vas; only single-address rows are used). The CSV
written has the kit's symbols schema (binary,name,va,size,type,bind,shndx) plus `refs`
(how many sites agreed) and `tu`; statics carry their TU so a build can scope them.
--gp: the original's $gp (needed for GPREL16/LITERAL); default: the ELF's .reginfo.

port_check.py runs this on its identical functions (--harvest).
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import struct
import sys
from pathlib import Path

E = "<"
R_MIPS_32, R_MIPS_26, R_MIPS_HI16, R_MIPS_LO16, R_MIPS_GPREL16, R_MIPS_LITERAL = 2, 4, 5, 6, 7, 8
STT_NOTYPE, STT_OBJECT, STT_FUNC, STT_SECTION = 0, 1, 2, 3
STB_LOCAL = 0
SHT_REL = 9
FIELD_MASK = {R_MIPS_26: 0xFC000000, R_MIPS_HI16: 0xFFFF0000, R_MIPS_LO16: 0xFFFF0000,
              R_MIPS_GPREL16: 0xFFFF0000, R_MIPS_LITERAL: 0xFFFF0000}


def sext16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class ObjFile:
    """A relocatable EE object: sections, symbols, .rel.text, defined functions."""

    def __init__(self, path_or_bytes):
        d = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else Path(path_or_bytes).read_bytes()
        if d[:4] != b"\x7fELF":
            raise ValueError("not an ELF")
        self.raw = bytes(d)
        shoff, = struct.unpack_from(E + "I", d, 0x20)
        shent, shnum, shstr = struct.unpack_from(E + "HHH", d, 0x2E)
        keys = ("nameoff", "typ", "flags", "addr", "off", "size", "link", "info", "align", "entsz")
        self.sections = [dict(zip(keys, struct.unpack_from(E + "10I", d, shoff + i * shent))) for i in range(shnum)]
        for s in self.sections:
            b = self.sections[shstr]["off"] + s["nameoff"]
            s["name"] = d[b:d.index(b"\0", b)].decode()
        self.index = {s["name"]: i for i, s in enumerate(self.sections)}
        st = next(s for s in self.sections if s["name"] == ".symtab")
        strt = self.sections[st["link"]]
        self.symbols = []
        for k in range(st["size"] // 16):
            no, val, size, info, _oth, shndx = struct.unpack_from(E + "IIIBBH", d, st["off"] + k * 16)
            b = strt["off"] + no
            self.symbols.append(dict(name=d[b:d.index(b"\0", b)].decode(), val=val, size=size,
                                     typ=info & 0xF, bind=info >> 4, shndx=shndx))
        self.text_idx = self.index.get(".text")
        t = self.sections[self.text_idx] if self.text_idx is not None else dict(off=0, size=0)
        self.text = d[t["off"]:t["off"] + t["size"]]
        self.relocs = self.rel_of(".text")
        self.funcs = {s["name"]: s for s in self.symbols
                      if s["typ"] == STT_FUNC and s["shndx"] == self.text_idx and s["name"]}

    def rel_of(self, secname):
        rel = next((s for s in self.sections if s["typ"] == SHT_REL and s["name"] == ".rel" + secname), None)
        if rel is None:
            return []
        out = []
        for k in range(rel["size"] // 8):
            off, info = struct.unpack_from(E + "II", self.raw, rel["off"] + k * 8)
            out.append((off, info & 0xFF, info >> 8))
        return out

    def word(self, off):
        return struct.unpack_from(E + "I", self.text, off)[0]

    def gp0(self):
        reg = self.index.get(".reginfo")
        if reg is None or self.sections[reg]["size"] < 24:
            return 0
        return struct.unpack_from(E + "I", self.raw, self.sections[reg]["off"] + 20)[0]


class Image:
    """A retail ELF's loaded bytes by VA, and its $gp from .reginfo."""

    def __init__(self, path_or_bytes):
        d = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else Path(path_or_bytes).read_bytes()
        self.raw = bytes(d)
        phoff, = struct.unpack_from(E + "I", d, 0x1C)
        ent, num = struct.unpack_from(E + "HH", d, 0x2A)
        self.segments = []
        self.gp = None
        for k in range(num):
            typ, off, va, _pa, filesz, memsz = struct.unpack_from(E + "6I", d, phoff + k * ent)
            if typ == 1:
                self.segments.append((va, off, filesz, memsz))
            elif typ == 0x70000000 and filesz >= 24:                 # PT_MIPS_REGINFO
                self.gp = struct.unpack_from(E + "I", d, off + 20)[0]

    def read(self, va, n):
        for sva, soff, size, _m in self.segments:
            if sva <= va and va + n <= sva + size:
                return self.raw[soff + va - sva: soff + va - sva + n]
        return None


def masked_compare(obj: ObjFile, fsym: dict, retail: bytes):
    """Words differing once each relocated field is masked, and the relocation types met that
    have no field mask (reported, and counted as a difference: the word cannot be judged).
    [] means identical modulo relocations."""
    rel = {o: t for o, t, _s in obj.relocs}
    diffs, unknown = [], set()
    for i in range(0, fsym["size"], 4):
        a = struct.unpack_from(E + "I", obj.text, fsym["val"] + i)[0]
        b = struct.unpack_from(E + "I", retail, i)[0]
        t = rel.get(fsym["val"] + i)
        m = 0xFFFFFFFF if t is None else FIELD_MASK.get(t, 0)
        if t is not None and t not in FIELD_MASK:
            unknown.add(t)
        if (a & m) != (b & m) or (t is not None and t not in FIELD_MASK):
            diffs.append((i, a, b, t))       # a relocation with no known field cannot be judged: a difference
    return diffs, unknown


def covering(obj: ObjFile, shndx, offset):
    """The named object-file symbol whose extent covers SHNDX+OFFSET, else None."""
    named = sorted(((s["val"], s) for s in obj.symbols
                    if s["shndx"] == shndx and s["name"] and s["typ"] in (STT_OBJECT, STT_FUNC, STT_NOTYPE)
                    and not s["name"].startswith("$") and "_compiled" not in s["name"]),
                   key=lambda vs: (vs[0], vs[1]["typ"] == STT_NOTYPE, vs[1]["name"]))
    best = None
    for k, (val, s) in enumerate(named):
        if val > offset:
            break
        end = val + s["size"] if s["size"] else (named[k + 1][0] if k + 1 < len(named) else val + (1 << 30))
        if offset < max(end, val + 1):
            best = s
    return best


def harvest_function(obj: ObjFile, tu: str, fname: str, va: int, image: Image, gp=None):
    """[(key, address, kind)] read from one function's relocated fields. key is
    ('G', name) for a global or undefined name, ('L', tu, name) for a named static,
    ('S', tu, section) for a section base, ('T', tu, offset) for a TU-local text label."""
    f = obj.funcs[fname]
    retail = image.read(va, f["size"])
    out = []
    rel = obj.relocs
    gp = gp if gp is not None else image.gp
    gp0 = obj.gp0()
    for i, (off, rt, si) in enumerate(rel):
        if not (f["val"] <= off < f["val"] + f["size"]):
            continue
        sym = obj.symbols[si]
        site_va = va + off - f["val"]
        W = struct.unpack_from(E + "I", retail, off - f["val"])[0]
        w = obj.word(off)
        is_sec = sym["typ"] == STT_SECTION
        secname = obj.sections[sym["shndx"]]["name"] if 0 < sym["shndx"] < len(obj.sections) else None
        if rt == R_MIPS_26:
            target = (site_va & 0xF0000000) | ((W & 0x3FFFFFF) << 2)
            addend = (w & 0x3FFFFFF) << 2
            if is_sec and secname == ".text":
                label = covering(obj, obj.text_idx, addend)
                if label is not None and label["val"] == addend:
                    out.append((("L" if label["bind"] == STB_LOCAL else "G", tu, label["name"])
                                if label["bind"] == STB_LOCAL else ("G", label["name"]), target, "func"))
                out.append((("T", tu, addend), target, "text"))
            elif not is_sec:
                out.append((("G", sym["name"]) if sym["bind"] != STB_LOCAL else ("L", tu, sym["name"]),
                            target - addend, "func"))
        elif rt == R_MIPS_LO16:
            # every %lo with its governing %hi: the nearest HI16 before it against the same
            # symbol, by instruction offset (the -g assembler does not keep HI16 next to its
            # LO16 in the table; one %hi may feed several %lo)
            his = [o2 for o2, t2, s2 in rel if s2 == si and t2 == R_MIPS_HI16 and f["val"] <= o2 < off]
            if not his:
                continue
            rs = (w >> 21) & 31                      # the %hi that loaded this %lo's base register
            hoff = max([h for h in his if (obj.word(h) >> 16) & 31 == rs] or his)
            Wh = struct.unpack_from(E + "I", retail, hoff - f["val"])[0]
            V = (((Wh & 0xFFFF) << 16) + sext16(W)) & 0xFFFFFFFF
            AHL = ((obj.word(hoff) & 0xFFFF) << 16) + sext16(w)
            out += section_or_symbol(obj, tu, sym, secname, AHL, V)
        elif rt in (R_MIPS_GPREL16, R_MIPS_LITERAL):
            if not gp:
                continue
            V = (gp + sext16(W)) & 0xFFFFFFFF
            A = sext16(w) + (gp0 if is_sec else 0)
            out += section_or_symbol(obj, tu, sym, secname, A, V)
    return out


def section_or_symbol(obj, tu, sym, secname, addend, V):
    if sym["typ"] != STT_SECTION:
        key = ("G", sym["name"]) if sym["bind"] != STB_LOCAL else ("L", tu, sym["name"])
        return [(key, (V - addend) & 0xFFFFFFFF, "data")]
    res = [(("S", tu, secname), (V - addend) & 0xFFFFFFFF, "section")]
    label = covering(obj, sym["shndx"], addend)
    if label is not None and not label["name"].startswith("$"):
        res.append((("L", tu, label["name"]) if label["bind"] == STB_LOCAL else ("G", label["name"]),
                    (V - (addend - label["val"])) & 0xFFFFFFFF, "data"))
    return res


def immediate_only(diffs):
    """Every differing word an I-type instruction whose opcode and registers agree. Branches
    and jumps are not immediates: a different target is different control flow."""
    return all(a != b and (a & 0xFFFF0000) == (b & 0xFFFF0000) and (a >> 26) not in (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17, 0x1C, 0x10, 0x11, 0x12, 0x13)
               for _i, a, b, _t in diffs)


def harvest(units, image: Image, gp=None, accept="identical"):
    """units: [(tu, obj_path_or_ObjFile, {fname: va})]. Returns a dict with `symbols`
    {key: Counter(address)}, `checked` per function verdicts and `unknown_relocs`.
    accept="immediate" also reads functions whose only differences are I-type immediates
    outside the relocated fields (a moved struct field): their relocated fields still
    name the same symbols, and every site is counted, so a disagreement still shows."""
    found = collections.defaultdict(collections.Counter)
    kinds, checked = {}, []
    for tu, obj, placed in units:
        obj = obj if isinstance(obj, ObjFile) else ObjFile(obj)
        for fname, va in placed.items():
            if fname not in obj.funcs:
                checked.append(dict(tu=tu, func=fname, harvested=False, why="not defined by the object"))
                continue
            f = obj.funcs[fname]
            retail = image.read(va, f["size"])
            if retail is None:
                checked.append(dict(tu=tu, func=fname, harvested=False, why="address outside the image"))
                continue
            diffs, unknown = masked_compare(obj, f, retail)
            if diffs and not (accept == "immediate" and immediate_only(diffs)):
                checked.append(dict(tu=tu, func=fname, harvested=False, why=f"{len(diffs)} word(s) differ"))
                continue
            for key, addr, kind in harvest_function(obj, tu, fname, va, image, gp):
                found[key][addr] += 1
                kinds[key] = kind
            checked.append(dict(tu=tu, func=fname, harvested=True, unknown_relocs=sorted(unknown),
                                class_="identical" if not diffs else "immediate"))
    return dict(symbols=found, kinds=kinds, checked=checked)


def summarize(result):
    syms = result["symbols"]
    conflicts = {k: dict(v) for k, v in syms.items() if len(v) > 1}
    named = {k: v for k, v in syms.items() if k[0] in ("G", "L")}
    return dict(names=len(named), single=sum(1 for v in named.values() if len(v) == 1),
                checked=sum(1 for v in named.values() if len(v) == 1 and sum(v.values()) > 1),
                conflicts=len([k for k in conflicts if k[0] in ("G", "L")]),
                sections=len([k for k in syms if k[0] == "S"]),
                section_conflicts=len([k for k in conflicts if k[0] == "S"]),
                functions_read=sum(1 for c in result["checked"] if c["harvested"]),
                functions_refused=sum(1 for c in result["checked"] if not c["harvested"]))


def write_csv(result, path, binary_name, sizes=None):
    sizes = sizes or {}
    rows = 0
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["binary", "name", "va", "size", "type", "bind", "shndx", "refs", "tu"])
        for key, cnt in sorted(result["symbols"].items(), key=lambda kv: str(kv[0])):
            if key[0] not in ("G", "L") or len(cnt) != 1:
                continue
            addr, n = next(iter(cnt.items()))
            name = key[1] if key[0] == "G" else key[2]
            tu = "" if key[0] == "G" else key[1]
            typ = "FUNC" if result["kinds"].get(key) == "func" else "OBJECT"
            w.writerow([binary_name, name, f"{addr:#x}", sizes.get(name, 0), typ,
                        "GLOBAL" if key[0] == "G" else "LOCAL", 1, n, tu])
            rows += 1
    return rows


def load_placements(path):
    out = {}
    with open(path) as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 2 or p[0] in ("name", "") or p[0].startswith("#"):
                continue
            if len(p) >= 4 and p[1].startswith("0x") and (p[3].startswith("0x") or not p[3]):
                vas = p[3]                                     # transfer_symbols.py TSV
                if not vas or "," in vas or (len(p) >= 8 and p[7]):
                    continue
                out[p[0]] = int(vas, 16)
            elif p[1].startswith("0x"):
                out[p[0]] = int(p[1], 16)
    return out


# ---------------------------------------------------------------- self-test

def _elf_rel(text_words, relocs, syms):
    """A minimal little-endian MIPS REL object: .text, .rel.text, .symtab, .strtab,
    .shstrtab. syms: [(name, value, size, typ, bind, shndx)] after the null symbol."""
    text = b"".join(struct.pack(E + "I", x) for x in text_words)
    strtab = b"\0"
    symtab = b"\0" * 16
    for name, val, size, typ, bind, shndx in syms:
        no = len(strtab)
        strtab += name.encode() + b"\0"
        symtab += struct.pack(E + "IIIBBH", no, val, size, (bind << 4) | typ, 0, shndx)
    rel = b"".join(struct.pack(E + "II", off, (si << 8) | t) for off, t, si in relocs)
    names = [b"", b".text", b".rel.text", b".symtab", b".strtab", b".shstrtab"]
    shstr = b"\0"
    nameoff = [0]
    for n in names[1:]:
        nameoff.append(len(shstr))
        shstr += n + b"\0"
    body = [text, rel, symtab, strtab, shstr]
    off = 0x34
    offs = []
    blob = b""
    for b in body:
        offs.append(off + len(blob))
        blob += b + b"\0" * (-len(b) % 4)
    shoff = 0x34 + len(blob)
    hdr = bytearray(0x34)
    hdr[:16] = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    struct.pack_into(E + "HHIIIIIHHHHHH", hdr, 16, 1, 8, 1, 0, 0, shoff, 0, 0x34, 0, 0, 40, 6, 5)
    sh = b"\0" * 40
    specs = [(1, 0, len(text), 0, 0), (SHT_REL, 1, len(rel), 3, 1), (2, 2, len(symtab), 4, 1),
             (3, 3, len(strtab), 0, 0), (3, 4, len(shstr), 0, 0)]
    for k, (typ, bi, size, link, info) in enumerate(specs):
        sh += struct.pack(E + "10I", nameoff[k + 1], typ, 0, 0, offs[bi], size, link, info, 4, 16 if typ == 2 else 0)
    return bytes(hdr) + blob + sh


def _elf_exec(va, payload, gp=None):
    """A minimal ET_EXEC with one PT_LOAD at file offset 0x100 (and a PT_MIPS_REGINFO)."""
    n = 2 if gp is not None else 1
    hdr = bytearray(0x100)
    hdr[:16] = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    struct.pack_into(E + "HHIIIIIHHHHHH", hdr, 16, 2, 8, 1, va, 0x34, 0, 0, 0x34, 32, n, 40, 0, 0)
    struct.pack_into(E + "8I", hdr, 0x34, 1, 0x100, va, va, len(payload), len(payload), 5, 0x10)
    tail = b""
    if gp is not None:
        reginfo = struct.pack(E + "6I", 0, 0, 0, 0, 0, gp)
        struct.pack_into(E + "8I", hdr, 0x54, 0x70000000, 0x100 + len(payload), 0, 0, 24, 24, 4, 4)
        tail = reginfo
    return bytes(hdr) + payload + tail


def self_test() -> int:
    checks = {}
    # object: f() { gvar via %hi/%lo; jal func; sdat via $gp; jr ra }
    words = [0x3C040000, 0x24840000, 0x0C000000, 0x00000000, 0x8F820000, 0x03E00008, 0x00000000]
    syms = [("f", 0, 28, STT_FUNC, 1, 1), ("gvar", 0, 0, STT_NOTYPE, 1, 0),
            ("func", 0, 0, STT_NOTYPE, 1, 0), ("sdat", 0, 0, STT_NOTYPE, 1, 0)]
    relocs = [(0, R_MIPS_HI16, 2), (4, R_MIPS_LO16, 2), (8, R_MIPS_26, 3), (16, R_MIPS_GPREL16, 4)]
    obj = ObjFile(_elf_rel(words, relocs, syms))
    gvar, func, gp, sdat = 0x00456780, 0x00123450, 0x00500000, 0x004FFFF0
    retail = [0x3C040045, 0x24846780, 0x0C000000 | (func >> 2), 0, 0x8F820000 | (sdat - gp) & 0xFFFF, 0x03E00008, 0]
    img = Image(_elf_exec(0x100000, b"".join(struct.pack(E + "I", x) for x in retail), gp=gp))
    checks["image-gp"] = img.gp == gp
    diffs, unknown = masked_compare(obj, obj.funcs["f"], img.read(0x100000, 28))
    checks["masked-identical"] = diffs == [] and unknown == set()
    r = harvest([("tu1", obj, {"f": 0x100000})], img)
    s = r["symbols"]
    checks["harvest-hilo"] = dict(s[("G", "gvar")]) == {gvar: 1}
    checks["harvest-jal"] = dict(s[("G", "func")]) == {func: 1}
    checks["harvest-gprel"] = dict(s[("G", "sdat")]) == {sdat: 1}
    # a differing opcode refuses the function: nothing is read from it
    bad = list(retail)
    bad[5] = 0x03E00009
    img2 = Image(_elf_exec(0x100000, b"".join(struct.pack(E + "I", x) for x in bad), gp=gp))
    r2 = harvest([("tu1", obj, {"f": 0x100000})], img2)
    checks["refuse-different"] = not r2["symbols"] and r2["checked"][0]["harvested"] is False
    # two sites disagreeing on a name is a conflict, reported not resolved
    img3 = Image(_elf_exec(0x100000, b"".join(struct.pack(E + "I", x) for x in retail * 2), gp=gp))
    retail_b = list(retail)
    retail_b[1] = 0x24846790                       # gvar + 0x10 at the second copy
    img3 = Image(_elf_exec(0x100000, b"".join(struct.pack(E + "I", x) for x in retail + retail_b), gp=gp))
    r3 = harvest([("tu1", obj, {"f": 0x100000}), ("tu2", obj, {"f": 0x10001C})], img3)
    sm = summarize(r3)
    checks["conflict"] = len(r3["symbols"][("G", "gvar")]) == 2 and sm["conflicts"] == 1 and sm["checked"] == 2
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        n = write_csv(r, Path(tmp) / "h.csv", "GAME.ELF")
        rows = list(csv.DictReader(open(Path(tmp) / "h.csv")))
        checks["csv"] = n == 3 and {x["name"]: x["va"] for x in rows} == {"gvar": hex(gvar), "func": hex(func),
                                                                         "sdat": hex(sdat)}
        (Path(tmp) / "p.tsv").write_text("name\tdonor_va\tsize\tacceptor_vas\tidentical\ttotal\tconfidence\tambiguous\n"
                                         "a\t0x10\t8\t0x20\t2\t2\thigh\t\nb\t0x30\t8\t0x40,0x50\t2\t2\thigh\t\n")
        checks["placements-transfer-tsv"] = load_placements(Path(tmp) / "p.tsv") == {"a": 0x20}
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--obj", action="append", default=[], help="a compiled object (repeatable); TU id = its stem")
    ap.add_argument("--binary", type=Path, help="the original ELF")
    ap.add_argument("--placements", type=Path, help="name<TAB>0xVA TSV, or a transfer_symbols.py TSV")
    ap.add_argument("--gp", help="the original's $gp (default: its .reginfo)")
    ap.add_argument("--accept", choices=("identical", "immediate"), default="identical",
                    help="also read immediate-only functions (default: identical only)")
    ap.add_argument("-o", "--out", type=Path, help="harvested symbols CSV")
    ap.add_argument("--report", type=Path, help="JSON report (conflicts, per-function verdicts)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.obj and a.binary and a.placements):
        ap.error("--obj, --binary and --placements are required")
    img = Image(a.binary)
    placed = load_placements(a.placements)
    units = []
    for o in a.obj:
        obj = ObjFile(o)
        units.append((Path(o).stem, obj, {n: placed[n] for n in obj.funcs if n in placed}))
    res = harvest(units, img, int(a.gp, 16) if a.gp else None, a.accept)
    sm = summarize(res)
    print(json.dumps(sm))
    if a.out:
        print(f"wrote {write_csv(res, a.out, a.binary.name)} rows to {a.out}")
    if a.report:
        a.report.write_text(json.dumps(dict(summary=sm, conflicts={str(k): {hex(x): n for x, n in v.items()}
                                                                    for k, v in res["symbols"].items() if len(v) > 1},
                                            checked=res["checked"]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
