#!/usr/bin/env python3
"""Which compiler built this PS2 code image, and what evidence says so.

Six titles were fingerprinted while this kit was built (docs/CORPUS.md). Two
heuristic corrections came out of that work and fix the order below:

  1. vendor strings first. CodeWarrior's `MW MIPS C Compiler (2.4.1.01)` in
     `.comment` survives stripping and settles the question on its own; GNU
     `GCC: (GNU)` / `GNU C` strings, `gcc2_compiled.` symbols and old-style
     C++ mangling (`name__Fii`) say GCC 2.x; Itanium `_Z` mangling says 3.x.
  2. container tells: an `MWo3` header is CodeWarrior's linker overlay; a zlib
     stub ELF hides the real image (see unwrap_image.py); an IOP module (ELF
     type 0xff80, `.iopmod`) is R3000 code and none of the EE heuristics apply.
  3. the prologue census: `addiu $sp,$sp,-N` followed by callee saves. SCE
     ee-gcc saves with `sd` at an 8-byte stride; SN ProDG *and* Metrowerks
     CodeWarrior both save with `sq` at a 16-byte stride, so `sq` alone never
     separates those two. Hardware `div`/`divu` and `div.s` (no magic-multiply,
     no reciprocal) are the 2.9x-era ee-gcc idiom; float constants formed as
     `lui`+`mtc1` immediates are ee-gcc 2.96 where 3.x loads them from .rodata.
  4. SDK banners `PsIIlib<name><version>` date the Sony SDK the game linked;
     `__DATE__`-style stamps and surviving source paths sketch the TU skeleton.

A minority population is normal in any census: the Sony SDK static libraries
are ee-gcc code in every binary, whatever built the game.

Usage:
  ./fingerprint_elf.py FILE                 # ELF (SLUS, .OVL, .IRX) or MWo3 file
  ./fingerprint_elf.py IMAGE --base 0x100000    # flat image at a known address
  ./fingerprint_elf.py IMAGE --sidecar IMAGE.json   # flat image + unwrap_image.py sidecar
  ./fingerprint_elf.py FILE --json [-v]
  ./fingerprint_elf.py --self-test

Exit: 0 report produced, 2 unreadable input or usage. The verdict is a ranked
hypothesis with its evidence, never a proof; matching one function against a
candidate compiler is the proof (see the kit's verify_decomp.py).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import struct
import sys
import zlib
from pathlib import Path

E = "<"
PT_LOAD = 1
SHT_SYMTAB, SHT_NOBITS = 2, 8
SHF_EXECINSTR = 4
STT_FUNC = 2
OP_SPECIAL, OP_ADDIU, OP_LUI, OP_COP1, OP_LQ, OP_SQ, OP_SW, OP_SD = 0, 9, 0x0F, 0x11, 0x1E, 0x1F, 0x2B, 0x3F
OP_LWC1, OP_LDC1 = 0x31, 0x35
SP, AT = 29, 1

VENDOR_PATTERNS = [
    ("metrowerks", rb"MW MIPS C Compiler[ -~]{0,40}"),
    ("metrowerks", rb"Metrowerks[ -~]{0,40}"),
    ("metrowerks", rb"CodeWarrior[ -~]{0,40}"),
    ("gnu", rb"GCC: \(GNU\)[ -~]{0,60}"),
    ("gnu", rb"GNU C [ -~]{0,60}"),
    ("gnu", rb"egcs-[0-9.]+[ -~]{0,40}"),
    ("sn", rb"SN Systems[ -~]{0,40}"),
    ("sn", rb"ProDG[ -~]{0,40}"),
]
BANNER = re.compile(rb"PsII(lib[A-Za-z0-9]{2,10}) ?(\d{4})")
STAMP = re.compile(rb"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [ 0-3]\d (?:19|20)\d\d(?: ?\d\d:\d\d:\d\d)?")
SOURCE_PATH = re.compile(rb"(?<![ -~])(?:[A-Za-z0-9_.:+-]*[/\\])*[A-Za-z_][A-Za-z0-9_+-]{1,40}(?:\.[A-Za-z0-9_+-]+)*\.(?:euc\.c|cpp|cc|cxx|c|s|h)(?![ -~])")


# -------------------------------------------------------------------- input


def read_elf(d: bytes):
    if len(d) < 52 or d[:4] != b"\x7fELF" or d[4] != 1 or d[5] != 1:
        return None
    e_type, e_machine = struct.unpack_from(E + "HH", d, 16)
    e_entry, phoff, shoff, e_flags = struct.unpack_from(E + "IIII", d, 0x18)
    phent, phnum, shent, shnum, shstrndx = struct.unpack_from(E + "HHHHH", d, 0x2A)
    segs = []
    for k in range(phnum):
        if phoff + (k + 1) * phent > len(d):
            break
        typ, off, va, _pa, filesz, memsz, flags, _al = struct.unpack_from(E + "8I", d, phoff + k * phent)
        if typ == PT_LOAD:
            segs.append(dict(va=va, off=off, filesz=filesz, memsz=memsz, flags=flags))
    secs = []
    if shoff and shnum and shoff + shnum * shent <= len(d):
        raw = [dict(zip(("nameoff", "typ", "flags", "addr", "off", "size", "link", "info", "align", "entsz"),
                        struct.unpack_from(E + "10I", d, shoff + i * shent))) for i in range(shnum)]
        if shstrndx < len(raw):
            tab = raw[shstrndx]["off"]
            for s in raw:
                b = tab + s["nameoff"]
                end = d.find(b"\0", b)
                s["name"] = d[b:end].decode("latin1") if 0 <= b < len(d) and end >= 0 else ""
            secs = raw
    return dict(type=e_type, machine=e_machine, flags=e_flags, entry=e_entry, segments=segs, sections=secs)


def mwo3_header(d: bytes):
    if len(d) < 0x40 or d[:4] != b"MWo3":
        return None
    a, load, text, data, bss, p, q = struct.unpack_from(E + "7I", d, 4)
    return dict(word1=a, load_addr=load, text_size=text, data_size=data, bss_size=bss, word6=p, word7=q,
                name=d[0x20:0x40].split(b"\0", 1)[0].decode("latin1"))


def is_zlib_stub(d: bytes, segs: list) -> bool:
    if len(segs) != 1:
        return False
    off = segs[0]["off"]
    if off + 6 > len(d) or d[off + 4] != 0x78 or d[off + 5] not in (0x01, 0x5E, 0x9C, 0xDA):
        return False
    try:
        zlib.decompressobj().decompress(d[off + 4:off + 4 + 4096])
    except zlib.error:
        return False
    return True


class Image:
    """Code regions (bytes, base va) to census, plus everything else the input offers."""

    def __init__(self, data: bytes, base=None, sidecar=None):
        self.data = data
        self.kind, self.elf, self.mwo3 = "raw", None, None
        self.code: list = []          # (bytes, va)
        self.loaded: list = []        # (bytes, va) every loaded region (strings live here too)
        self.symbols: list = []       # (name, va, size, type)
        self.sections: list = []
        self.notes: list = []
        if sidecar and base is None and sidecar.get("base") is not None:
            base = sidecar["base"]
        h = mwo3_header(data)
        if h:
            self.kind, self.mwo3 = "mwo3", h
            text = data[0x40:0x40 + h["text_size"]]
            self.code.append((text, h["load_addr"] + 0x40))
            self.loaded.append((data, h["load_addr"]))
            return
        elf = read_elf(data)
        if elf:
            self.elf = elf
            self.kind = "elf"
            self.sections = [s["name"] for s in elf["sections"] if s["name"]]
            if elf["type"] == 0xFF80 or ".iopmod" in self.sections:
                self.kind = "iop-module"
            elif is_zlib_stub(data, elf["segments"]):
                self.kind = "zlib-stub-elf"
            for s in elf["segments"]:
                chunk = data[s["off"]:s["off"] + s["filesz"]]
                self.loaded.append((chunk, s["va"]))
                if s["flags"] & 1:
                    self.code.append((chunk, s["va"]))
            if not self.code:          # no executable segment flag (some overlays): executable sections
                for s in elf["sections"]:
                    if s["flags"] & SHF_EXECINSTR and s["typ"] != SHT_NOBITS:
                        self.code.append((data[s["off"]:s["off"] + s["size"]], s["addr"]))
            if not self.loaded and elf["type"] == 1:      # relocatable: sections only
                for s in elf["sections"]:
                    if s["typ"] != SHT_NOBITS and s["flags"] & 2:
                        self.loaded.append((data[s["off"]:s["off"] + s["size"]], s["addr"]))
            self._symbols(elf)
            return
        self.loaded.append((data, base or 0))
        self.code.append((data, base or 0))
        if base is None:
            self.notes.append("no header recognised and no --base: addresses are offsets")

    def _symbols(self, elf):
        for s in elf["sections"]:
            if s["typ"] != SHT_SYMTAB or s["link"] >= len(elf["sections"]):
                continue
            strtab = elf["sections"][s["link"]]["off"]
            for k in range(s["size"] // 16):
                no, val, size, info, _other, _shndx = struct.unpack_from(E + "IIIBBH", self.data, s["off"] + k * 16)
                end = self.data.find(b"\0", strtab + no)
                self.symbols.append((self.data[strtab + no:end].decode("latin1"), val, size, info & 0xF))

    def section(self, name):
        if not self.elf:
            return None
        for s in self.elf["sections"]:
            if s["name"] == name:
                return s
        return None


# ------------------------------------------------------------------ evidence


def strings_evidence(img: Image) -> dict:
    out = dict(vendor=[], comment=[], banners=[], stamps=[], source_paths=[], source_path_count=0,
               libinfo=[], gcc2_markers=0, mangling=dict(old_gnu=0, itanium=0), zlib_strings=False)
    for name in (".comment", ".ident"):
        s = img.section(name)
        if s and s["typ"] != SHT_NOBITS:
            out["comment"] += sorted({x.decode("latin1") for x in img.data[s["off"]:s["off"] + s["size"]].split(b"\0") if len(x) > 3})
    blobs = [b for b, _ in img.loaded] or [img.data]
    seen_vendor, seen_banner, paths = set(), {}, set()
    for blob in ["\0".join(out["comment"]).encode("latin1")] + blobs:
        for family, pat in VENDOR_PATTERNS:
            for m in re.finditer(pat, blob):
                text = m.group().decode("latin1").strip()
                if (family, text) not in seen_vendor:
                    seen_vendor.add((family, text))
                    out["vendor"].append(dict(family=family, text=text))
        for m in BANNER.finditer(blob):
            seen_banner.setdefault(m.group(1).decode(), m.group(2).decode())
        out["stamps"] += [m.group().decode() for m in STAMP.finditer(blob)]
        for m in SOURCE_PATH.finditer(blob):
            paths.add(m.group().decode("latin1"))
        for marker in (b"__ps2_libinfo__", b"__ps2_klibinfo__"):
            if marker in blob and marker.decode() not in out["libinfo"]:
                out["libinfo"].append(marker.decode())
        if b"inflate" in blob and b"zlib" in blob.lower():
            out["zlib_strings"] = True
        out["mangling"]["itanium"] += len(set(re.findall(rb"(?<![A-Za-z0-9_])_Z[NK]?\d+[A-Za-z_]\w{2,}", blob)))
    out["banners"] = [dict(library=k, version=v, release=".".join(v)) for k, v in sorted(seen_banner.items())]
    out["stamps"] = sorted(set(out["stamps"]))[:12]
    out["source_path_count"] = len(paths)
    out["source_paths"] = sorted(paths, key=len)[:10]
    for name, _va, _size, _typ in img.symbols:
        if name in ("gcc2_compiled.", "__gnu_compiled_c", "__gnu_compiled_cplusplus"):
            out["gcc2_markers"] += 1
        elif re.match(r"^[A-Za-z_]\w*__F[a-zA-Z0-9_]*$", name) or re.match(r"^_\$_|^__\d+[A-Z]\w+$", name):
            out["mangling"]["old_gnu"] += 1
        elif name.startswith("_Z"):
            out["mangling"]["itanium"] += 1
    for marker in (b"gcc2_compiled.", b"__gnu_compiled_c"):
        if not img.symbols and any(marker in b for b in blobs):
            out["gcc2_markers"] += 1
    return out


def census(img: Image) -> dict:
    c = dict(prologues=0, save_style=collections.Counter(), stride=collections.Counter(),
             sd_sp=0, sq_sp=0, sw_sp=0, lq=0, sq=0, div=0, divu=0, div_s=0, float_immediates=0,
             float_pool_loads=0, mult3=0, words=0)
    for blob, _va in img.code:
        n = len(blob) // 4
        words = struct.unpack_from(E + f"{n}I", blob, 0)
        c["words"] += n
        prev_lui = None
        for i, w in enumerate(words):
            op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
            funct = w & 0x3F
            if op == OP_SPECIAL:
                if funct == 0x1A:
                    c["div"] += 1
                elif funct == 0x1B:
                    c["divu"] += 1
                elif funct in (0x18, 0x19) and (w >> 11) & 31:    # mult/multu with rd: 3-operand R5900 form
                    c["mult3"] += 1
            elif op == OP_COP1:
                if rs == 0x10 and funct == 3:
                    c["div_s"] += 1
                elif rs == 0x04 and prev_lui == rt:          # mtc1 rt,fs right after lui rt
                    c["float_immediates"] += 1
            elif op in (OP_LWC1, OP_LDC1) and rs != SP:
                c["float_pool_loads"] += 1
            elif op == OP_LQ:
                c["lq"] += 1
            elif op == OP_SQ:
                c["sq"] += 1
                if rs == SP:
                    c["sq_sp"] += 1
            elif op == OP_SD and rs == SP:
                c["sd_sp"] += 1
            elif op == OP_SW and rs == SP:
                c["sw_sp"] += 1
            prev_lui = rt if op == OP_LUI else None
            if op == OP_ADDIU and rs == SP and rt == SP and w & 0x8000:
                offs = dict(sd=[], sq=[], sw=[])
                for w2 in words[i + 1:min(i + 20, n)]:
                    op2, base2 = w2 >> 26, (w2 >> 21) & 31
                    if base2 == SP and op2 in (OP_SD, OP_SQ, OP_SW):
                        offs[{OP_SD: "sd", OP_SQ: "sq", OP_SW: "sw"}[op2]].append(w2 & 0xFFFF)
                    elif op2 in (2, 3) or (op2 == 0 and (w2 & 0x3F) in (8, 9)):
                        break
                best = max(offs, key=lambda k: len(offs[k]))
                if len(offs[best]) >= 2:
                    c["prologues"] += 1
                    c["save_style"][best] += 1
                    d = sorted(offs[best])
                    stride = min(b - a for a, b in zip(d, d[1:]))
                    c["stride"][f"{best}/{stride}"] += 1
    c["save_style"] = dict(c["save_style"].most_common())
    c["stride"] = dict(c["stride"].most_common(4))
    return c


def verdict(img: Image, s: dict, c: dict) -> dict:
    reasons, footnotes = [], []
    if img.kind == "iop-module":
        return dict(compiler="IOP module (R3000, MIPS-I): EE heuristics skipped", confidence="n/a",
                    reasons=["ELF type 0xff80 / .iopmod section"], footnotes=[])
    if img.kind == "zlib-stub-elf":
        return dict(compiler="zlib decompressor stub: fingerprint the inflated payload (unwrap_image.py)",
                    confidence="n/a", reasons=["single PT_LOAD beginning with [u32 length][zlib stream]"], footnotes=[])
    families = {v["family"] for v in s["vendor"]}
    style = c["save_style"]
    dominant = max(style, key=style.get) if style else None
    stride = max(c["stride"], key=c["stride"].get) if c["stride"] else None
    total = sum(style.values())
    minority = total - style.get(dominant, 0) if dominant else 0
    if "metrowerks" in families:
        name, conf = "Metrowerks CodeWarrior for PS2 (MWCC)", "high"
        reasons.append("vendor string: " + next(v["text"] for v in s["vendor"] if v["family"] == "metrowerks"))
        if dominant == "sq":
            reasons.append(f"corroborated by the census: sq/16-byte saves dominate ({style.get('sq')} of {total})")
    elif "sn" in families:
        name, conf = "SN Systems ProDG", "high"
        reasons.append("vendor string: " + next(v["text"] for v in s["vendor"] if v["family"] == "sn"))
    elif dominant == "sq":
        name, conf = "sq/16-byte saves: SN ProDG or Metrowerks CodeWarrior (census cannot separate them)", "medium"
        reasons.append(f"sq saves dominate ({style.get('sq')} of {total} prologues); no vendor string found")
        if img.kind == "mwo3":
            name, conf = "Metrowerks CodeWarrior for PS2 (MWCC)", "high"
            reasons.append("MWo3 container: CodeWarrior's linker overlay")
    elif dominant == "sd":
        gcc3 = s["mangling"]["itanium"] > s["mangling"]["old_gnu"] and s["mangling"]["itanium"] > 2
        pool = c["float_pool_loads"] > 4 * max(1, c["float_immediates"])
        stride_n = int(stride.split("/")[1]) if stride else 0
        if gcc3 or (pool and not s["gcc2_markers"] and not s["mangling"]["old_gnu"]):
            name, conf = "SCE ee-gcc 3.x", "medium"
            if gcc3:
                reasons.append(f"Itanium mangling ({s['mangling']['itanium']} names)")
            if pool:
                reasons.append(f"float constants loaded from .rodata ({c['float_pool_loads']}) rather than lui+mtc1 "
                               f"({c['float_immediates']}): the 3.x idiom (2.96 forms them as immediates)")
        elif stride_n == 16 and not s["gcc2_markers"]:
            name, conf = "SCE ee-gcc 2.9x, 1999 build (ee-gcc 2.9-991111: sd saves at a 16-byte stride)", "medium"
            reasons.append("sd saves at a 16-byte stride: the idiom-probe rig matched that to the 2.9-991111 build; "
                           "2.96 and 3.x save at 8 (a per-function check against both compilers is the proof)")
        else:
            name, conf = "SCE ee-gcc 2.96 (Cygnus/SCE R5900 GCC, 2.9x family)", "high" if (s["gcc2_markers"] or "gnu" in families) else "medium"
            if s["gcc2_markers"]:
                reasons.append(f"gcc2_compiled./__gnu_compiled_c markers: {s['gcc2_markers']}")
            if s["mangling"]["old_gnu"]:
                reasons.append(f"old GNU C++ mangling, zero Itanium: {s['mangling']['old_gnu']} names")
            if c["float_immediates"]:
                reasons.append(f"float constants as lui+mtc1 immediates: {c['float_immediates']} (ee-gcc 2.96 idiom)")
        reasons.insert(0, f"sd saves at {stride.split('/')[1] if stride else '?'}-byte stride dominate ({style.get('sd')} of {total} prologues)")
        if c["div"] + c["divu"]:
            reasons.append(f"hardware div/divu {c['div']}/{c['divu']}, div.s {c['div_s']}: real divides, no magic-multiply "
                           "or reciprocal (true of every PS2 compiler met so far; it rules out -ffast-math-style flags, not a vendor)")
    else:
        name, conf = "unknown (no prologues found)", "none"
    for v in s["vendor"]:
        if v["family"] == "gnu":
            reasons.append("GNU string: " + v["text"])
    if minority:
        footnotes.append(f"{minority} prologue(s) use another save style: a mixed census is normal, "
                         "the Sony SDK static libraries are ee-gcc code in every binary")
    eh = img.section(".eh_frame")
    if eh and eh["size"] == 4:
        reasons.append(".eh_frame is 4 bytes: built with -fno-exceptions")
    for name_ in (".vutext", ".vudata", ".DVP.ovlytab", ".mdebug.eabi64"):
        if name_ in img.sections:
            reasons.append(f"{name_} section: Sony DVP/EE toolchain output")
            break
    return dict(compiler=name, confidence=conf, reasons=reasons, footnotes=footnotes)


def fingerprint(path_name: str, data: bytes, base=None, sidecar=None) -> dict:
    img = Image(data, base, sidecar)
    s = strings_evidence(img)
    c = census(img)
    funcs = sum(1 for _n, _v, _s, t in img.symbols if t == STT_FUNC)
    rec = dict(input=path_name, size=len(data), kind=img.kind,
               stripped=(img.kind != "mwo3" and not img.symbols) if img.elf or img.kind == "raw" else None,
               func_symbols=funcs, sections=img.sections, notes=img.notes, strings=s, census=c)
    if img.mwo3:
        rec["mwo3"] = dict(name=img.mwo3["name"], load_addr=f"{img.mwo3['load_addr']:#x}",
                           text=img.mwo3["text_size"], data=img.mwo3["data_size"], bss=img.mwo3["bss_size"])
    if img.elf:
        rec["elf"] = dict(type=f"{img.elf['type']:#x}", flags=f"{img.elf['flags']:#x}", entry=f"{img.elf['entry']:#x}",
                          segments=[dict(va=f"{x['va']:#x}", filesz=f"{x['filesz']:#x}", memsz=f"{x['memsz']:#x}",
                                         flags=x["flags"]) for x in img.elf["segments"]])
    rec["verdict"] = verdict(img, s, c)
    return rec


def report(rec: dict, verbose=False) -> str:
    v, s, c = rec["verdict"], rec["strings"], rec["census"]
    lines = [f"{rec['input']}: {rec['kind']}, {rec['size']} bytes, "
             f"{'stripped' if rec['stripped'] else ('%d FUNC symbols' % rec['func_symbols']) if rec['stripped'] is not None else ''}".rstrip(", "),
             f"  verdict: {v['compiler']}  [{v['confidence']}]"]
    lines += [f"    - {r}" for r in v["reasons"]]
    lines += [f"    note: {f}" for f in v["footnotes"]]
    if rec.get("mwo3"):
        m = rec["mwo3"]
        lines.append(f"  MWo3 '{m['name']}' at {m['load_addr']}: text {m['text']:#x} data {m['data']:#x} bss {m['bss']:#x}")
    if c["prologues"]:
        lines.append(f"  census: {c['prologues']} prologues {c['save_style']}  strides {c['stride']}")
        lines.append(f"          sd(sp) {c['sd_sp']}  sq(sp) {c['sq_sp']}  sw(sp) {c['sw_sp']}  lq/sq {c['lq']}/{c['sq']}  "
                     f"div/divu {c['div']}/{c['divu']}  div.s {c['div_s']}  mult3 {c['mult3']}  float imm/pool {c['float_immediates']}/{c['float_pool_loads']}")
    if s["comment"]:
        lines.append("  .comment: " + " | ".join(s["comment"][:4]))
    if s["vendor"] and verbose:
        lines += [f"  vendor: {x['text']}" for x in s["vendor"][:6]]
    if s["banners"]:
        lines.append("  SDK banners: " + ", ".join(f"{b['library']} {b['release']}" for b in s["banners"]))
    if s["libinfo"]:
        lines.append("  " + ", ".join(s["libinfo"]) + " present")
    if s["stamps"]:
        lines.append(f"  build stamps: {', '.join(s['stamps'][:4])}" + (f" (+{len(s['stamps']) - 4})" if len(s["stamps"]) > 4 else ""))
    if s["source_path_count"]:
        lines.append(f"  source paths: {s['source_path_count']} distinct, e.g. {', '.join(s['source_paths'][:5])}")
    if s["mangling"]["old_gnu"] or s["mangling"]["itanium"]:
        lines.append(f"  mangling: old GNU {s['mangling']['old_gnu']}, Itanium {s['mangling']['itanium']}")
    if verbose and rec["sections"]:
        lines.append("  sections: " + " ".join(rec["sections"][:24]))
    return "\n".join(lines)


# ---------------------------------------------------------------- self-test


def build_elf(code: bytes, comment: bytes = b"", symbols=None, e_type=2, extra_sections=()) -> bytes:
    """A minimal ELF32 LE: one executable PT_LOAD at 0x100000 holding `code`, optional
    .comment, .symtab/.strtab and named empty sections."""
    names = [b"", b".text"] + ([b".comment"] if comment else []) + ([b".symtab", b".strtab"] if symbols else []) \
        + [n.encode() for n in extra_sections] + [b".shstrtab"]
    shstr = b"\0".join(names) + b"\0"
    nameoff = {n: shstr.index(n + b"\0") if n else 0 for n in names}
    body = bytearray()
    layout = {}
    def put(name, blob):
        layout[name] = (0x1000 + len(body), len(blob))
        body.extend(blob + b"\0" * (-len(blob) % 16))
    put(b".text", code)
    if comment:
        put(b".comment", comment)
    if symbols:
        strtab = b"\0" + b"\0".join(n.encode() for n, _v, _s in symbols) + b"\0"
        symtab = b"\0" * 16
        for n, v, s in symbols:
            symtab += struct.pack(E + "IIIBBH", strtab.index(n.encode() + b"\0"), v, s, (1 << 4) | STT_FUNC, 0, 1)
        put(b".symtab", symtab)
        put(b".strtab", strtab)
    put(b".shstrtab", shstr)
    shdrs = [struct.pack(E + "10I", *([0] * 10))]
    for n in names[1:]:
        off, size = layout.get(n, (0, 0))
        typ, flags, link, entsz = 1, 0, 0, 0
        if n == b".text":
            flags = 6
        elif n == b".symtab":
            typ, link, entsz = SHT_SYMTAB, names.index(b".strtab"), 16
        elif n in (b".strtab", b".shstrtab"):
            typ = 3
        shdrs.append(struct.pack(E + "10I", nameoff[n], typ, flags, 0x100000 if n == b".text" else 0, off, size, link, 0, 4, entsz))
    shoff = 0x1000 + len(body)
    hdr = bytearray(b"\x7fELF\x01\x01\x01" + b"\0" * 9)
    hdr += struct.pack(E + "HHIIIIIHHHHHH", e_type, 8, 1, 0x100008, 52, shoff, 0x20924001, 52, 32, 1, 40, len(names), len(names) - 1)
    hdr += struct.pack(E + "8I", PT_LOAD, 0x1000, 0x100000, 0x100000, len(code), len(code), 5, 16)
    return bytes(hdr) + b"\0" * (0x1000 - len(hdr)) + bytes(body) + b"".join(shdrs)


def asm(*words):
    return struct.pack(E + f"{len(words)}I", *words)


SD_PROLOGUE = asm(0x27BDFFC0,                 # addiu sp,sp,-0x40
                  0xFFB00000, 0xFFB10008, 0xFFB20010, 0xFFBF0018,   # sd s0..s2,ra at 8-byte stride
                  0x0081001A, 0x00001012,     # div a0,at ; mflo v0
                  0x46020003,                 # div.s f0,f0,f2
                  0x3C014000, 0x44810000,     # lui at,0x4000 ; mtc1 at,f0
                  0x03E00008, 0x00000000)
SQ_PROLOGUE = asm(0x27BDFFC0,
                  0x7FB00000, 0x7FB10010, 0x7FBF0020,                # sq s0,s1,ra at 16-byte stride
                  0x03E00008, 0x00000000)


def self_test() -> int:
    checks = {}
    mw = build_elf(SQ_PROLOGUE * 5, comment=b"MW MIPS C Compiler (2.4.1.01)\0PlayStation2\0")
    r = fingerprint("mw", mw)
    checks["mwcc"] = r["verdict"]["compiler"].startswith("Metrowerks") and r["census"]["save_style"] == {"sq": 5} \
        and "MW MIPS C Compiler (2.4.1.01)" in r["strings"]["comment"] and r["stripped"]
    r = fingerprint("sq-only", build_elf(SQ_PROLOGUE * 3))
    checks["sq-ambiguous"] = "ProDG or Metrowerks" in r["verdict"]["compiler"]
    syms = [("gcc2_compiled.", 0x100000, 0), ("main", 0x100000, 48), ("rotX__FFZ", 0x100030, 48)]
    gnu = build_elf(SD_PROLOGUE * 4 + SQ_PROLOGUE, symbols=syms, extra_sections=(".vutext",))
    r = fingerprint("gnu", gnu)
    v = r["verdict"]
    checks["ee-gcc-2.96"] = v["compiler"].startswith("SCE ee-gcc 2.96") and v["confidence"] == "high" \
        and r["census"]["save_style"] == {"sd": 4, "sq": 1} and r["census"]["stride"].get("sd/8") == 4 \
        and r["census"]["div"] == 4 and r["census"]["div_s"] == 4 and r["census"]["float_immediates"] == 4 \
        and r["strings"]["gcc2_markers"] == 1 and r["strings"]["mangling"]["old_gnu"] == 1 \
        and any("vutext" in x for x in v["reasons"]) and v["footnotes"] and not r["stripped"] and r["func_symbols"] == 3
    banner_blob = SD_PROLOGUE * 2 + b"PsIIlibcdvd 2424\0PsIIlibgraph2400\0__ps2_libinfo__\0Nov 24 2002 11:02:50\0" \
        + b"\0../Undulate.euc.c\0src/main/mem.c\0"
    r = fingerprint("banners", build_elf(banner_blob))
    s = r["strings"]
    checks["banners"] = [b["library"] + ":" + b["release"] for b in s["banners"]] == ["libcdvd:2.4.2.4", "libgraph:2.4.0.0"] \
        and s["libinfo"] == ["__ps2_libinfo__"] and s["stamps"] == ["Nov 24 2002 11:02:50"] and s["source_path_count"] == 2
    checks["iop"] = fingerprint("irx", build_elf(SD_PROLOGUE, e_type=0xFF80))["verdict"]["compiler"].startswith("IOP module")
    hdr = b"MWo3" + struct.pack(E + "7I", 8, 0x01F0BC00, len(SQ_PROLOGUE) * 2, 16, 0, 0, 0) + b"gx_t".ljust(32, b"\0")
    r = fingerprint("ovl.bin", hdr + SQ_PROLOGUE * 2 + b"\x11" * 16)
    checks["mwo3"] = r["kind"] == "mwo3" and r["verdict"]["compiler"].startswith("Metrowerks") and r["mwo3"]["name"] == "gx_t"
    payload = zlib.compress(b"\0" * 64)
    stub = build_elf(struct.pack(E + "I", 64) + payload)
    checks["stub"] = fingerprint("stub", stub)["kind"] == "zlib-stub-elf"
    r = fingerprint("flat", SD_PROLOGUE * 3, base=0x100000)
    checks["flat"] = r["kind"] == "raw" and r["verdict"]["compiler"].startswith("SCE ee-gcc 2.96") and not r["notes"]
    checks["report"] = "verdict:" in report(fingerprint("gnu", gnu))
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", type=Path)
    ap.add_argument("--base", type=lambda x: int(x, 0), help="load address of a flat image")
    ap.add_argument("--sidecar", type=Path, help="unwrap_image.py sidecar JSON (supplies the base)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if args.file is None:
        ap.error("FILE is required (or --self-test)")
    try:
        data = args.file.read_bytes()
        sidecar = json.loads(args.sidecar.read_text()) if args.sidecar else None
        if sidecar is None and args.file.with_suffix(args.file.suffix + ".json").is_file():
            sidecar = json.loads(args.file.with_suffix(args.file.suffix + ".json").read_text())
        if sidecar is None and args.file.suffix == ".image" and args.file.with_suffix(".json").is_file():
            sidecar = json.loads(args.file.with_suffix(".json").read_text())
    except (OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    rec = fingerprint(args.file.name, data, args.base, sidecar)
    print(json.dumps(rec, indent=1) if args.json else report(rec, args.verbose))
    return 0


if __name__ == "__main__":
    sys.exit(main())
