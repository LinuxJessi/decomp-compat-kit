"""dump_symbols.py — export the symbols an ELF still carries to CSV.

Two sources, both little-endian 32-bit MIPS ELF (EE executables and overlays, IOP
modules):

  .symtab/.strtab   the ELF symbol table (an unstripped game: Xenosaga I's boot ELF
                    and overlays, most IOP sound drivers)
  .mdebug           the ECOFF symbolic information older GNU toolchains wrote: source
                    file names, procedures, file-local statics and labels. It can
                    survive in a module whose game executable is stripped (Xenosaga
                    II's KORGIVAG.IRX and CRI_ADXI.IRX), and it names statics the
                    .symtab does not attribute to a file.

Binaries are discovered by scanning --code-dir for ELFs that carry either; stripped
ELFs are skipped. By default only the top level is scanned (the EE binaries of an
extracted disc), so IOP modules in a sub-directory do not mix into an EE table;
--recursive takes them too.

Usage:
    python3 dump_symbols.py --code-dir CODE --out symbols.csv
    python3 dump_symbols.py --code-dir CODE --recursive --out all.csv --mdebug-out mdebug.csv
    python3 dump_symbols.py --self-test

symbols.csv columns: binary,name,va,size,type,bind,shndx
  type is FUNC/OBJECT/SECTION/NOTYPE/FILE/other-int; va is hex.
mdebug.csv columns:  binary,file,kind,name,value
  kind is file/proc/static-proc/static/global/label/other:<st>; value is hex (for a
  module, an offset from its load address).
"""
from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
from pathlib import Path

ELF32_LE = b"\x7fELF\x01\x01\x01"                  # class 32, little-endian, version 1: all this reads
SHT_SYMTAB, SHT_NOBITS = 2, 8
SYM_TYPES = {0: "NOTYPE", 1: "OBJECT", 2: "FUNC", 3: "SECTION", 4: "FILE"}
SYM_BINDS = {0: "LOCAL", 1: "GLOBAL", 2: "WEAK"}

# ECOFF symbolic header (HDRR), file descriptor (FDR), local symbol (SYMR), external (EXTR)
MDEBUG_MAGIC = 0x7009
HDRR = struct.Struct("<hH23I")
HDRR_FIELDS = ("ilineMax cbLine cbLineOffset idnMax cbDnOffset ipdMax cbPdOffset isymMax cbSymOffset "
               "ioptMax cbOptOffset iauxMax cbAuxOffset issMax cbSsOffset issExtMax cbSsExtOffset "
               "ifdMax cbFdOffset crfd cbRfdOffset iextMax cbExtOffset").split()
FDR_SIZE, SYMR_SIZE, EXTR_SIZE = 72, 12, 16
ST_KINDS = {1: "global", 2: "static", 5: "label", 6: "proc", 11: "file", 14: "static-proc"}
ST_SKIP = {0, 3, 4, 7, 8, 9, 10, 15}       # nil, param, local, block, end, member, typedef, constant


def read_sections(data: bytes) -> dict[str, tuple[int, int]]:
    """Return {section_name: (file_offset, size)} for a 32-bit LE ELF."""
    (shoff,) = struct.unpack_from("<I", data, 0x20)
    (shentsize,) = struct.unpack_from("<H", data, 0x2E)
    (shnum,) = struct.unpack_from("<H", data, 0x30)
    (shstrndx,) = struct.unpack_from("<H", data, 0x32)
    if not shoff or shentsize != 40 or not shnum or shstrndx >= shnum:
        raise ValueError("no section table")
    (str_off,) = struct.unpack_from("<I", data, shoff + shstrndx * shentsize + 16)
    secs: dict[str, tuple[int, int]] = {}
    for i in range(shnum):
        base = shoff + i * shentsize
        name_off, _typ, _flags, _addr, offset, size = struct.unpack_from(
            "<IIIIII", data, base
        )
        name = data[str_off + name_off : data.index(b"\0", str_off + name_off)]
        secs[name.decode(errors="replace")] = (offset, size)
    return secs


