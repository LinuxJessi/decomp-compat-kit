#!/usr/bin/env python3
"""Turn a PS2 code file that is not a plain ELF into a flat image plus a sidecar.

A matching decomp starts from "the bytes at their load address". Three of the
six titles probed while building this kit do not hand those over directly:

  zlib-stub ELF   the boot ELF is a small decompressor; its only PT_LOAD begins
                  with `[u32 length][zlib stream]` and the real, headerless EE
                  image is what inflates out of it (Metal Gear Solid 3: the
                  inflated image opens with a Konami container header whose
                  first word is the Unix build timestamp).
  MWo3 overlay    Metrowerks CodeWarrior's linker overlay (Silent Hill 2 stage
                  code, Yakuza's whole game in MAIN.BIN): a 0x40-byte header
                  `[MWo3][u32 a][u32 load_addr][u32 text][u32 data][u32 bss]
                  [u32 p][u32 q][name @0x20]`, then text and data. The two
                  samples measured agree that load_addr maps file offset 0 (the
                  header is part of the image: SH2's p == q == load_addr + file
                  size == the start of .bss); word `a` (8 on SH2, 0 on Yakuza)
                  and p/q beyond that are not understood and are reported raw.
  ELF             passthrough: PT_LOAD segments flattened into one image whose
                  base is the lowest segment address (EE SLUS, XS1/XS3 .OVL
                  overlays, which are ELFs despite the extension).
  raw             anything else: copied as is; the base is whatever --base says.

Usage:
  ./unwrap_image.py FILE --describe            # JSON only, nothing written
  ./unwrap_image.py FILE --out DIR             # DIR/<stem>.image + DIR/<stem>.json
  ./unwrap_image.py FILE --out DIR --base 0x100000   # raw file, caller knows the base
  ./unwrap_image.py --self-test

The sidecar carries `kind`, `base` (null when unknown), `segments`, the input and
output SHA-256, and for a stub the declared and inflated lengths, the container
header, and the addresses the stub's own code forms outside itself (candidates
for the payload's destination; unproven, so they are listed, never asserted).
fingerprint_elf.py reads the sidecar to fingerprint a flat image.

Exit: 0 ok, 1 the file is not what its header claims (declared length differs
from the inflated length, truncated MWo3), 2 usage / unreadable input.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

E = "<"
PT_LOAD = 1
MWO3_HEADER = 0x40


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------- ELF


def elf_header(d: bytes):
    """(e_type, e_machine, e_flags, e_entry, segments[(va, off, filesz, memsz, flags)]) of an ELF32 LE."""
    if len(d) < 52 or d[:4] != b"\x7fELF" or d[4] != 1 or d[5] != 1:
        return None
    e_type, e_machine = struct.unpack_from(E + "HH", d, 16)
    e_entry, phoff = struct.unpack_from(E + "II", d, 0x18)
    e_flags, = struct.unpack_from(E + "I", d, 0x24)
    phent, phnum = struct.unpack_from(E + "HH", d, 0x2A)
    segs = []
    for k in range(phnum):
        base = phoff + k * phent
        if base + 32 > len(d):
            break
        typ, off, va, _pa, filesz, memsz, flags, _al = struct.unpack_from(E + "8I", d, base)
        if typ == PT_LOAD:
            segs.append(dict(va=va, off=off, filesz=filesz, memsz=memsz, flags=flags))
    return e_type, e_machine, e_flags, e_entry, segs


def flatten(d: bytes, segs: list) -> tuple:
    """One image spanning the loaded segments (file bytes, zero elsewhere), and its base."""
    loaded = [s for s in segs if s["memsz"]]
    if not loaded:
        return b"", None
    lo = min(s["va"] for s in loaded)
    hi = max(s["va"] + s["memsz"] for s in loaded)
    img = bytearray(hi - lo)
    for s in loaded:
        chunk = d[s["off"]:s["off"] + s["filesz"]]
        img[s["va"] - lo:s["va"] - lo + len(chunk)] = chunk
    return bytes(img), lo


# ------------------------------------------------------------- zlib stub


def formed_addresses(words, lo, hi):
    """Addresses a code stream forms with lui + addiu/ori/daddiu, outside [lo, hi)."""
    hi_half, counts = {}, {}
    for w in words:
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op == 0x0F:
            hi_half[rt] = imm << 16
        elif op in (0x09, 0x0D, 0x19) and rs in hi_half:
            v = (hi_half[rs] + (imm if op == 0x0D else (imm - 0x10000 if imm & 0x8000 else imm))) & 0xFFFFFFFF
            if 0x100000 <= v < 0x2000000 and not (lo <= v < hi):
                counts[v] = counts.get(v, 0) + 1
    return [dict(address=f"{a:#x}", uses=n) for a, n in sorted(counts.items(), key=lambda kv: -kv[1])[:8]]


def detect_stub(d: bytes, segs: list):
    """A single-PT_LOAD ELF whose load segment starts with [u32 length][zlib stream]."""
    if len(segs) != 1:
        return None
    s = segs[0]
    off = s["off"]
    if off + 6 > len(d):
        return None
    length, = struct.unpack_from(E + "I", d, off)
    if d[off + 4] != 0x78 or d[off + 5] not in (0x01, 0x5E, 0x9C, 0xDA):
        return None
    try:
        obj = zlib.decompressobj()
        payload = obj.decompress(d[off + 4:off + s["filesz"]])
    except zlib.error:
        return None
    if not payload:
        return None
    rest = off + 4 + (s["filesz"] - 4 - len(obj.unused_data))
    rest = (rest + 3) & ~3                                   # the stub's code follows the stream, word-aligned
    words = struct.unpack_from(E + f"{(off + s['filesz'] - rest) // 4}I", d, rest) if rest + 4 <= off + s["filesz"] else ()
    return dict(payload_offset=off + 4, declared_length=length, payload=payload,
                stub_code_offset=rest, stub_words=words)


def container_header(payload: bytes) -> dict:
    """The Konami timestamped header seen on MGS3: word 0 = Unix time, then zero words."""
    w0, = struct.unpack_from(E + "I", payload, 0)
    n = 1
    while n * 4 + 4 <= min(len(payload), 256) and struct.unpack_from(E + "I", payload, n * 4)[0] == 0:
        n += 1
    stamp = None
    if 0x30000000 <= w0 < 0x60000000:                      # 1995..2021 as a Unix time: a build stamp
        stamp = datetime.datetime.fromtimestamp(w0, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return dict(word0=f"{w0:#010x}", word0_as_unix_time=stamp, zero_words_after=n - 1,
                first_nonzero_offset=f"{n * 4:#x}")


# ------------------------------------------------------------------ MWo3


def mwo3_header(d: bytes):
    if len(d) < MWO3_HEADER or d[:4] != b"MWo3":
        return None
    a, load, text, data, bss, p, q = struct.unpack_from(E + "7I", d, 4)
    name = d[0x20:0x40].split(b"\0", 1)[0].decode("latin1")
    return dict(word1=a, load_addr=load, text_size=text, data_size=data, bss_size=bss,
                word6=p, word7=q, name=name)


def describe_mwo3(d: bytes, h: dict) -> dict:
    expected = MWO3_HEADER + h["text_size"] + h["data_size"]
    base = h["load_addr"]
    segs = [dict(name="header", va=base, off=0, size=MWO3_HEADER),
            dict(name="text", va=base + MWO3_HEADER, off=MWO3_HEADER, size=h["text_size"]),
            dict(name="data", va=base + MWO3_HEADER + h["text_size"], off=MWO3_HEADER + h["text_size"], size=h["data_size"]),
            dict(name="bss", va=base + expected, off=None, size=h["bss_size"])]
    notes = []
    if len(d) != expected:
        notes.append(f"file is {len(d):#x} bytes, header sizes add up to {expected:#x}")
    if h["word6"] == h["word7"] == base + expected:
        notes.append("word6 == word7 == load_addr + file size: the header is part of the image and .bss follows it")
    return dict(kind="mwo3", name=h["name"], base=base, header=dict(word1=h["word1"], word6=f"{h['word6']:#x}", word7=f"{h['word7']:#x}"),
                segments=[dict(s, va=f"{s['va']:#x}") for s in segs], complete=len(d) == expected, notes=notes)


# ---------------------------------------------------------------- driver


def describe(path: Path, data: bytes, base_override=None) -> tuple:
    """(sidecar dict, image bytes). The sidecar never carries machine paths."""
    rec = dict(input=path.name, input_size=len(data), input_sha256=sha256(data))
    h = mwo3_header(data)
    if h:
        rec.update(describe_mwo3(data, h))
        return rec, data
    eh = elf_header(data)
    if eh:
        e_type, e_machine, e_flags, e_entry, segs = eh
        rec.update(elf=dict(type=f"{e_type:#x}", machine=e_machine, flags=f"{e_flags:#x}", entry=f"{e_entry:#x}"),
                   segments=[dict(va=f"{s['va']:#x}", off=f"{s['off']:#x}", filesz=f"{s['filesz']:#x}",
                                  memsz=f"{s['memsz']:#x}", flags=s["flags"]) for s in segs])
        stub = detect_stub(data, segs)
        if stub:
            payload = stub["payload"]
            s = segs[0]
            rec.update(kind="zlib-stub-elf", stub=dict(load=f"{s['va']:#x}", size=f"{s['filesz']:#x}",
                                                      payload_offset=f"{stub['payload_offset']:#x}",
                                                      code_offset=f"{stub['stub_code_offset']:#x}"),
                       declared_length=stub["declared_length"], inflated_length=len(payload),
                       length_ok=stub["declared_length"] == len(payload),
                       container=container_header(payload),
                       base=base_override,
                       base_note="the payload is a headerless memory image; its destination is not stated in the ELF. "
                                 "`stub_forms` lists the addresses the stub's code builds outside its own segment "
                                 "(candidates, unproven); pass --base when you know it.",
                       stub_forms=formed_addresses(stub["stub_words"], s["va"], s["va"] + s["memsz"]))
            return rec, payload
        img, lo = flatten(data, segs)
        rec.update(kind="elf", base=lo if base_override is None else base_override)
        return rec, img
    rec.update(kind="raw", base=base_override,
               base_note=None if base_override is not None else "no header recognised; pass --base")
    return rec, data


def self_test() -> int:
    checks = {}
    # MWo3: 0x40 header + 8 words of text + 4 of data, word6/7 = load + file size
    text, data = b"\x00" * 32, b"\x11" * 16
    load, size = 0x01F0BC00, MWO3_HEADER + len(text) + len(data)
    hdr = b"MWo3" + struct.pack(E + "7I", 8, load, len(text), len(data), 0xE00, load + size, load + size)
    hdr += b"gx_test".ljust(32, b"\0")
    rec, img = describe(Path("t.bin"), hdr + text + data)
    checks["mwo3"] = (rec["kind"] == "mwo3" and rec["name"] == "gx_test" and rec["base"] == load and rec["complete"]
                      and rec["segments"][3]["va"] == f"{load + size:#x}" and img == hdr + text + data
                      and any("part of the image" in n for n in rec["notes"]))
    rec, _ = describe(Path("t.bin"), (hdr + text + data)[:-4])
    checks["mwo3-truncated"] = rec["kind"] == "mwo3" and not rec["complete"]

    # a zlib-stub ELF: one PT_LOAD at 0x800000 holding [len][zlib(payload)] then stub code
    payload = struct.pack(E + "I", 0x4182_2CCD) + b"\0" * 60 + bytes(range(256)) * 4
    z = zlib.compress(payload)
    stub_code = struct.pack(E + "II", 0x3C040010, 0x24840010)      # lui a0,0x10 ; addiu a0,a0,0x10 -> 0x100010
    body = struct.pack(E + "I", len(payload)) + z
    body += b"\0" * (-len(body) % 4) + stub_code
    elf = bytearray(b"\x7fELF\x01\x01\x01" + b"\0" * 9)
    elf += struct.pack(E + "HHIIIIIHHHHHH", 2, 8, 1, 0x800000, 52, 0, 0x20924001, 52, 32, 1, 40, 0, 0)
    elf += struct.pack(E + "8I", PT_LOAD, 0x1000, 0x800000, 0x800000, len(body), len(body), 7, 16)
    elf += b"\0" * (0x1000 - len(elf)) + body
    rec, img = describe(Path("SLUS_tst.00"), bytes(elf))
    checks["stub"] = (rec["kind"] == "zlib-stub-elf" and img == payload and rec["length_ok"]
                      and rec["container"]["word0_as_unix_time"] == "2004-10-29 11:43:09 UTC"
                      and rec["container"]["zero_words_after"] == 15
                      and rec["stub_forms"] and rec["stub_forms"][0]["address"] == "0x100010" and rec["base"] is None)
    # the same ELF with a wrong declared length
    bad = bytearray(elf)
    struct.pack_into(E + "I", bad, 0x1000, len(payload) + 1)
    rec, _ = describe(Path("x"), bytes(bad))
    checks["stub-length"] = rec["kind"] == "zlib-stub-elf" and not rec["length_ok"]

    # plain ELF with two segments flattens with zero fill between them
    elf2 = bytearray(b"\x7fELF\x01\x01\x01" + b"\0" * 9)
    elf2 += struct.pack(E + "HHIIIIIHHHHHH", 2, 8, 1, 0x100008, 52, 0, 0x20924001, 52, 32, 2, 40, 0, 0)
    elf2 += struct.pack(E + "8I", PT_LOAD, 0x100, 0x100000, 0x100000, 8, 8, 5, 16)
    elf2 += struct.pack(E + "8I", PT_LOAD, 0x108, 0x100020, 0x100020, 4, 16, 6, 16)
    elf2 += b"\0" * (0x100 - len(elf2)) + b"A" * 8 + b"B" * 4
    rec, img = describe(Path("e"), bytes(elf2))
    checks["elf-flatten"] = rec["kind"] == "elf" and rec["base"] == 0x100000 and len(img) == 0x30 and img[:8] == b"A" * 8 \
        and img[0x20:0x24] == b"B" * 4 and img[8:0x20] == b"\0" * 24
    rec, img = describe(Path("r"), b"\x12\x34" * 8, base_override=0x200000)
    checks["raw"] = rec["kind"] == "raw" and rec["base"] == 0x200000 and len(img) == 16
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", type=Path)
    ap.add_argument("--out", type=Path, help="write <stem>.image and <stem>.json here")
    ap.add_argument("--describe", action="store_true", help="print the sidecar JSON only")
    ap.add_argument("--base", type=lambda s: int(s, 0), help="load address of a raw file or a stub payload")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.file is None or not (args.out or args.describe):
        ap.error("FILE and one of --out / --describe are required (or --self-test)")
    try:
        data = args.file.read_bytes()
    except OSError as e:
        print(e, file=sys.stderr)
        return 2
    rec, img = describe(args.file, data, args.base)
    rec["image_size"] = len(img)
    rec["image_sha256"] = sha256(img)
    rc = 0
    if rec["kind"] == "zlib-stub-elf" and not rec["length_ok"]:
        rc = 1
    if rec["kind"] == "mwo3" and not rec["complete"]:
        rc = 1
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        stem = args.file.name
        (args.out / f"{stem}.image").write_bytes(img)
        rec["image"] = f"{stem}.image"
        (args.out / f"{stem}.json").write_text(json.dumps(rec, indent=1) + "\n")
        print(f"{args.out / stem}.image  {len(img)} bytes  kind={rec['kind']}  base="
              f"{rec['base'] if rec['base'] is None else hex(rec['base'])}")
    else:
        print(json.dumps(rec, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
