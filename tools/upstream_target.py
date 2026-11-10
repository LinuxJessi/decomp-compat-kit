#!/usr/bin/env python3
"""Describe a splat-based decomp checkout whose originals carry NO symbol table as a kit
target: a symbols CSV and a decomp-target/1 manifest (docs/TOOLS.md "Targets"), both
rebuilt from the checkout's own config files. Written for OpokXeno/xsg-ii-decomp
(Xenosaga Episode II, stripped); nothing in it names that game.

  tools/upstream_target.py --upstream ~/src/xsg-ii-decomp --game-dir ~/xs2/originals \\
      --symbols-out symbols/xs2-symbols.csv --target-out symbols/xs2-target.json

Run it again after the upstream renames functions or moves TUs. The checkout is only read.

What is read (all under <upstream>/config):
  symbols/estimated-functions.json   units.<unit>.file = the unit's original; its functions
                                     [name, va, size, shndx] say which addresses a unit owns
  symbols/<unit>.txt                 splat symbol_addrs (`name = 0xVA; // type:func size:0xN`):
                                     the names of record; a row without a type is a data label
  symbols/data-names.json            units.<unit>.{va: name}: more data labels
  symbols/*.externals.ld             PROVIDE(name = va); /* unit | libgcc */ : names taken from another
                                     unit, and the compiler's helper calls (dpmul, __divdi3)
  objects/<unit>.compile.json        tus.<key>: id, path, language, in_scope, flags,
                                     compiler_profile, assembler, assembler_options; toolchains
  objects/overlays.compile.json      units.<unit>.tus[]: ordinal, source, kind, flags, compiler, assembler
  toolchain-identity.json            tools.<name>.dir|path (where a tool sits under the toolchains
                                     root) and the contract that pairs them (cc1 driver flags)
and from --game-dir, the program headers of each original (which unit an address belongs
to) and the resident image's .reginfo (the value of _gp). No game byte is written anywhere.

The CSV has the kit's columns (binary,name,va,size,type,bind,shndx). Every binding is
GLOBAL: the originals have no table, so nothing is known to be file-local. A data label has
size 0 (its extent is unknown). Function sizes are the upstream's estimates.

The manifest carries no machine path: `root`, `game_dir` and `toolchains` are written as
$DECOMP_UPSTREAM, $DECOMP_GAME_DIR and $DECOMP_TOOLCHAINS (the manifest expands variables),
the symbol table as a path relative to the manifest. Each TU names a contract built from
what the upstream's compile manifest says of that TU: its cc1, its assembler and the
assembler's per-object options (`-g`); -O and -G stay per-TU flags.
"""
import argparse
import collections
import csv
import json
import re
import struct
import sys
from pathlib import Path

ROW = re.compile(r"^\s*([A-Za-z_.$][\w.$]*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;\s*(?://\s*(.*))?$")
PROVIDE = re.compile(r"PROVIDE\(\s*\"?([A-Za-z_.$][\w.$]*)\"?\s*=\s*(0x[0-9A-Fa-f]+)\s*\)\s*;\s*(?:/\*\s*(\w+)\s*\*/)?")


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def segments(image: bytes) -> list:
    """[(va, memsz)] of the PT_LOAD program headers."""
    if image[:4] != b"\x7fELF":
        return []
    phoff, = struct.unpack_from("<I", image, 0x1C)
    ent, num = struct.unpack_from("<HH", image, 0x2A)
    out = []
    for k in range(num):
        typ, _off, va, _pa, _filesz, memsz = struct.unpack_from("<6I", image, phoff + k * ent)
        if typ == 1:
            out.append((va, memsz))
    return out


def reginfo_gp(image: bytes):
    """ri_gp_value of the ELF's .reginfo section (the linker's _gp), or None."""
    if image[:4] != b"\x7fELF":
        return None
    shoff, = struct.unpack_from("<I", image, 0x20)
    shentsize, shnum, _shstrndx = struct.unpack_from("<HHH", image, 0x2E)
    for i in range(shnum):
        _name, typ, _flags, _addr, off, size = struct.unpack_from("<6I", image, shoff + i * shentsize)
        if typ == 0x70000006 and size >= 24:            # SHT_MIPS_REGINFO
            return struct.unpack_from("<I", image, off + 20)[0]
    return None


def unit_files(up: Path) -> dict:
    """{unit: original file name}, the resident unit (`main`) first."""
    est = load(up / "config" / "symbols" / "estimated-functions.json") or {}
    units = {u: v.get("file") for u, v in (est.get("units") or {}).items() if isinstance(v, dict) and v.get("file")}
    return dict(sorted(units.items(), key=lambda kv: (kv[0] != "main", kv[0])))


