#!/usr/bin/env python3
"""Cross-game port check: compile game A's decomp C and judge it against game B's binary.

Two games of one studio and compiler era share engine code. transfer_symbols.py finds
where an A function sits in B (masked shape match); this tool goes one step further and
asks whether A's *source* reproduces B's bytes: it compiles A's C translation units with
the kit's lanes (stock, or the decomp's contract toolchain), places each compiled function
at its B address, and compares word by word masking only the relocated FIELDS (j/jal: the
26-bit target; HI16, LO16, GPREL16, LITERAL: the 16-bit immediate; any other relocation:
the whole word, reported). Addresses legitimately differ between games; every other bit
must not.

Classes per function:
  identical    every word equal once relocated fields are masked: A's C is B's C here
  immediate    same size, every differing word an I-type instruction whose opcode and
               registers agree and only the 16-bit immediate differs (a struct offset, a
               constant): A's C with B's layout
  size-diff    the compiled function and B's differ in length
  other        anything else

  port_check.py --upstream A_DECOMP [--unit main] --map a_to_b.tsv --acceptor B.ELF \\
                [--compiler contract|stock] [--toolchains DIR] [--contract-name NAME] \\
                [--harvest b_symbols.csv] [--out report.json] [-v]
  port_check.py --target A_TARGET.json --unit main --map ... --acceptor ...
  port_check.py --self-test

--map is a transfer_symbols.py TSV (donor name -> acceptor address); only rows with one
acceptor address and no ambiguity are used. --harvest reads every relocated field of the
identical functions out of B (harvest_relocs.py) and writes B's symbol addresses.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = "<"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hr = _load("harvest_relocs")


def load_map(path):
    """{donor name: (acceptor va, donor size)} from a transfer_symbols.py TSV."""
    out = {}
    with open(path) as fh:
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < 4 or p[0] == "name" or not p[3] or "," in p[3] or (len(p) >= 8 and p[7]):
                continue
            out[p[0]] = (int(p[3], 16), int(p[2]))
    return out


def classify_port(obj, fname, retail, donor_size=None):
    """(class, detail) of one compiled function against B's bytes (retail, read at its size)."""
    f = obj.funcs[fname]
    if donor_size is not None and f["size"] != donor_size:
        return "size-diff", f"object {f['size']} B vs {donor_size} B"
    if retail is None or len(retail) < f["size"]:
        return "size-diff", "acceptor bytes unavailable at that size"
    diffs, unknown = hr.masked_compare(obj, f, retail)
    note = f" (relocation types masked whole: {sorted(unknown)})" if unknown else ""
    if not diffs:
        return "identical", f"{sum(1 for o, _t, _s in obj.relocs if f['val'] <= o < f['val'] + f['size'])} relocated fields{note}"
    imm = all(a != b and (a & 0xFFFF0000) == (b & 0xFFFF0000) and (a >> 26) not in (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17, 0x1C, 0x10, 0x11, 0x12, 0x13)
              for _i, a, b, _t in diffs)
    first = ", ".join(f"+{i:#x} {a:08x}/{b:08x}" for i, a, b, _t in diffs[:3])
    return ("immediate" if imm else "other"), f"{len(diffs)} word(s): {first}{note}"


def build(vd, tus, includes, defines, compiler, toolchains, rootfs, work, image, timeout, contract_of):
    """Preprocess and compile TUS in groups of one contract each; {tu id: object path}."""
    groups, objs, skipped = collections.OrderedDict(), {}, []
    for k in ("pp",):
        (work / k).mkdir(parents=True, exist_ok=True)
    tags_seen = set()
    for t in tus:
        tag = t["id"].replace("/", "__")
        if "__" in t["id"] or tag in tags_seen:      # the build files are named by the tag: it must be unique
            sys.exit(f"TU id {t['id']!r}: ids may not contain '__' or repeat")
        tags_seen.add(tag)
        cpp = subprocess.run(["clang", "-E", "-P", "-DSKIP_ASM"] + [f"-D{d}" for d in defines]
                             + [a for d in includes for a in ("-I", str(d))] + [str(t["path"]), "-o", str(work / "pp" / f"{tag}.i")],
                             capture_output=True, text=True)
        if cpp.returncode:
            skipped.append((t["id"], cpp.stderr.strip().splitlines()[:1]))
            continue
        g, extra = vd.split_cflags(t.get("flags") or ["-G8"])
        c = contract_of(t) if compiler == "contract" else None
        key = (c["contract"] if c else "stock", " ".join(extra))
        groups.setdefault(key, (c, extra, []))[2].append(dict(tu=t["id"], tag=tag, g=g or "-G8"))
    for n, (key, (c, extra, js)) in enumerate(groups.items()):
        gw = work / f"g{n}"
        for sub in ("pp", "asm", "obj", "flags"):
            (gw / sub).mkdir(parents=True, exist_ok=True)
        for j in js:
            shutil.copyfile(work / "pp" / f"{j['tag']}.i", gw / "pp" / f"{j['tag']}.i")
            (gw / "flags" / j["tag"]).write_text(j["g"])
        if c is not None:
            c = dict(c, cc1_flags=(c.get("cc1_flags", "") + (" " + " ".join(extra) if extra else "")).strip())
        rc, fails, tail = vd.docker_build(gw, rootfs, js, image, compiler, toolchains, timeout, c,
                                          stock_cc1_flags=" ".join(extra) if compiler != "contract" else "")
        if rc != 0:
            raise RuntimeError(f"docker run failed (rc {rc}): {tail}")
        bad = {l.split()[1] for l in fails if not l.startswith("ASWARN")}
        for j in js:
            o = gw / "obj" / f"{j['tag']}.o"
            if o.exists() and j["tag"] not in bad:
                objs[j["tu"]] = o
            else:
                skipped.append((j["tu"], ["build failed"]))
    return objs, skipped


