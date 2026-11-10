"""find_symbols.py — look through a whole disc image for anything that still names code.

A stripped game is worth one full search before reconstruction starts: symbols survive
in odd places (an IOP sound driver, a demo's second executable, a forgotten build
leftover in slack space). This reads the image byte by byte, so it also sees data no
directory entry points at:

  * every ELF header at any offset, with what its section table carries: real
    `.symtab` entries (FUNC and OBJECT counted; the one-entry table every SCE module
    has is not a find), `.mdebug` (ECOFF, counted through dump_symbols.py), stabs,
    DWARF;
  * text only a toolchain writes: `gcc2_compiled.` markers and GNU ld map-file
    headings, with the file each hit lies in.

What it does not see, so a clean report is not proof of absence:
  * compressed archive members (extract those and scan the result);
  * ELFs that are not 32-bit little-endian (every PS2 EE and IOP binary is);
  * an ELF stored in pieces (a multi-extent or fragmented file): its header is found
    but its tables are read as if the file were contiguous, and it is reported
    `unreadable` rather than guessed at;
  * images with 2352-byte raw sectors (convert to 2048-byte sectors first).
File names come from the ISO 9660 directory only; a file listed only in UDF is still
scanned, and reported as outside every listed file.

Usage:
    python3 find_symbols.py IMAGE [IMAGE ...] [--json report.json]
    python3 find_symbols.py --self-test

Exit status 0 always for a completed scan; the report says what was found.
"""
from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import struct
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECTOR = 2048
ELF_MAGIC = b"\x7fELF\x01\x01\x01"                 # 32-bit, little-endian, version 1
PATTERNS = {
    "gcc2_compiled.": b"gcc2_compiled.",
    "ld map: Memory Configuration": b"Memory Configuration",
    "ld map: Linker script and memory map": b"Linker script and memory map",
    "ld map: Cross Reference Table": b"Cross Reference Table",
}
DEBUG_SECTIONS = (".mdebug", ".stab", ".stabstr", ".debug_info", ".debug_line", ".line")
MAX_ELF = 64 << 20                                 # no PS2 executable is near this; bounds a hostile header
MAX_CANDIDATES = 100000
CHUNK = 32 << 20
OVERLAP = 64
SAMPLES = 200