def symbol_rows(up: Path, game_dir, units: dict, warn=print) -> list:
    """The CSV rows [(binary, name, va, size, type, bind, shndx)]."""
    est = (load(up / "config" / "symbols" / "estimated-functions.json") or {}).get("units") or {}
    own = {u: {int(f[1]): (f[0], int(f[2]), int(f[3]) if len(f) > 3 else 1) for f in (est.get(u) or {}).get("functions") or []}
           for u in units}
    segs = {}
    for u, name in units.items():
        path = Path(game_dir) / name if game_dir else None
        segs[u] = segments(path.read_bytes()) if path and path.is_file() else []
    resident = next(iter(units), None)

    def owner(unit, va):
        """The unit a data label listed by UNIT belongs to: UNIT when the address is inside
        its image, else the resident image when it is inside that one."""
        for u in (unit, resident):
            if any(lo <= va < lo + n for lo, n in segs.get(u, [])):
                return u
        return None

    rows, seen = [], set()

    def add(unit, name, va, size, typ, shndx=1):
        key = (units[unit], name)
        if key in seen or name.startswith(("$", ".L")):       # $LC0, .L12: an assembler-local label, one per
            return                                            # object file, never a name of the program
        seen.add(key)
        rows.append((units[unit], name, f"{va:#x}", size, typ, "GLOBAL", shndx))

    labels = collections.defaultdict(dict)                    # unit -> {va: name} data labels
    for u in units:
        txt = up / "config" / "symbols" / f"{u}.txt"
        if not txt.is_file():
            warn(f"{txt}: missing, {u} gets no names")
            continue
        named = {}
        for line in txt.read_text().splitlines():
            m = ROW.match(line)
            if not m:
                continue
            name, va, attrs = m.group(1), int(m.group(2), 16), m.group(3) or ""
            if "type:func" in attrs:
                size = re.search(r"size:(0x[0-9A-Fa-f]+)", attrs)
                if va in own[u] or (size and not own[u]):
                    named[va] = name
                    add(u, name, va, own[u][va][1] if va in own[u] else int(size.group(1), 16), "FUNC",
                        own[u][va][2] if va in own[u] else 1)
            else:
                labels[u][va] = name
        for va, (name, size, shndx) in own[u].items():          # a function the .txt does not list
            if va not in named:
                add(u, name, va, size, "FUNC", shndx)
    data = load(up / "config" / "symbols" / "data-names.json") or {}
    for u, table in (data.get("units") or {}).items():
        if u in units:
            for va, name in table.items():
                labels[u].setdefault(int(va, 16), name)
    for u in units:
        for va, name in sorted(labels[u].items()):
            o = owner(u, va)
            if o:
                add(o, name, va, 0, "OBJECT")
    for ld in sorted((up / "config" / "symbols").glob("*.externals.ld")):
        for m in PROVIDE.finditer(ld.read_text()):                # the tag is a unit, or a library (libgcc)
            o = m.group(3) if m.group(3) in units else owner(resident, int(m.group(2), 16))
            if o:
                add(o, m.group(1), int(m.group(2), 16), 0, "NOTYPE")
    if resident and game_dir and (Path(game_dir) / units[resident]).is_file():
        gp = reginfo_gp((Path(game_dir) / units[resident]).read_bytes())
        if gp:
            add(resident, "_gp", gp, 0, "NOTYPE")
        else:
            warn(f"{units[resident]}: no .reginfo, so no _gp row ($gp-relative references stay unverifiable)")
    return rows


def tool_path(identity: dict, ref, leaf: str):
    """`tool:<name>` (or a bare tool name) -> its file under the toolchains root."""
    tool = (identity.get("tools") or {}).get(str(ref or "").removeprefix("tool:")) or {}
    if tool.get("path"):
        return tool["path"]
    return f"{tool['dir']}/{leaf}" if tool.get("dir") else None


def driver_flags(identity: dict, cc_ref, as_ref) -> str:
    """The cc1 codegen flags (-f*/-m*) of the identity contract pairing these two tools."""
    cc, asm = str(cc_ref or "").removeprefix("tool:"), str(as_ref or "").removeprefix("tool:")
    best = None
    for c in (identity.get("contracts") or {}).values():
        if isinstance(c, dict) and c.get("compiler") == cc:
            if c.get("assembler") == asm:
                best = c
                break
            best = best or c
    return " ".join(f for f in (best or {}).get("driver_flags") or [] if f.startswith(("-f", "-m")))


