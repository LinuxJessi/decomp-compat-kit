#!/usr/bin/env python3
"""Turn Sony's SDK static libraries into a symbol donor for transfer_symbols.py.

The PS2 Runtime Library discs (DTL-S12000 Release 1.0, DTL-S12010 Release 2.0,
and later ones) ship `sce/ee/lib/*.a`: unstripped relocatable objects for every
`sce*` function the games link. A retail binary carries those objects verbatim
apart from the relocated fields, which is exactly what transfer_symbols.py
masks, so the libraries name the SDK minority of any stripped PS2 executable,
whatever compiler built the game itself (the libraries are ee-gcc code even in a
CodeWarrior title).

transfer_symbols.py wants an ELF with program headers and a symbols.csv from
dump_symbols.py. This tool builds both from one or more directories of `.a`
files: every executable PROGBITS section of every member is laid out in one
executable PT_LOAD at --base (16-byte aligned, relocations left as the
assembler wrote them, which the masking ignores), and every FUNC symbol defined
in those sections becomes a CSV row. GNU `ar` long names (`//` table) and the
`name/` convention are handled; members that are not ELF are skipped.

Measured (2026-10-31, SDK 2.0 libraries, --mask strict, high confidence, one
hit): 58 names in Final Fantasy X (SLUS_203.12), 85 in Silent Hill 2
(SLUS_202.28, a CodeWarrior title), 49 in Xenosaga I (SLUS_204.69, where the
retail symbol table confirms them). docs/SDK-ARCHIVE.md has the details.

Usage:
  ./sdk_donor.py --out-elf sdk20.elf --out-csv sdk20.csv sce/ee/lib [more/lib ...]
  ./transfer_symbols.py --donor sdk20.elf --donor-symbols sdk20.csv --acceptor SLUS_xxx -o names.tsv
  ./sdk_donor.py --self-test

Exit: 0 ok, 1 self-test failed, 2 usage / no objects found.
"""
from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
from pathlib import Path

E = "<"
SHT_PROGBITS, SHT_SYMTAB = 1, 2
SHF_EXECINSTR = 4
STT_FUNC = 2
BINDS = {0: "LOCAL", 1: "GLOBAL", 2: "WEAK"}


def ar_members(d: bytes) -> list[tuple[str, bytes]]:
    """(name, body) of every member of a GNU/System V ar archive; [] if not one."""
    if d[:8] != b"!<arch>\n":
        return []
    o, names, out = 8, None, []
    while o + 60 <= len(d):
        h = d[o:o + 60]
        try:
            name = h[:16].decode("latin1").rstrip()
            size = int(h[48:58])
        except ValueError:
            break
        body = d[o + 60:o + 60 + size]
        if name == "//":
            names = body
        elif name.startswith("/") and name[1:].strip().isdigit() and names is not None:
            k = int(name[1:])
            end = names.find(b"\n", k)
            name = names[k:end if end >= 0 else None].decode("latin1").rstrip("/")
            out.append((name, body))
        elif name not in ("/", "/SYM64/"):
            out.append((name.rstrip("/"), body))
        o += 60 + size + (size & 1)
    return out


def object_text_and_funcs(d: bytes):
    """({section index: (offset, size)} of executable PROGBITS sections,
    [(name, section index, value, size, bind)] of FUNC symbols defined in them)."""
    if len(d) < 52 or d[:4] != b"\x7fELF" or d[4] != 1 or d[5] != 1:
        return {}, []
    shoff, = struct.unpack_from(E + "I", d, 0x20)
    shent, shnum = struct.unpack_from(E + "HH", d, 0x2E)
    if not shoff or shoff + shnum * shent > len(d):
        return {}, []
    secs = [struct.unpack_from(E + "10I", d, shoff + i * shent) for i in range(shnum)]
    texts = {i: (s[4], s[5]) for i, s in enumerate(secs) if s[1] == SHT_PROGBITS and s[2] & SHF_EXECINSTR and s[5]}
    funcs = []
    for s in secs:
        if s[1] != SHT_SYMTAB or s[6] >= len(secs):
            continue
        strtab = secs[s[6]][4]
        for k in range(s[5] // 16):
            no, val, size, info, _other, shn = struct.unpack_from(E + "IIIBBH", d, s[4] + k * 16)
            if info & 0xF == STT_FUNC and shn in texts and size:
                end = d.find(b"\0", strtab + no)
                funcs.append((d[strtab + no:end].decode("latin1"), shn, val, size, info >> 4))
    return texts, funcs


def build(lib_dirs: list[Path], base: int, binary_name: str):
    """(elf bytes, csv rows, stats)"""
    img, rows = bytearray(), []
    stats = dict(archives=0, objects=0, skipped=0)
    for root in lib_dirs:
        for f in sorted(root.glob("*.a")):
            members = ar_members(f.read_bytes())
            if not members:
                continue
            stats["archives"] += 1
            for name, body in members:
                texts, funcs = object_text_and_funcs(body)
                if not texts:
                    stats["skipped"] += 1
                    continue
                stats["objects"] += 1
                placed = {}
                for idx, (off, size) in texts.items():
                    while len(img) % 16:
                        img.append(0)
                    placed[idx] = base + len(img)
                    img += body[off:off + size]
                for n, shn, val, size, bind in funcs:
                    rows.append(dict(binary=binary_name, name=n, va=f"0x{placed[shn] + val:x}", size=size,
                                     type="FUNC", bind=BINDS.get(bind, "LOCAL"), shndx=1))
    hdr = bytearray(b"\x7fELF\x01\x01\x01" + b"\0" * 9)
    hdr += struct.pack(E + "HHIIIIIHHHHHH", 2, 8, 1, base, 52, 0, 0x20924001, 52, 32, 1, 40, 0, 0)
    hdr += struct.pack(E + "8I", 1, 0x1000, base, base, len(img), len(img), 5, 16)
    elf = bytes(hdr) + b"\0" * (0x1000 - len(hdr)) + bytes(img)
    stats["text_bytes"], stats["functions"] = len(img), len(rows)
    return elf, rows, stats


def write_csv(path: Path, rows: list):
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["binary", "name", "va", "size", "type", "bind", "shndx"])
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- self-test