def _dump_symbols():
    spec = importlib.util.spec_from_file_location("dump_symbols", HERE / "dump_symbols.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def iso_files(fh) -> list:
    """[(start byte, end byte, path)] of every file an ISO 9660 image lists, sorted; []
    when the image has no primary volume descriptor (a raw scan still works)."""
    fh.seek(16 * SECTOR)
    pvd = fh.read(SECTOR)
    if len(pvd) < SECTOR or pvd[:6] != b"\x01CD001":
        return []
    out, seen = [], set()
    root = pvd[156:190]
    stack = [(struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0], "")]
    while stack:
        lba, length, prefix = stack.pop()
        if lba in seen or length > (64 << 20):
            continue
        seen.add(lba)
        fh.seek(lba * SECTOR)
        d = fh.read(length)
        i = 0
        while i < len(d):
            n = d[i]
            if n == 0:                                   # records do not cross sectors
                i = (i // SECTOR + 1) * SECTOR
                continue
            if i + n > len(d) or n < 34:
                break
            elba, esize = struct.unpack_from("<I", d, i + 2)[0], struct.unpack_from("<I", d, i + 10)[0]
            flags, nlen = d[i + 25], d[i + 32]
            name = d[i + 33:i + 33 + nlen]
            i += n
            if name in (b"\0", b"\1"):
                continue
            text = name.decode("latin1").split(";")[0]
            if flags & 2:
                stack.append((elba, esize, prefix + text + "/"))
            else:
                out.append((elba * SECTOR, elba * SECTOR + esize, prefix + text))
    return sorted(out)


def locate(files, starts, off) -> str:
    i = bisect.bisect_right(starts, off) - 1
    if i >= 0 and files[i][0] <= off < files[i][1]:
        return f"{files[i][2]}+{off - files[i][0]:#x}"
    return "(outside every listed file)"


def describe_elf(fh, off, size, ds) -> dict | None:
    """What the ELF at OFF carries, or None when the header does not hold up (a stray
    magic number in data)."""
    fh.seek(off)
    hdr = fh.read(52)
    if len(hdr) < 52:
        return None
    typ, mach = struct.unpack_from("<HH", hdr, 16)
    entry, _phoff, shoff = struct.unpack_from("<III", hdr, 24)
    shentsize, shnum, shstr = struct.unpack_from("<HHH", hdr, 46)
    if mach != 8:                                        # EM_MIPS
        return None
    info = dict(offset=off, type=typ, entry=entry, sections=shnum, symtab=0, funcs=0, objects=0, labels=0,
                debug=[], mdebug=None)
    if not shoff or shentsize != 40 or not 0 < shnum < 4096 or off + shoff + shnum * 40 > size:
        info["note"] = "no usable section table"
        return info
    fh.seek(off + shoff)
    table = fh.read(shnum * 40)
    secs = [struct.unpack_from("<10I", table, k * 40) for k in range(shnum)]
    end = max([shoff + shnum * 40] + [s[4] + s[5] for s in secs if s[1] != 8])
    if end > MAX_ELF or off + end > size:
        info["note"] = "unreadable: its sections lie outside the image or it is larger than this tool reads"
        return info
    fh.seek(off)
    data = fh.read(end)
    try:
        names = ds.sections_of(data)
        rows = [r for r in ds.symtab_rows(data) if r[0]]
        h = ds.mdebug_header(data)
        if h:
            sum(1 for _ in ds.mdebug_rows(data))             # the tables must hold up, not just the header
    except (ValueError, struct.error, IndexError) as e:
        info["note"] = f"unreadable: {e}"
        return info
    # a debug section counts when it holds more than an empty table (a lone NUL, a bare header)
    info["debug"] = [n for n in DEBUG_SECTIONS if names.get(n, (0, 0))[1] > 16 and (n != ".mdebug" or h)]
    info["symtab"] = len(rows)
    info["funcs"] = sum(1 for r in rows if r[3] == "FUNC")
    info["objects"] = sum(1 for r in rows if r[3] == "OBJECT")
    info["labels"] = sum(1 for r in rows if r[3] == "NOTYPE")
    if h:
        info["mdebug"] = dict(files=h["ifdMax"], procedures=h["ipdMax"], local_symbols=h["isymMax"], externals=h["iextMax"])
    return info


def scan(path: Path, ds) -> dict:
    size = path.stat().st_size
    elf_offsets, hits = [], {k: [] for k in PATTERNS}
    counts = dict.fromkeys(PATTERNS, 0)
    with open(path, "rb") as fh:
        files = iso_files(fh)
        starts = [f[0] for f in files]
        fh.seek(0)
        pos, prev = 0, b""
        while True:
            buf = fh.read(CHUNK)
            if not buf:
                break
            data, base = prev + buf, pos - len(prev)
            for key, pat in list(PATTERNS.items()) + [("", ELF_MAGIC)]:
                i = data.find(pat)
                while i != -1:
                    if i + len(pat) > len(prev):             # not a repeat from the overlap
                        if key:
                            counts[key] += 1
                            if len(hits[key]) < SAMPLES:
                                hits[key].append(base + i)
                        elif len(elf_offsets) < MAX_CANDIDATES:
                            elf_offsets.append(base + i)
                    i = data.find(pat, i + 1)
            prev = data[-OVERLAP:]
            pos += len(buf)
        elfs = []
        for off in elf_offsets:
            e = describe_elf(fh, off, size, ds)
            if e is not None:
                e["where"] = locate(files, starts, off)
                elfs.append(e)
    finds = [e for e in elfs if e["symtab"] or e["debug"]]            # any named symbol, or real debug data
    return dict(image=path.name, bytes=size, listed_files=len(files), elf_headers=len(elfs),
                elf_outside_files=sum(1 for e in elfs if e["where"].startswith("(")),
                unreadable=[e for e in elfs if str(e.get("note", "")).startswith("unreadable")],
                candidates_capped=len(elf_offsets) >= MAX_CANDIDATES,
                with_symbols=finds, elfs=elfs,
                text={k: dict(count=counts[k], where=sorted({locate(files, starts, o).split("+")[0] for o in hits[k]}))
                      for k in PATTERNS})


def render(r: dict) -> str:
    out = [f"{r['image']}: {r['bytes']:,} bytes, {r['listed_files']} listed files, {r['elf_headers']} MIPS ELF headers "
           f"({r['elf_outside_files']} outside every listed file)"]
    if r["with_symbols"]:
        out.append("  ELFs that carry symbols or debug sections:")
        for e in r["with_symbols"]:
            md = e["mdebug"]
            extra = f", .mdebug {md['files']} files / {md['procedures']} procedures" if md else ""
            out.append(f"    {e['where']:<40} {e['funcs']} FUNC, {e['objects']} OBJECT, {e['labels']} label(s){extra}"
                       f"{', ' + ' '.join(d for d in e['debug'] if d != '.mdebug') if set(e['debug']) - {'.mdebug'} else ''}")
    else:
        out.append("  no readable ELF carries a named symbol or debug data")
    for e in r["unreadable"]:
        out.append(f"  not read: {e['where']} ({e['note']})")
    if r["candidates_capped"]:
        out.append(f"  more than {MAX_CANDIDATES} ELF magic numbers: only the first {MAX_CANDIDATES} were examined")
    for k, v in r["text"].items():
        if v["count"]:
            out.append(f"  {k!r}: {v['count']} hit(s) in {', '.join(v['where'][:8])}")
    return "\n".join(out)


def self_test() -> int:
    ds = _dump_symbols()
    checks = {}

    def elf(funcs, mach=8):
        strtab = b"\0" + b"".join(b"f%d\0" % k for k in range(funcs))
        symtab = b"\0" * 16 + b"".join(struct.pack("<IIIBBH", 1 + 3 * k if k < 10 else 1, 0x100 + 8 * k, 8, 0x12, 0, 1)
                                        for k in range(funcs))
        shstr = b"\0.symtab\0.strtab\0.shstrtab\0"
        blob = bytearray(b"\x7fELF\x01\x01\x01" + b"\0" * 45)
        struct.pack_into("<HH", blob, 16, 2, mach)
        at = {}
        for name, payload in ((".symtab", symtab), (".strtab", strtab), (".shstrtab", shstr)):
            at[name] = (len(blob), len(payload))
            blob.extend(payload)
        struct.pack_into("<I", blob, 0x20, len(blob))
        struct.pack_into("<HHH", blob, 0x2E, 40, 4, 3)
        blob.extend(b"\0" * 40)
        for name in (".symtab", ".strtab", ".shstrtab"):
            blob.extend(struct.pack("<10I", shstr.index(name.encode() + b"\0"), 2 if name == ".symtab" else 3, 0, 0,
                                    at[name][0], at[name][1], 2 if name == ".symtab" else 0, 0, 0, 0))
        return bytes(blob)

    def dir_record(lba, size, name, is_dir=False):
        n = 33 + len(name) + (1 - len(name) % 2)
        r = bytearray(n)
        r[0] = n
        struct.pack_into("<I", r, 2, lba)
        struct.pack_into("<I", r, 10, size)
        r[25], r[32] = (2 if is_dir else 0), len(name)
        r[33:33 + len(name)] = name
        return bytes(r)

    named, stripped, hidden = elf(3), elf(0), elf(2)
    img = bytearray(SECTOR * 40)
    root = dir_record(20, SECTOR, b"\0", True) + dir_record(20, SECTOR, b"\1", True) + \
        dir_record(22, len(named), b"A.ELF;1") + dir_record(21, SECTOR, b"SUB", True)
    sub = dir_record(21, SECTOR, b"\0", True) + dir_record(20, SECTOR, b"\1", True) + dir_record(24, len(stripped), b"B.IRX;1")
    pvd = bytearray(SECTOR)
    pvd[:6] = b"\x01CD001"
    pvd[156:156 + 34] = dir_record(20, SECTOR, b"\0", True)[:34]
    img[16 * SECTOR:17 * SECTOR] = pvd
    img[20 * SECTOR:20 * SECTOR + len(root)] = root
    img[21 * SECTOR:21 * SECTOR + len(sub)] = sub
    img[22 * SECTOR:22 * SECTOR + len(named)] = named
    img[24 * SECTOR:24 * SECTOR + len(stripped)] = stripped
    img[30 * SECTOR + 100:30 * SECTOR + 100 + len(hidden)] = hidden          # in no directory, not sector-aligned
    img[35 * SECTOR:35 * SECTOR + 14] = b"gcc2_compiled."
    img[36 * SECTOR:36 * SECTOR + 7] = ELF_MAGIC                               # a bare magic number: not an ELF
    hostile = bytearray(named)                                                # section table pointing far outside
    struct.pack_into("<I", hostile, 0x20, 0x7FFFFF00)
    img[18 * SECTOR:18 * SECTOR + len(hostile)] = hostile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.iso"
        p.write_bytes(img)
        r = scan(p, ds)
    where = {e["where"]: e for e in r["elfs"]}
    checks["listing"] = r["listed_files"] == 2
    checks["named"] = where.get("A.ELF+0x0", {}).get("funcs") == 3
    checks["stripped-not-a-find"] = "SUB/B.IRX+0x0" in where and all(e["where"] != "SUB/B.IRX+0x0" for e in r["with_symbols"])
    checks["hidden"] = r["elf_outside_files"] == 2 and any(e["funcs"] == 2 and e["where"].startswith("(") for e in r["with_symbols"])
    checks["stray-magic"] = r["elf_headers"] == 4
    checks["hostile-survived"] = any("no usable section table" in str(e.get("note")) or str(e.get("note", "")).startswith("unreadable")
                                     for e in r["elfs"] if e["offset"] == 18 * SECTOR) and where.get("A.ELF+0x0", {}).get("funcs") == 3
    global CHUNK
    keep, CHUNK = CHUNK, 22 * SECTOR + 3                                       # the magic of A.ELF straddles a chunk edge
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.iso"
        p.write_bytes(img)
        r2 = scan(p, ds)
    CHUNK = keep
    checks["chunk-edge"] = sorted(e["offset"] for e in r2["elfs"]) == sorted(e["offset"] for e in r["elfs"])
    checks["marker"] = r["text"]["gcc2_compiled."] == dict(count=1, where=["(outside every listed file)"])
    checks["render"] = "A.ELF" in render(r) and "3 FUNC" in render(r)
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="*", type=Path)
    ap.add_argument("--json", type=Path, help="write the full report (every ELF header) here")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.images:
        ap.error("give at least one image")
    ds = _dump_symbols()
    reports = []
    for p in args.images:
        r = scan(p, ds)
        reports.append(r)
        print(render(r))
    if args.json:
        args.json.write_text(json.dumps(reports, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