def run(args) -> int:
    vd = _load("verify_decomp")
    mapping = load_map(args.map)
    img = hr.Image(args.acceptor)
    rootfs = Path(args.rootfs).expanduser()
    if args.target:
        target = vd.Target(args.target)
        tus = [t for t in target.tus(args.unit) if t["mode"] == "c" and t["in_scope"]]
        includes, defines = target.include or [target.root], target.defines
        toolchains = Path(args.toolchains).expanduser() if args.toolchains else target.toolchains
        contract_of = lambda t: target.contract(t.get("contract"))       # noqa: E731
        compiler = args.compiler or ("contract" if target.contracts else "stock")
    else:
        up = Path(args.upstream).expanduser()
        tus = [dict(t, path=str(up / t["path"])) for t in vd.discover_tus(up, args.unit)
               if t.get("mode") == "c" and t.get("in_scope")]
        includes, defines = [up / "include", up / f"src/{args.unit}", up], []
        toolchains = Path(args.toolchains).expanduser() if args.toolchains else vd.default_toolchains(up)
        contract = vd.resolve_contract(toolchains, up, {}, args.contract_name) if toolchains else None
        compiler = args.compiler or ("contract" if contract else "stock")
        if compiler == "contract" and contract is None:
            print("contract toolchain not found (--toolchains / --contract-name)", file=sys.stderr)
            return 2
        contract_of = lambda t: contract                                 # noqa: E731
    work = Path(args.work).expanduser() / f"run-{os.getpid()}-{int(time.time() * 1000)}"
    work.mkdir(parents=True)
    print(f"work: {work}\n{len(tus)} TU(s), {len(mapping)} mapped names, compiler {compiler}")
    objs, skipped = build(vd, tus, includes, defines, compiler, toolchains, rootfs, work, args.image,
                          args.docker_timeout, contract_of)
    rows, totals, units = [], collections.Counter(), []
    for tu, path in objs.items():
        obj = hr.ObjFile(path)
        placed = {}
        for fname in obj.funcs:
            if fname not in mapping:
                continue
            va, donor_size = mapping[fname]
            retail = img.read(va, obj.funcs[fname]["size"])
            kind, detail = classify_port(obj, fname, retail, donor_size)
            totals[kind] += 1
            rows.append(dict(tu=tu, func=fname, va=f"{va:#x}", size=obj.funcs[fname]["size"], kind=kind, detail=detail))
            if kind == "identical" or (kind == "immediate" and args.harvest_accept == "immediate"):
                placed[fname] = va
        units.append((tu, obj, placed))
    print(f"compared {len(rows)} function(s): " + "  ".join(f"{k} {totals[k]}" for k in
                                                         ("identical", "immediate", "size-diff", "other")))
    if args.verbose:
        for r in sorted(rows, key=lambda r: (r["kind"], r["tu"], r["func"])):
            print(f"  {r['kind']:<10} {r['tu']:<14} {r['func']:<32} {r['va']:<10} {r['detail']}")
    report = dict(upstream=str(args.upstream) if args.upstream else None, target=str(args.target) if args.target else None,
                  unit=args.unit, acceptor=str(args.acceptor), compiler=compiler, work=str(work), totals=dict(totals),
                  functions=rows, skipped=[dict(tu=t, why=w) for t, w in skipped])
    if args.harvest:
        res = hr.harvest(units, img, accept=args.harvest_accept)
        sm = hr.summarize(res)
        n = hr.write_csv(res, args.harvest, Path(args.acceptor).name)
        report["harvest"] = dict(sm, csv=str(args.harvest), rows=n,
                                 conflicts={str(k): {hex(a): c for a, c in v.items()}
                                            for k, v in res["symbols"].items() if len(v) > 1},
                                 sections={f"{k[1]} {k[2]}": [hex(a) for a in v]
                                           for k, v in res["symbols"].items() if k[0] == "S"})
        print(f"harvest: {json.dumps(sm)} -> {args.harvest} ({n} rows)")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))
        print(f"wrote {args.out}")
    return 0