def target_manifest(up: Path, units: dict, symbols_rel: str, name: str, warn=print) -> dict:
    identity = load(up / "config" / "toolchain-identity.json") or {}
    contracts, tus = {}, []

    def contract(cc_ref, as_ref, options, label):
        cc1, ee_as = tool_path(identity, cc_ref, "cc1"), tool_path(identity, as_ref, "ee-as")
        if not cc1 or not ee_as:
            return None
        opts = [o for o in options if o not in ("-EL", "-m5900", "-mabi=eabi", "-I", ".", "<G>") and not o.startswith("-G")]
        as_flags = " ".join(["-EL", "-m5900", "-mabi=eabi"] + opts + ["-I", "."])
        for cname, c in contracts.items():
            if (c["cc1"], c["as"], c["as_flags"]) == (cc1, ee_as, as_flags):
                return cname
        base, n = label, 2
        while label in contracts:                    # same profile and assembler names, other options:
            label, n = f"{base}#{n}", n + 1          # a contract of its own, never an overwrite
        contracts[label] = {"cc1": cc1, "as": ee_as, "cc1_flags": driver_flags(identity, cc_ref, as_ref), "as_flags": as_flags}
        return label

    for u in units:
        m = load(up / "config" / "objects" / f"{u}.compile.json") or {}
        toolchains = m.get("toolchains") or {}
        for key, v in (m.get("tus") or {}).items() if isinstance(m.get("tus"), dict) else []:
            if not isinstance(v, dict) or v.get("language") != "c" or not v.get("id") or not v.get("path"):
                continue
            prof = toolchains.get(v.get("compiler_profile") or "") or {}
            as_key = str(v.get("assembler") or "").split(" ")[0]
            asm = toolchains.get(as_key) or {}
            options = v.get("assembler_options", asm.get("assembler_options")) or []
            cname = contract(prof.get("cc_dir"), asm.get("path"), options, f"{v.get('compiler_profile')}+{as_key}")
            if cname is None:
                warn(f"{v['id']}: no tool path for {v.get('compiler_profile')} / {as_key}; left out")
                continue
            tus.append({"id": v["id"], "unit": u, "path": v["path"], "in_scope": bool(v.get("in_scope")),
                        "flags": list(v.get("flags") or []), "contract": cname})
    ov = load(up / "config" / "objects" / "overlays.compile.json") or {}
    for u, block in (ov.get("units") or {}).items():
        if u not in units:
            continue
        for v in block.get("tus") or []:
            if not isinstance(v, dict) or v.get("kind") != "c" or not v.get("source") or v.get("ordinal") is None:
                continue
            comp, asm = v.get("compiler") or {}, v.get("assembler") or {}
            as_key = str(asm.get("id") or "").split(" ")[0]
            cname = contract(comp.get("dir"), asm.get("path"), str(asm.get("argv") or "").split(), f"{comp.get('id')}+{as_key}")
            if cname is None:
                warn(f"{u}/tu{int(v['ordinal']):03d}: no tool path for {comp.get('id')} / {as_key}; left out")
                continue
            tus.append({"id": f"{u}/tu{int(v['ordinal']):03d}", "unit": u, "path": v["source"],
                        "in_scope": bool(v.get("in_scope")), "flags": list(v.get("flags") or []), "contract": cname})
    resident = next(iter(units), None)
    return {"schema": "decomp-target/1", "name": name,
            "generated_by": "tools/upstream_target.py (rebuild it; do not edit)",
            "root": "$DECOMP_UPSTREAM", "game_dir": "$DECOMP_GAME_DIR", "toolchains": "$DECOMP_TOOLCHAINS",
            "symbols": symbols_rel, "resident": units.get(resident),
            "units": {u: {"binary": f} for u, f in units.items()},
            "include": ["include", "."], "defines": [],
            "contracts": contracts, "default_contract": next(iter(contracts), None),
            "tus": sorted(tus, key=lambda t: t["id"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upstream", type=Path, required=True, help="the decomp checkout (read only)")
    ap.add_argument("--game-dir", type=Path, help="folder holding the originals (program headers, _gp)")
    ap.add_argument("--symbols-out", type=Path, required=True, help="the symbols CSV to write")
    ap.add_argument("--target-out", type=Path, help="the decomp-target/1 manifest to write")
    ap.add_argument("--name", help="the target's display name (default: the resident original's file name)")
    args = ap.parse_args()
    up = args.upstream.expanduser().resolve()
    warn = lambda m: print(m, file=sys.stderr)
    units = unit_files(up)
    if not units:
        warn(f"{up}/config/symbols/estimated-functions.json names no unit: not a checkout this tool can read")
        return 2
    if not args.game_dir:
        warn("no --game-dir: data labels cannot be placed in a unit and _gp is unknown; only functions are written")
    rows = symbol_rows(up, args.game_dir, units, warn)
    args.symbols_out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.symbols_out, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["binary", "name", "va", "size", "type", "bind", "shndx"])
        w.writerows(sorted(rows, key=lambda r: (list(units.values()).index(r[0]), int(r[2], 16), r[1])))
    count = collections.Counter((r[0], r[4]) for r in rows)
    out = dict(symbols=str(args.symbols_out), rows=len(rows),
               per_binary={b: {t: n for (bb, t), n in sorted(count.items()) if bb == b} for b in units.values()})
    if args.target_out:
        try:
            rel = str(args.symbols_out.resolve().relative_to(args.target_out.resolve().parent))
        except ValueError:
            rel = str(args.symbols_out.resolve())
        manifest = target_manifest(up, units, rel, args.name or units[next(iter(units))], warn)
        args.target_out.write_text(json.dumps(manifest, indent=1) + "\n")
        per_unit = collections.Counter(t["unit"] for t in manifest["tus"])
        out.update(target=str(args.target_out), tus=dict(per_unit), contracts=manifest["contracts"])
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