def section_records(data: bytes) -> list[dict]:
    """Every section header as a dict (name, type, offset, size, link), in table order; []
    when DATA is not a readable 32-bit little-endian ELF. Names are looked up inside
    .shstrtab only."""
    if data[:7] != ELF32_LE:
        return []
    try:
        (shoff,) = struct.unpack_from("<I", data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
        if not shoff or shentsize != 40 or not shnum or shstrndx >= shnum:
            return []
        raw = [struct.unpack_from("<10I", data, shoff + i * 40) for i in range(shnum)]
    except struct.error:
        return []
    str_off, str_size = raw[shstrndx][4], raw[shstrndx][5]
    out = []
    for r in raw:
        a = str_off + r[0]
        z = data.find(b"\0", a, str_off + str_size) if r[0] < str_size else -1
        out.append(dict(name=data[a:z].decode(errors="replace") if z >= 0 else "", type=r[1], offset=r[4],
                        size=r[5], link=r[6]))
    return out


def sections_of(data: bytes) -> dict[str, tuple[int, int]]:
    """read_sections, or {} when DATA is not a readable 32-bit LE ELF."""
    if data[:7] != ELF32_LE:
        return {}
    try:
        return read_sections(data)
    except (struct.error, ValueError, IndexError):
        return {}


def has_symbols(data: bytes) -> bool:
    """A symbol table with at least one named entry, or a .mdebug with a valid header."""
    return any(r[0] for r in symtab_rows(data)) or mdebug_header(data) is not None


def discover_binaries(code_dir: Path, recursive: bool = False) -> list[Path]:
    """Files under code_dir that are 32-bit ELFs carrying a .symtab or a .mdebug.

    Top-level only unless RECURSIVE: the EE-side binaries (boot ELF + overlays) live at
    the root of an extracted code directory, while IOP/*.IRX are R3000 modules whose
    symbols would pollute an EE-focused CSV.
    """
    found = []
    for path in sorted(code_dir.rglob("*") if recursive else code_dir.glob("*")):
        if path.is_file() and has_symbols(path.read_bytes()):
            found.append(path)
    return found


def dump_binary(path: Path):
    data = path.read_bytes()
    if data[:4] != b"\x7fELF":
        raise ValueError(f"{path}: not an ELF")
    yield from symtab_rows(data)


def symtab_rows(data: bytes):
    """(name, value, size, type, bind, shndx) for every entry of the ELF symbol table;
    nothing when there is none. The table is the first SHT_SYMTAB section and its strings
    are the section it links to (whatever either is called). A name offset that leaves the
    string table gives an empty name, never an exception: a damaged table is still read."""
    recs = section_records(data)
    tab = next((r for r in recs if r["type"] == SHT_SYMTAB and r["size"] >= 16), None)
    if tab is None or tab["link"] >= len(recs) or tab["offset"] + tab["size"] > len(data):
        return
    strs = recs[tab["link"]]
    s0, s1 = strs["offset"], min(strs["offset"] + strs["size"], len(data))
    for i in range(tab["size"] // 16):
        name_off, val, size, info, _other, shndx = struct.unpack_from("<IIIBBH", data, tab["offset"] + i * 16)
        z = data.find(b"\0", s0 + name_off, s1) if name_off < strs["size"] else -1
        yield (
            data[s0 + name_off:z].decode(errors="replace") if z >= 0 else "",
            val,
            size,
            SYM_TYPES.get(info & 0xF, str(info & 0xF)),
            SYM_BINDS.get(info >> 4, str(info >> 4)),
            shndx,
        )


def mdebug_header(data: bytes):
    """The ECOFF symbolic header of DATA's .mdebug as a dict, or None. Table offsets in
    it are file offsets (from the start of the ELF), as the format defines them."""
    off, size = sections_of(data).get(".mdebug", (0, 0))
    if size < HDRR.size or off + HDRR.size > len(data):
        return None
    f = HDRR.unpack_from(data, off)
    if (f[0] & 0xFFFF) != MDEBUG_MAGIC:
        return None
    return dict(zip(["magic", "vstamp"] + HDRR_FIELDS, f))


def _cstr(data: bytes, base: int, size: int, off: int) -> str:
    """The NUL-terminated string at OFF inside the string table [BASE, BASE+SIZE)."""
    z = data.find(b"\0", base + off, min(base + size, len(data))) if 0 <= off < size else -1
    if z < 0:
        raise ValueError(f".mdebug string offset {off} outside its {size}-byte table")
    return data[base + off:z].decode("latin1")


def mdebug_rows(data: bytes):
    """(file, kind, name, value) from .mdebug: one `file` row per file descriptor, then
    its procedures, statics and labels in table order, then the external symbols (file
    ""). Scope-only records (parameters, locals, blocks, ends, members) are left out.
    Raises ValueError on a table that runs outside the file: nothing is guessed."""
    h = mdebug_header(data)
    if h is None:
        return

    def table(off, count, size, what):
        if count and not (0 < off and off + count * size <= len(data)):
            raise ValueError(f".mdebug {what} table runs outside the file")
        return off

    fd0 = table(h["cbFdOffset"], h["ifdMax"], FDR_SIZE, "file")
    sy0 = table(h["cbSymOffset"], h["isymMax"], SYMR_SIZE, "symbol")
    ex0 = table(h["cbExtOffset"], h["iextMax"], EXTR_SIZE, "external")
    for k in range(h["ifdMax"]):
        adr, rss, iss_base, _cb_ss, isym_base, csym = struct.unpack_from("<Iiiiii", data, fd0 + FDR_SIZE * k)
        if csym < 0 or not 0 <= iss_base <= h["issMax"]:
            raise ValueError(f".mdebug file {k}: bad symbol count or string base")
        ss0, ssn = h["cbSsOffset"] + iss_base, h["issMax"] - iss_base
        fname = _cstr(data, ss0, ssn, rss) if rss >= 0 else ""
        yield fname, "file", fname, adr
        if isym_base < 0 or isym_base + csym > h["isymMax"]:
            raise ValueError(f".mdebug file {k}: symbols {isym_base}+{csym} outside the table")
        for j in range(csym):
            iss, value, bits = struct.unpack_from("<iII", data, sy0 + SYMR_SIZE * (isym_base + j))
            st = bits & 0x3F
            if st in ST_SKIP or st == 11:
                continue
            yield fname, ST_KINDS.get(st, f"other:{st}"), _cstr(data, ss0, ssn, iss), value
    for k in range(h["iextMax"]):
        iss, value, bits = struct.unpack_from("<iII", data, ex0 + EXTR_SIZE * k + 4)
        st = bits & 0x3F
        yield "", ST_KINDS.get(st, f"other:{st}"), _cstr(data, h["cbSsExtOffset"], h["issExtMax"], iss), value


def self_test() -> int:
    """A synthetic ELF with a .symtab and a two-file .mdebug, built here."""
    checks = {}
    strtab = b"\0f\0g_var\0"
    symtab = b"\0" * 16 + struct.pack("<IIIBBH", 1, 0x100, 8, 0x12, 0, 1) + struct.pack("<IIIBBH", 3, 0x200, 4, 0x01, 0, 2)
    ss = b"a.c\0f\0sLocal\0" + b"b.c\0h\0"            # local strings: file a at 0, file b at 13
    ssext = b"g_var\0"
    syms = b"".join(struct.pack("<iII", iss, val, st) for iss, val, st in (
        (0, 0, 11), (4, 0x100, 6), (0, 0, 8), (6, 0x300, 2), (0, 0, 4),      # a.c: file, proc f, end, static, local
        (0, 0, 11), (4, 0x140, 14)))                                         # b.c: file, static proc h
    exts = struct.pack("<hhiII", 0, 0, 0, 0x200, 1)
    fdr = lambda adr, iss_base, isym_base, csym: struct.pack("<Iiiiii", adr, 0, iss_base, 0, isym_base, csym) + b"\0" * (FDR_SIZE - 24)
    fds = fdr(0x100, 0, 0, 5) + fdr(0x140, 13, 5, 2)
    body = {}
    blob = bytearray(b"\x7fELF" + b"\x01\x01\x01" + b"\0" * 45)       # 52-byte ELF header

    def put(name, payload):
        body[name] = (len(blob), len(payload))
        blob.extend(payload)
    put(".symtab", symtab)
    put(".strtab", strtab)
    md_at = len(blob)
    tail = md_at + HDRR.size
    offs = {}
    for name, payload in (("sym", syms), ("ss", ss), ("ssext", ssext), ("fd", fds), ("ext", exts)):
        offs[name] = tail
        tail += len(payload)
    hdr = dict.fromkeys(HDRR_FIELDS, 0)
    hdr.update(isymMax=7, cbSymOffset=offs["sym"], issMax=len(ss), cbSsOffset=offs["ss"], issExtMax=len(ssext),
               cbSsExtOffset=offs["ssext"], ifdMax=2, cbFdOffset=offs["fd"], iextMax=1, cbExtOffset=offs["ext"])
    put(".mdebug", HDRR.pack(MDEBUG_MAGIC, 0, *[hdr[k] for k in HDRR_FIELDS]) + syms + ss + ssext + fds + exts)
    shstr = b"\0.symtab\0.strtab\0.mdebug\0.shstrtab\0"
    put(".shstrtab", shstr)
    shoff = len(blob)
    blob.extend(b"\0" * 40)
    for name in (".symtab", ".strtab", ".mdebug", ".shstrtab"):
        o, n = body[name]
        typ, link = {".symtab": (SHT_SYMTAB, 2), ".strtab": (3, 0)}.get(name, (1, 0))
        blob.extend(struct.pack("<10I", shstr.index(name.encode() + b"\0"), typ, 0, 0, o, n, link, 0, 0, 0))
    struct.pack_into("<I", blob, 0x20, shoff)
    struct.pack_into("<HHH", blob, 0x2E, 40, 5, 4)
    data = bytes(blob)
    checks["sections"] = set(sections_of(data)) == {"", ".symtab", ".strtab", ".mdebug", ".shstrtab"}
    checks["symtab"] = [r for r in symtab_rows(data) if r[0]] == [("f", 0x100, 8, "FUNC", "GLOBAL", 1),
                                                                   ("g_var", 0x200, 4, "OBJECT", "LOCAL", 2)]
    h = mdebug_header(data)
    checks["mdebug-header"] = h is not None and h["ifdMax"] == 2 and h["isymMax"] == 7 and h["iextMax"] == 1
    checks["mdebug-rows"] = list(mdebug_rows(data)) == [
        ("a.c", "file", "a.c", 0x100), ("a.c", "proc", "f", 0x100), ("a.c", "static", "sLocal", 0x300),
        ("b.c", "file", "b.c", 0x140), ("b.c", "static-proc", "h", 0x140), ("", "global", "g_var", 0x200)]
    checks["has-symbols"] = has_symbols(data) and not has_symbols(b"\x7fELF\x01\x01\x01" + b"\0" * 60)
    broken = bytearray(data)                       # a symbol name offset past the string table: empty, no exception
    struct.pack_into("<I", broken, body[".symtab"][0] + 16, 0x7FFFFFF0)
    checks["bad-name"] = [r[0] for r in symtab_rows(bytes(broken))] == ["", "", "g_var"]
    neg = bytearray(data)                          # a negative string index is refused, not read from earlier bytes
    struct.pack_into("<i", neg, offs["sym"] + SYMR_SIZE, -3)
    try:
        list(mdebug_rows(bytes(neg)))
        checks["mdebug-negative-iss"] = False
    except ValueError:
        checks["mdebug-negative-iss"] = True
    checks["elf64-refused"] = section_records(b"\x7fELF\x02\x01\x01" + data[7:]) == []
    bad = bytearray(data)
    struct.pack_into("<I", bad, md_at + 4 + 4 * HDRR_FIELDS.index("cbFdOffset"), len(data) + 64)
    try:
        list(mdebug_rows(bytes(bad)))
        checks["mdebug-bounds"] = False
    except ValueError:
        checks["mdebug-bounds"] = True
    checks["not-elf"] = sections_of(b"MZ" + b"\0" * 80) == {} and mdebug_header(b"\x7fELF" + b"\0" * 60) is None
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--code-dir", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--recursive", action="store_true", help="scan sub-directories too (IOP modules)")
    ap.add_argument("--mdebug-out", type=Path, help="also write the .mdebug records here")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.code_dir or not args.out:
        ap.error("--code-dir and --out are required")

    rows = mrows = 0
    # top level: the bare file name, as before; with --recursive the path under --code-dir,
    # so IOP/A.IRX and DEMO/IOP/A.IRX stay apart
    label = (lambda q: q.relative_to(args.code_dir).as_posix()) if args.recursive else (lambda q: q.name)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    binaries = discover_binaries(args.code_dir, args.recursive)
    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["binary", "name", "va", "size", "type", "bind", "shndx"])
        for path in binaries:
            for name, va, size, typ, bind, shndx in dump_binary(path):
                if not name:
                    continue
                w.writerow([label(path), name, f"0x{va:x}", size, typ, bind, shndx])
                rows += 1
    print(f"{args.out}: {rows} symbols from {len(binaries)} binaries")
    if args.mdebug_out:
        args.mdebug_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.mdebug_out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["binary", "file", "kind", "name", "value"])
            for path in binaries:
                for fname, kind, name, value in mdebug_rows(path.read_bytes()):
                    w.writerow([label(path), fname, kind, name, f"0x{value:x}"])
                    mrows += 1
        print(f"{args.mdebug_out}: {mrows} .mdebug records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