def self_test() -> int:
    checks = {}
    words = [0x3C040000, 0x24840000, 0x0C000000, 0x00000000, 0x8C820010, 0x03E00008, 0x00000000]
    syms = [("f", 0, 28, hr.STT_FUNC, 1, 1), ("gvar", 0, 0, hr.STT_NOTYPE, 1, 0), ("func", 0, 0, hr.STT_NOTYPE, 1, 0)]
    relocs = [(0, hr.R_MIPS_HI16, 2), (4, hr.R_MIPS_LO16, 2), (8, hr.R_MIPS_26, 3)]
    obj = hr.ObjFile(hr._elf_rel(words, relocs, syms))

    def img(ws):
        return hr.Image(hr._elf_exec(0x200000, b"".join(struct.pack(E + "I", x) for x in ws)))
    same = [0x3C040045, 0x24846780, 0x0C048D14, 0, 0x8C820010, 0x03E00008, 0]
    checks["identical"] = classify_port(obj, "f", img(same).read(0x200000, 28), 28)[0] == "identical"
    moved = list(same)
    moved[4] = 0x8C820124                       # lw v0,0x124(a0): a struct field moved
    checks["immediate"] = classify_port(obj, "f", img(moved).read(0x200000, 28), 28)[0] == "immediate"
    reg = list(same)
    reg[4] = 0x8C830010                         # another destination register: not an immediate
    checks["other"] = classify_port(obj, "f", img(reg).read(0x200000, 28), 28)[0] == "other"
    bw = list(words)
    bw[4] = 0x10800002                          # beq a0,zero,+2 here, +5 there: other control flow
    br = list(same)
    br[4] = 0x10800005
    checks["branch-not-immediate"] = classify_port(hr.ObjFile(hr._elf_rel(bw, relocs, syms)), "f",
                                                   img(br).read(0x200000, 28), 28)[0] == "other"
    checks["size-diff"] = classify_port(obj, "f", img(same).read(0x200000, 28), 32)[0] == "size-diff"
    # the masks are FIELD masks: a different opcode under a relocation is still a difference
    jop = list(same)
    jop[2] = 0x08048D14                         # j instead of jal: same target field
    checks["field-not-word"] = classify_port(obj, "f", img(jop).read(0x200000, 28), 28)[0] != "identical"
    lit = hr.ObjFile(hr._elf_rel([0xC7808004, 0x03E00008, 0], [(0, hr.R_MIPS_LITERAL, 1)],
                                 [("g", 0, 12, hr.STT_FUNC, 1, 1)]))
    checks["literal-field"] = (classify_port(lit, "g", img([0xC7801234, 0x03E00008, 0]).read(0x200000, 12), 12)[0] == "identical"
                               and classify_port(lit, "g", img([0xC7811234, 0x03E00008, 0]).read(0x200000, 12), 12)[0] != "identical")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "m.tsv"
        p.write_text("name\tdonor_va\tsize\tacceptor_vas\tidentical\ttotal\tconfidence\tambiguous\n"
                     "f\t0x100\t28\t0x200000\t7\t7\thigh\t\ng\t0x200\t8\t0x1,0x2\t2\t2\tmed\t\nh\t0x300\t8\t0x5\t2\t2\tmed\tx\n")
        checks["map"] = load_map(p) == {"f": (0x200000, 28)}
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--upstream", help="game A's decomp checkout (xsg-i-decomp layout)")
    src.add_argument("--target", help="game A as a decomp-target/1 manifest")
    ap.add_argument("--unit", default="main")
    ap.add_argument("--map", type=Path, help="transfer_symbols.py TSV: A name -> B address")
    ap.add_argument("--acceptor", type=Path, help="game B's ELF")
    ap.add_argument("--compiler", choices=("stock", "contract"))
    ap.add_argument("--toolchains", help="contract toolchains directory")
    ap.add_argument("--contract-name", help="contract of the upstream's toolchain-identity.json to use")
    ap.add_argument("--rootfs", default=os.environ.get("EE_ROOTFS", str(Path.home() / "ee-rootfs")))
    ap.add_argument("--work", default=str(Path.home() / ".cache" / "decomp-port"))
    ap.add_argument("--image", default=os.environ.get("DECOMP_IMAGE", "debian:bookworm-slim"))
    ap.add_argument("--docker-timeout", type=int, default=1800)
    ap.add_argument("--harvest", type=Path, help="write B's symbol addresses read at the identical functions")
    ap.add_argument("--harvest-accept", choices=("identical", "immediate"), default="identical",
                    help="also harvest from immediate-only functions (their relocated fields are unaffected)")
    ap.add_argument("--out", type=Path, help="JSON report")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.upstream or a.target) or not a.map or not a.acceptor:
        ap.error("--upstream or --target, --map and --acceptor are required")
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
