#!/usr/bin/env python3
"""The Tier-A ledger of a decomp target: which functions are byte-identical, and the proof on demand.

A row of the ledger (the Xenosaga III convention, `src/matched.tsv`) says a function of a
unit compiles, from the named source, to exactly the original's bytes: every word equal
with every relocation APPLIED from the target's symbol table and the TU's annotations
(@addr, @rodata, @cflags, @data/@sdata/@bss/@sbss), nothing masked, same length. The
ledger is data; this tool re-proves it.

  ledger.py check SRC.c [--add]      compile the TU holding SRC.c, print every function's verdict;
                                     --add appends its Tier-A functions to the ledger
  ledger.py promote REPORT.json      append the Tier-A functions of a target-mode verify report
  ledger.py all [--unit U]           re-verify every row (exit 0 only when every row is still Tier A)
  ledger.py list
  ledger.py sibling NAME [--unit U] [--remap] [--harvest DIR]
                                     the ledger against another build of the same program (the
                                     manifest's `siblings`): compile every TU holding a row,
                                     place each row's function at its address in that build
                                     and compare with relocated FIELDS masked (port_check.py).
                                     A row the sibling cannot reproduce is worth a look: either
                                     the code changed between builds or the match is luck.
                                     --remap rebuilds the address map first (transfer_symbols.py
                                     --sibling); --harvest writes the sibling's symbol addresses
  ledger.py --self-test

The target is --target or DECOMP_TARGET (a decomp-target/1 manifest naming its `ledger`).
Columns: unit, address, function, bytes, source (relative to the manifest), lane, verified.
"""
from __future__ import annotations

import argparse
import argparse as _ap
import collections
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEADER = ("# Tier-A (byte-identical, relocations applied) functions. Re-verify: tools/ledger.py all\n"
          "# unit\taddr\tfunction\tbytes\tsource\tlane\tverified\n")