def tiny_object(name: str, code: bytes) -> bytes:
    """A relocatable ELF with .text = code and one global FUNC `name` covering it."""
    shstr = b"\0.text\0.symtab\0.strtab\0.shstrtab\0"
    strtab = b"\0" + name.encode() + b"\0"
    symtab = b"\0" * 16 + struct.pack(E + "IIIBBH", 1, 0, len(code), (1 << 4) | STT_FUNC, 0, 1)
    blobs = [code, symtab, strtab, shstr]
    offs, body = [], bytearray()
    for b in blobs:
        offs.append(0x40 + len(body))
        body += b + b"\0" * (-len(b) % 4)
    shdrs = [struct.pack(E + "10I", *([0] * 10)),
             struct.pack(E + "10I", 1, SHT_PROGBITS, 6, 0, offs[0], len(code), 0, 0, 16, 0),
             struct.pack(E + "10I", 7, SHT_SYMTAB, 0, 0, offs[1], len(symtab), 3, 1, 4, 16),
             struct.pack(E + "10I", 15, 3, 0, 0, offs[2], len(strtab), 0, 0, 1, 0),
             struct.pack(E + "10I", 23, 3, 0, 0, offs[3], len(shstr), 0, 0, 1, 0)]
    shoff = 0x40 + len(body)
    hdr = bytearray(b"\x7fELF\x01\x01\x01" + b"\0" * 9)
    hdr += struct.pack(E + "HHIIIIIHHHHHH", 1, 8, 1, 0, 0, shoff, 0, 52, 0, 0, 40, 5, 4)
    return bytes(hdr) + b"\0" * (0x40 - len(hdr)) + bytes(body) + b"".join(shdrs)


def tiny_archive(members: list[tuple[str, bytes]]) -> bytes:
    """A GNU ar archive with a `//` long-name table (every name goes through it)."""
    names = b"".join(n.encode() + b"/\n" for n, _ in members)
    out = bytearray(b"!<arch>\n")
    def header(name, size):
        return (f"{name:<16}{'0':<12}{'0':<6}{'0':<6}{'100644':<8}{size:<10}`\n").encode()
    out += header("//", len(names)) + names + (b"\n" if len(names) & 1 else b"")
    pos = 0
    for n, body in members:
        out += header(f"/{pos}", len(body)) + body + (b"\n" if len(body) & 1 else b"")
        pos += len(n) + 2
    return bytes(out)


def self_test() -> int:
    import tempfile
    checks = {}
    code_a = struct.pack(E + "4I", 0x27BDFFF0, 0xFFBF0000, 0x0C000000, 0x00000000)   # addiu sp; sd ra; jal 0; nop
    code_b = struct.pack(E + "3I", 0x03E00008, 0x24020001, 0x00000000)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "libtest.a").write_bytes(tiny_archive([("a_long_object_name.o", tiny_object("sceTestA", code_a)),
                                                    ("b.o", tiny_object("sceTestB", code_b)),
                                                    ("notes.txt", b"not an object\n")]))
        elf, rows, stats = build([d], 0x1000000, "sdk.elf")
        checks["members"] = stats == dict(archives=1, objects=2, skipped=1, text_bytes=16 + len(code_b), functions=2)
        checks["rows"] = [(r["name"], r["va"], r["size"]) for r in rows] == [("sceTestA", "0x1000000", 16), ("sceTestB", "0x1000010", 12)]
        checks["elf"] = elf[:4] == b"\x7fELF" and elf[0x1000:0x1010] == code_a and elf[0x1010:0x101C] == code_b
        ph = struct.unpack_from(E + "8I", elf, 52)
        checks["phdr"] = ph[0] == 1 and ph[2] == 0x1000000 and ph[4] == 16 + len(code_b) and ph[6] == 5
        write_csv(d / "s.csv", rows)
        checks["csv"] = list(csv.DictReader((d / "s.csv").open()))[1]["name"] == "sceTestB"
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lib_dirs", nargs="*", type=Path, help="directories holding the SDK's *.a archives")
    ap.add_argument("--out-elf", type=Path, help="the donor ELF to write")
    ap.add_argument("--out-csv", type=Path, help="the donor symbols.csv to write")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0x1000000, help="address of the donor's text (default 0x1000000)")
    ap.add_argument("--binary-name", help="`binary` column of the CSV (default: the ELF's file name)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.lib_dirs or not args.out_elf or not args.out_csv:
        ap.error("lib_dirs, --out-elf and --out-csv are required (or --self-test)")
    elf, rows, stats = build(args.lib_dirs, args.base, args.binary_name or args.out_elf.name)
    if not rows:
        print("no FUNC symbols found in any archive", file=sys.stderr)
        return 2
    args.out_elf.write_bytes(elf)
    write_csv(args.out_csv, rows)
    print(json.dumps(dict(elf=str(args.out_elf), csv=str(args.out_csv), **stats)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