def verifier():
    spec = importlib.util.spec_from_file_location("verify_decomp", HERE / "verify_decomp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_ledger(path: Path) -> list:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        p = line.split("\t")
        p += [""] * (7 - len(p))
        rows.append(dict(unit=p[0], addr=p[1], function=p[2], bytes=p[3], source=p[4], lane=p[5] or "contract",
                         verified=p[6]))
    return rows


def write_ledger(path: Path, rows: list):
    rows = sorted(rows, key=lambda r: (r["unit"], int(r["addr"], 16)))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")        # written whole, then swapped in: never a half ledger
    tmp.write_text(HEADER + "".join(f"{r['unit']}\t{r['addr']}\t{r['function']}\t{r['bytes']}\t{r['source']}\t"
                                    f"{r['lane']}\t{r['verified']}\n" for r in rows))
    os.replace(tmp, path)


def verify(vd, target, unit, tus, compiler, args):
    """Run verify_decomp's target mode on TUS of UNIT; the JSON report."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "r.json"
        ns = _ap.Namespace(unit=unit, binary=None, binary_given=False, symbols=None, symbols_given=False,
                           toolchains=None, compiler=compiler,
                           rootfs=Path(os.environ.get("EE_ROOTFS", Path.home() / "ee-rootfs")).expanduser(),
                           baseline=None, work=Path(args.work).expanduser(), docker_timeout=args.docker_timeout,
                           tu=sorted(tus), define=[], image=args.image, verbose=False, out=out)
        rc = vd.run_target(ns, target)
        if not out.is_file():
            raise SystemExit(f"verify produced no report (rc {rc})")
        return json.loads(out.read_text())


def tu_of_source(target, src: Path):
    src = Path(src).resolve()
    for unit in target.units:
        for t in target.tus(unit):
            if Path(t["path"]).resolve() == src:
                return unit, t
    raise SystemExit(f"{src} is not a TU of {target.path}")


def tier_a_rows(report, target, lane, stamp):
    rows = []
    for tu, funcs in (report.get("tier_a") or {}).items():
        t = next((x for x in target.tus(report["unit"]) if x["id"] == tu), None)
        src = str(Path(t["path"]).resolve().relative_to(Path(target.root).resolve())) if t else tu
        for f in funcs:
            rows.append(dict(unit=report["unit"], addr=f["va"], function=f["name"], bytes=str(f["size"]),
                             source=src, lane=lane, verified=stamp))
    return rows


def merge(existing, new):
    key = {(r["unit"], int(r["addr"], 16)): r for r in existing}
    added = 0
    for r in new:
        k = (r["unit"], int(r["addr"], 16))
        if k not in key:
            added += 1
        key[k] = r
    return list(key.values()), added


def cmd_check(args, vd, target):
    unit, t = tu_of_source(target, args.src)
    rep = verify(vd, target, unit, [t["id"]], args.compiler, args)
    tier = {f["name"] for fs in (rep.get("tier_a") or {}).values() for f in fs}
    for f in sorted(tier):
        print(f"  A     {f}")
    for m in rep.get("mismatches") or []:
        print(f"  {m['kind']:<11} {m['func']}  {m['detail']}")
    for sk in rep.get("function_skips") or []:
        print(f"  skip  {sk['func']}  {sk['why']}")
    print(f"{t['id']}: {len(tier)} Tier A")
    if args.add and tier:
        rows, added = merge(read_ledger(target.ledger), tier_a_rows(rep, target, rep["compiler"], time.strftime("%Y-%m-%d")))
        write_ledger(target.ledger, rows)
        print(f"ledger: +{added} row(s) -> {target.ledger}")
    return 0 if tier else 1


def cmd_promote(args, vd, target):
    rep = json.loads(Path(args.report).read_text())
    if rep.get("target") and Path(rep["target"]).resolve() != target.path:
        print(f"note: the report is for {rep['target']}", file=sys.stderr)
    rows, added = merge(read_ledger(target.ledger), tier_a_rows(rep, target, rep.get("compiler", "contract"),
                                                                time.strftime("%Y-%m-%d")))
    write_ledger(target.ledger, rows)
    print(f"ledger: +{added} row(s), {len(rows)} total -> {target.ledger}")
    return 0


def cmd_all(args, vd, target):
    rows = read_ledger(target.ledger)
    if args.unit:
        rows = [r for r in rows if r["unit"] == args.unit]
    if not rows:
        print("empty ledger")
        return 1
    bad, results = 0, []
    by_unit_lane = collections.defaultdict(list)
    for r in rows:
        by_unit_lane[(r["unit"], r["lane"])].append(r)
    for (unit, lane), rs in sorted(by_unit_lane.items()):
        tus, tu_of_row = set(), {}
        for r in rs:
            _u, t = tu_of_source(target, target.root / r["source"])
            tus.add(t["id"])
            tu_of_row[id(r)] = t["id"]
        rep = verify(vd, target, unit, tus, lane if lane in ("contract", "stock") else "contract", args)
        # a row is proven only by its own recorded source: the same name in another TU does not count
        tier = {(tu, f["name"], int(f["va"], 16), int(f["size"])) for tu, fs in (rep.get("tier_a") or {}).items() for f in fs}
        why = {m["func"]: m["kind"] for m in rep.get("mismatches") or []}
        why.update({s["func"]: "skip" for s in rep.get("function_skips") or []})
        for r in rs:
            ok = (tu_of_row[id(r)], r["function"], int(r["addr"], 16), int(r["bytes"])) in tier
            bad += not ok
            results.append((r["function"], unit, r["bytes"], lane, "A" if ok else f"FAIL ({why.get(r['function'], 'not emitted')})"))
    for res in results:
        print("  " + "  ".join(str(x).ljust(w) for x, w in zip(res, (32, 6, 6, 9, 20))))
    print(f"{len(results) - bad}/{len(results)} ledger entries verified Tier A")
    return 1 if bad else 0


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def remap(target, name, unit, sib) -> int:
    """Rebuild SIB's map: target unit UNIT's functions placed in the sibling binary."""
    import subprocess
    donor = target.binary(unit)
    cmd = [sys.executable, str(HERE / "transfer_symbols.py"), "--sibling", "--donor", str(donor),
           "--donor-symbols", str(target.symbols), "--donor-binary", donor.name, "--acceptor", str(sib["binary"]),
           "--min-size", "8", "--anchor-words", "2", "-o", str(sib["map"])]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(f"  {unit}: " + " | ".join(l.strip() for l in r.stdout.splitlines() if l.strip()))
    if r.returncode:
        print(r.stderr, file=sys.stderr)
    return r.returncode


def sibling_verdicts(rows, report, mapping):
    """[(row, verdict, detail)] for ledger ROWS from a port_check report; a row whose
    function the map could not place is `unmapped`, one never compiled `not emitted`."""
    by = {f["func"]: f for f in report.get("functions") or []}
    out = []
    for r in rows:
        f = by.get(r["function"])
        if r["function"] not in mapping:
            out.append((r, "unmapped", "no unique, order-consistent address in the sibling"))
        elif f is None:
            out.append((r, "not emitted", ""))
        else:
            out.append((r, f["kind"], f"{f['va']} {f['detail']}"))
    return out


def cmd_sibling(args, vd, target):
    pc = _load_tool("port_check")
    rows = read_ledger(target.ledger)
    if args.unit:
        rows = [r for r in rows if r["unit"] == args.unit]
    units = sorted({r["unit"] for r in rows})
    note = (target.siblings.get(args.name) or {}).get("note")
    print(f"sibling {args.name}" + (f": {note}" if note else ""))
    results = []
    for unit in units:
        sib = target.sibling(args.name, unit)
        urows = [r for r in rows if r["unit"] == unit]
        if sib is None:
            results += [(r, "no unit", f"{args.name} has no {unit}") for r in urows]
            continue
        if args.remap or not sib["map"].is_file():
            if remap(target, args.name, unit, sib):
                return 2
        lanes = {r["lane"] for r in urows}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "port.json"
            harvest = Path(args.harvest).expanduser() / f"{args.name}_{unit}.csv" if args.harvest else None
            if harvest:
                harvest.parent.mkdir(parents=True, exist_ok=True)
            ns = _ap.Namespace(upstream=None, target=str(target.path), unit=unit, map=sib["map"],
                               acceptor=sib["binary"], compiler="stock" if lanes == {"stock"} else "contract",
                               toolchains=None, contract_name=None,
                               rootfs=os.environ.get("EE_ROOTFS", str(Path.home() / "ee-rootfs")),
                               work=args.work, image=args.image, docker_timeout=args.docker_timeout,
                               harvest=harvest, harvest_accept="identical", out=out, verbose=False)
            pc.run(ns)
            rep = json.loads(out.read_text())
        results += sibling_verdicts(urows, rep, pc.load_map(sib["map"]))
    tally = collections.Counter(v for _r, v, _d in results)
    for r, v, d in results:
        if v != "identical" or args.verbose:
            print(f"  {v:<11} {r['unit']:<5} {r['function']:<32} {d}")
    print(f"{tally['identical']}/{len(results)} ledger row(s) identical in {args.name} (relocated fields masked): "
          + ", ".join(f"{k} {n}" for k, n in sorted(tally.items())))
    bad = sum(n for k, n in tally.items() if k not in ("identical", "unmapped", "no unit"))
    return 1 if bad else 0


def cmd_list(args, vd, target):
    rows = read_ledger(target.ledger)
    per = collections.Counter(r["unit"] for r in rows)
    for r in rows:
        print(f"  {r['unit']:<5} {r['addr']:<10} {r['function']:<32} {r['bytes']:>5}  {r['source']}  {r['lane']}  {r['verified']}")
    print(f"{len(rows)} row(s): " + ", ".join(f"{u} {n}" for u, n in sorted(per.items())))
    return 0


def self_test() -> int:
    checks = {}
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "m.tsv"
        rows = [dict(unit="main", addr="0x120010", function="b", bytes="8", source="src/a.c", lane="contract", verified="d"),
                dict(unit="main", addr="0x120000", function="a", bytes="16", source="src/a.c", lane="contract", verified="d")]
        write_ledger(p, rows)
        back = read_ledger(p)
        checks["roundtrip-sorted"] = [r["function"] for r in back] == ["a", "b"] and back[0]["bytes"] == "16"
        merged, added = merge(back, [dict(rows[0], verified="e"), dict(rows[0], addr="0x120020", function="c")])
        checks["merge"] = added == 1 and len(merged) == 3 and \
            next(r for r in merged if r["function"] == "b")["verified"] == "e"
        p.write_text("# old XS3 ledger\n# bin\taddr\tfunction\tbytes\tsource\nov04\t0x00a919c8\tFUN_00a919c8\t1120\tsrc/hakox.c\n")
        old = read_ledger(p)
        checks["xs3-columns"] = old[0]["unit"] == "ov04" and old[0]["lane"] == "contract" and old[0]["source"] == "src/hakox.c"
        rep = dict(unit="main", tier_a={"main/a": [dict(name="a", va="0x120000", size=16)]})

        class T:
            root = Path(tmp)
            def tus(self, unit):
                return [dict(id="main/a", path=str(Path(tmp) / "src" / "a.c"))]
        checks["tier-rows"] = tier_a_rows(rep, T(), "contract", "x") == [dict(
            unit="main", addr="0x120000", function="a", bytes="16", source="src/a.c", lane="contract", verified="x")]
    lrows = [dict(unit="main", function=n, lane="contract") for n in ("a", "b", "c", "d")]
    rep = dict(functions=[dict(func="a", kind="identical", va="0x1", detail=""),
                          dict(func="b", kind="immediate", va="0x2", detail="1 word(s)")])
    got = [v for _r, v, _d in sibling_verdicts(lrows, rep, {"a": 1, "b": 2, "c": 3})]
    checks["sibling-verdicts"] = got == ["identical", "immediate", "not emitted", "unmapped"]
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=os.environ.get("DECOMP_TARGET"), help="decomp-target/1 manifest")
    ap.add_argument("--compiler", choices=("contract", "stock"), default="contract")
    ap.add_argument("--work", default=str(Path.home() / ".cache" / "decomp-ledger"))
    ap.add_argument("--image", default=os.environ.get("DECOMP_IMAGE", "debian:bookworm-slim"))
    ap.add_argument("--docker-timeout", type=int, default=1800)
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("src", type=Path)
    c.add_argument("--add", action="store_true")
    pr = sub.add_parser("promote")
    pr.add_argument("report")
    al = sub.add_parser("all")
    al.add_argument("--unit")
    sub.add_parser("list")
    sb = sub.add_parser("sibling")
    sb.add_argument("name")
    sb.add_argument("--unit")
    sb.add_argument("--remap", action="store_true")
    sb.add_argument("--harvest")
    sb.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.cmd:
        ap.error("a command is required (check | promote | all | list | sibling) or --self-test")
    if not a.target:
        ap.error("--target or DECOMP_TARGET is required")
    vd = verifier()
    target = vd.Target(a.target)
    if target.ledger is None:
        ap.error(f"{target.path} names no `ledger`")
    return dict(check=cmd_check, promote=cmd_promote, all=cmd_all, list=cmd_list,
                sibling=cmd_sibling)[a.cmd](a, vd, target)


if __name__ == "__main__":
    sys.exit(main())
