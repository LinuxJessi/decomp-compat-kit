#!/usr/bin/env python3
"""ps2decomp: the PS2-Decomp Compatibility Kit as an MCP server.

Every tool shells out to the kit's own scripts (tools/*.py) with this interpreter,
so a verdict reached through this server is the same verdict the command line
gives, and the rules the kit enforces (anti-cheat lint, the no-progress stop, the
UNVERIFIABLE stop, the 2 KB card) stay on the server side where a model cannot
argue with them. Nothing here reads a game binary into a tool result: tools return
analyses, verdicts and diffs.

Configuration is environment only (the 2026-07-28 protocol deprecates roots; paths
never come from the model):

  DECOMP_UPSTREAM     the decomp checkout (xsg-i-decomp layout)
  DECOMP_GAME_DIR     folder holding the original executables
  DECOMP_BINARY       the original's file name (default SLUS_204.69)
  DECOMP_UNIT         the upstream unit (default main)
  DECOMP_RESIDENT     the always-loaded image an overlay calls into (default SLUS_204.69)
  EE_ROOTFS           the i386 rootfs with the stock ee-gcc 2.96 under opt/ee
  DECOMP_TOOLCHAINS   the decomp's pinned (contract) toolchains, optional
  DECOMP_SANDBOXES    where match sandboxes live (default ~/.cache/decomp-match)
  DECOMP_JOBS         where background job logs live (default ~/.cache/decomp-jobs)

Run:  .venv/bin/python mcp/server.py            (stdio)
      claude mcp add ps2decomp -e DECOMP_UPSTREAM=... -- .venv/bin/python mcp/server.py

Tools, by pipeline stage: doctor | identify_fingerprint, identify_unwrap |
name_lookup, name_transfer_symbols | triage_function, triage_unit |
probe_toolchain_start | verify_tu, verify_unit_start | job_status |
match_init, match_card, match_disasm, match_source, match_write_function,
match_edit, match_check, match_verdict, match_best, match_patch, match_list.
Resources: kit://docs/{name}, kit://sandbox/{function}/card, kit://sandbox/{function}/budget.
Prompt: match_one_function.

Long-running work (a whole-unit verification, the toolchain probe) returns a job
handle at once and is polled with job_status: the protocol's tasks extension is
not in the Python SDK yet, and a handle passed as an ordinary argument is what the
stateless 2026-07-28 revision asks for anyway.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

KIT = Path(__file__).resolve().parents[1]
TOOLS = KIT / "tools"
PYTHON = sys.executable
SANDBOXES = Path(os.environ.get("DECOMP_SANDBOXES", Path.home() / ".cache" / "decomp-match")).expanduser()
JOBS_DIR = Path(os.environ.get("DECOMP_JOBS", Path.home() / ".cache" / "decomp-jobs")).expanduser()
UPSTREAM = Path(os.environ["DECOMP_UPSTREAM"]).expanduser() if os.environ.get("DECOMP_UPSTREAM") else None
GAME = Path(os.environ.get("DECOMP_GAME_DIR", ".")).expanduser()
BINARY = os.environ.get("DECOMP_BINARY", "SLUS_204.69")
UNIT = os.environ.get("DECOMP_UNIT", "main")
ROOTFS = Path(os.environ.get("EE_ROOTFS", Path.home() / "ee-rootfs")).expanduser()
SYMBOLS = KIT / "symbols" / "symbols.csv"
DOCKER_IMAGE = os.environ.get("DECOMP_IMAGE", "debian:bookworm-slim")   # verify_decomp.py --image: passed to every verify run and to the probe
TEXT_LIMIT = 12000            # characters of tool stdout returned before truncation
DOCS = {"playbook": "MATCHING-PLAYBOOK.md", "toolchain-differences": "TOOLCHAIN-DIFFERENCES.md",
        "corpus": "CORPUS.md", "readme": "../README.md"}

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
LOCAL_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

server = MCPServer(
    "ps2decomp",
    title="PS2-Decomp Compatibility Kit",
    version="0.1.0",
    instructions=(
        "Tools for PS2 matching decompilation. The matching loop: match_init (one sandbox per "
        "function) -> match_card / match_disasm (evidence) -> match_write_function (your C) -> "
        "match_check with one changed hypothesis per call -> repeat until MATCH, a NO PROGRESS or "
        "ceiling stop, or an UNVERIFIABLE stop -> match_verdict -> match_patch. The tool decides "
        "exactness; never claim a match the check did not print. Read kit://docs/playbook once."),
)


# ---------------------------------------------------------------- plumbing

def env():
    e = dict(os.environ)
    e.setdefault("DECOMP_SANDBOXES", str(SANDBOXES))
    return e


def run(tool: str, args: list[str], timeout: int = 600) -> dict[str, Any]:
    """Run one kit tool with this interpreter; return rc, stdout, stderr (bounded)."""
    cmd = [PYTHON, "-B", str(TOOLS / tool)] + [str(a) for a in args]
    try:
        done = subprocess.run(cmd, cwd=str(KIT), env=env(), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return dict(rc=124, stdout="", stderr=f"timed out after {timeout} s", command=shlex.join(cmd))
    out, err = done.stdout, done.stderr
    truncated = len(out) > TEXT_LIMIT
    return dict(rc=done.returncode, stdout=out[:TEXT_LIMIT], stderr=err[-3000:], truncated=truncated,
                command=shlex.join(cmd))


def parse_json_tail(text: str):
    """The last JSON object a tool printed, if any."""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    try:
        return json.loads(text)
    except ValueError:
        return None


def sandbox_path(function_or_path: str) -> Path:
    p = Path(function_or_path).expanduser()
    if p.is_absolute():
        return p
    return SANDBOXES / function_or_path


def budget_of(sandbox: Path) -> dict:
    return json.loads((sandbox / "budget.json").read_text())


def match_kit_module():
    spec = importlib.util.spec_from_file_location("match_kit", TOOLS / "match_kit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def toolchains_dir() -> Path | None:
    """DECOMP_TOOLCHAINS, else <upstream>/.work/toolchains: the same rule as verify_decomp.py."""
    tc = os.environ.get("DECOMP_TOOLCHAINS") or (str(UPSTREAM / ".work" / "toolchains") if UPSTREAM else None)
    return Path(tc).expanduser() if tc else None


_PROBED: dict[tuple[str, str], bool] = {}     # (resolved path, image) -> True: successes only, per process


def docker_sees(path: Path, test: str, timeout: int = 180) -> dict[str, Any]:
    """Mount PATH read-only into the kit's box (the same `-v` bind and `--platform linux/386`
    verify_decomp.py uses, so the probe sees what the build sees) and run the shell TEST
    there. A directory that exists on the host can still be empty inside the box: on the
    first machine colima shared /Users but not the separate volume mounted at ~/kosmos,
    and the contract cc1 then failed with "cannot open shared object file" (rc 127) on
    every TU. `kind` tells a failed probe ("unshared") from docker itself failing
    ("docker": no daemon, no image after the pull timeout). Successes are cached for the
    process: the mount topology does not change under a running server, and a probe is
    a container start (seconds; the first one may also pull the image)."""
    key = (str(path.resolve()), DOCKER_IMAGE)
    if _PROBED.get(key):
        return dict(ok=True, cached=True)
    cmd = ["docker", "run", "--rm", "--platform", "linux/386", "-v", f"{path.resolve()}:/probe:ro",
           DOCKER_IMAGE, "/bin/sh", "-c", test]
    try:
        d = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return dict(ok=False, kind="docker", error="docker: command not found")
    except subprocess.TimeoutExpired:
        return dict(ok=False, kind="docker", error=f"docker probe exceeded {timeout} s (a first run pulls {DOCKER_IMAGE}; "
                                                    "run `docker pull --platform linux/386 " + DOCKER_IMAGE + "` and retry)")
    if d.returncode == 0:
        _PROBED[key] = True
        return dict(ok=True)
    err = d.stderr.strip()[-300:]
    if err and "docker" in err.lower() and "daemon" in err.lower():
        return dict(ok=False, kind="docker", error=err)
    return dict(ok=False, kind="unshared", error=err or f"`{test}` failed inside the box")


def contract_probe() -> str:
    """The shell test a usable toolchains mount passes: the kit-default pair verify_decomp.py
    prefers (its CONTRACT_CC1/CONTRACT_AS), or else any pinned `<name>/bin/cc1` for a decomp
    whose contract comes from config/toolchain-identity.json."""
    try:
        spec = importlib.util.spec_from_file_location("verify_decomp", TOOLS / "verify_decomp.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        pair = f"( test -r /probe/{shlex.quote(mod.CONTRACT_CC1)} && test -r /probe/{shlex.quote(mod.CONTRACT_AS)} ) || "
    except Exception:
        pair = ""
    return pair + "ls /probe/*/bin/cc1 >/dev/null 2>&1"


def contract_blocker() -> str | None:
    """None when a contract build can run; otherwise why it cannot, so verify_* and match_init
    refuse up front instead of reporting every TU as BUILD FAILED."""
    tc = toolchains_dir()
    if not tc or not tc.is_dir():
        return f"no toolchains directory ({tc}): set DECOMP_TOOLCHAINS to the decomp's pinned toolchains"
    seen = docker_sees(tc, contract_probe())
    if seen["ok"]:
        return None
    if seen.get("kind") == "docker":
        return f"docker cannot run the kit's box: {seen['error']}"
    return (f"{tc} holds no pinned cc1 visible inside the docker box ({seen['error']}): the directory is not "
            "shared with docker (on macOS/colima only /Users is, not a separate volume mounted under it); "
            "point DECOMP_TOOLCHAINS at a copy on the shared disk")


# ---------------------------------------------------------------- doctor

@server.tool(annotations=READ_ONLY, description="Report the server's configuration and whether each dependency "
             "(upstream checkout, originals, symbol table, stock rootfs, contract toolchains, docker, rabbitizer) "
             "is in place. Call first when any other tool fails with a tooling error.")
def doctor() -> dict[str, Any]:
    checks = {}
    checks["upstream"] = dict(path=str(UPSTREAM), ok=bool(UPSTREAM and (UPSTREAM / "config" / "tu-build.json").is_file()
                                                           or (UPSTREAM and (UPSTREAM / "config" / "objects").is_dir())))
    checks["game_dir"] = dict(path=str(GAME), binary=BINARY, ok=(GAME / BINARY).is_file())
    checks["unit"] = UNIT
    checks["symbols"] = dict(path=str(SYMBOLS), ok=SYMBOLS.is_file())
    checks["rootfs"] = dict(path=str(ROOTFS), ok=(ROOTFS / "lib" / "ld-linux.so.2").is_file()
                            and (ROOTFS / "opt" / "ee" / "bin" / "ee-as").is_file())
    manifest = UPSTREAM / "config" / "tu-build.json" if UPSTREAM else None
    checks["manifest"] = dict(path=str(manifest), ok=bool(manifest and manifest.is_file()),
                              note=None if manifest and manifest.is_file() else
                              "verify_* and match_init discover TUs from config/tu-build.json + config/tu/<unit>/; "
                              "upstream trees without them (Opok's) need the files copied in (git ignores them)")
    tc = toolchains_dir()
    checks["toolchains"] = dict(path=str(tc) if tc else None, ok=bool(tc and tc.is_dir()))
    try:
        d = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=20)
        checks["docker"] = dict(ok=d.returncode == 0, version=d.stdout.strip() or d.stderr.strip()[-200:])
    except (OSError, subprocess.TimeoutExpired) as e:
        checks["docker"] = dict(ok=False, error=str(e))
    if checks["toolchains"]["ok"] and checks["docker"]["ok"]:
        # The host-side is_dir() is not enough: the box must see the pinned cc1 through the mount.
        seen = docker_sees(tc, contract_probe())
        checks["toolchains"]["visible_in_docker"] = seen["ok"]
        if not seen["ok"]:
            checks["toolchains"]["ok"] = False
            checks["toolchains"]["error"] = seen["error"]
            checks["toolchains"]["note"] = ("contract builds would fail on every TU; point DECOMP_TOOLCHAINS at a "
                                            "directory docker shares (on macOS/colima: under /Users, not a separate volume)")
    try:
        import rabbitizer  # noqa: F401
        checks["rabbitizer"] = dict(ok=True)
    except ImportError:
        checks["rabbitizer"] = dict(ok=False, note="pip install rabbitizer==1.16.2 into the kit venv; match_init refuses without it")
    checks["sandboxes"] = str(SANDBOXES)
    checks["python"] = PYTHON
    ok = all(v.get("ok", True) if isinstance(v, dict) else True for v in checks.values())
    return dict(ok=ok, checks=checks)


# ---------------------------------------------------------------- identify

@server.tool(annotations=READ_ONLY, description="Which compiler built a PS2 code image (ELF, MWo3 overlay, or an "
             "unwrapped .image with its sidecar): vendor strings, container tells, prologue census, idioms, SDK banners. "
             "Returns the tool's JSON report.")
def identify_fingerprint(path: str, base: str | None = None, verbose: bool = False) -> dict[str, Any]:
    args = [path, "--json"] + (["--base", base] if base else []) + (["-v"] if verbose else [])
    r = run("fingerprint_elf.py", args, timeout=300)
    return dict(report=parse_json_tail(r["stdout"]), rc=r["rc"], stderr=r["stderr"])


@server.tool(annotations=LOCAL_WRITE, description="Describe (or unwrap into out_dir) a code image that is not a plain "
             "ELF: a zlib-stub boot ELF, a Metrowerks MWo3 overlay, or a plain ELF flattened. With out_dir it writes "
             "<name>.image and <name>.json there.")
def identify_unwrap(path: str, out_dir: str | None = None, base: str | None = None) -> dict[str, Any]:
    args = [path] + (["--out", out_dir] if out_dir else ["--describe"]) + (["--base", base] if base else [])
    r = run("unwrap_image.py", args, timeout=300)
    return dict(result=parse_json_tail(r["stdout"]) or r["stdout"], rc=r["rc"], stderr=r["stderr"])


# ---------------------------------------------------------------- name

@server.tool(annotations=READ_ONLY, description="Look a name or address up in the kit's symbol table: a substring "
             "of a name (case-insensitive) or a hex address (the covering symbol). Returns up to `limit` rows "
             "(binary, name, va, size, type, bind).")
def name_lookup(query: str, binary: str | None = None, limit: int = 40) -> dict[str, Any]:
    import csv
    rows = list(csv.DictReader(open(SYMBOLS)))
    want = (binary or "").strip()
    out = []
    if re.fullmatch(r"0x[0-9a-fA-F]+", query.strip()):
        va = int(query, 16)
        for r in rows:
            if want and r["binary"] != want:
                continue
            start, size = int(r["va"], 16), int(r["size"] or 0)
            if start == va or (size and start <= va < start + size):
                out.append(dict(binary=r["binary"], name=r["name"], va=r["va"], size=size, type=r["type"], bind=r["bind"],
                                offset=va - start))
    else:
        q = query.lower()
        for r in rows:
            if want and r["binary"] != want:
                continue
            if q in r["name"].lower():
                out.append(dict(binary=r["binary"], name=r["name"], va=r["va"], size=int(r["size"] or 0),
                                type=r["type"], bind=r["bind"]))
    return dict(count=len(out), rows=out[:limit], truncated=len(out) > limit)


@server.tool(annotations=LOCAL_WRITE, description="Name a stripped binary's functions from an unstripped donor by "
             "masked body matching (same compiler era => verbatim SDK/CRT/engine objects). Writes a TSV (and an "
             "optional splat symbol file) and returns the summary and the first rows.")
def name_transfer_symbols(donor: str, donor_symbols: str, acceptor: str, out_tsv: str, donor_binary: str | None = None,
                          splat_out: str | None = None, mask: str = "loose", min_size: int = 64,
                          acceptor_base: str | None = None, compare: str | None = None) -> dict[str, Any]:
    args = ["--donor", donor, "--donor-symbols", donor_symbols, "--acceptor", acceptor, "-o", out_tsv, "--mask", mask,
            "--min-size", min_size]
    if donor_binary:
        args += ["--donor-binary", donor_binary]
    if splat_out:
        args += ["--splat", splat_out]
    if acceptor_base:
        args += ["--acceptor-base", acceptor_base]
    if compare:
        args += ["--compare", compare]
    r = run("transfer_symbols.py", args, timeout=900)
    head = ""
    try:
        head = "".join(open(out_tsv).readlines()[:30])
    except OSError:
        pass
    return dict(summary=r["stdout"], rc=r["rc"], stderr=r["stderr"], tsv=out_tsv, head=head)


# ---------------------------------------------------------------- triage

@server.tool(annotations=READ_ONLY, description="Shape and difficulty of one function of the configured original "
             "(by name or 0x address): class trivial/easy/medium/hard/cop2/huge, score, callees, switch tables, "
             "COP2, lq/sq, the -G0/-G8 witness, frame and saved registers.")
def triage_function(name_or_va: str, binary: str | None = None) -> dict[str, Any]:
    b = binary or BINARY
    sel = ["--va", name_or_va] if name_or_va.lower().startswith("0x") else ["--name", name_or_va]
    data, r = triage_json(["--binary", b] + sel, b, timeout=300)
    funcs = (data or {}).get("functions") or []
    return dict(result=funcs[0] if len(funcs) == 1 else None, report=data, rc=r["rc"], stderr=r["stderr"])


def triage_json(args: list[str], binary: str, timeout: int) -> tuple[dict | None, dict]:
    """Run triage.py with `--json` into a temp file and return (its report, the run). triage.py
    takes a file path there (`-` once wrote a file named `-` into the kit root), and the TSV on
    stdout is capped at TEXT_LIMIT (the OV01 table is 58 KB), so the file is the only complete
    source of rows."""
    with tempfile.NamedTemporaryFile(prefix="triage-", suffix=".json", delete=False) as fh:
        out = Path(fh.name)
    try:
        r = run("triage.py", ["--elf", str(GAME / binary), "--symbols", str(SYMBOLS)] + args + ["--json", str(out)],
                timeout=timeout)
        try:
            data = json.loads(out.read_text())
        except (OSError, ValueError):
            data = None
    finally:
        out.unlink(missing_ok=True)
    return data, r


CLASS_ORDER = ("trivial", "easy", "medium", "hard", "cop2", "huge")


def triage_row(r: dict) -> dict:
    """The TSV columns of triage.py (tsv_row), from a JSON record."""
    return dict(name=r.get("name"), va=r.get("va"), size=r.get("size"), **{"class": r.get("cls")}, score=r.get("score"),
                instr=r.get("instruction_count"), callees=r.get("callee_count"), switch=len(r.get("switch_jumps") or []),
                cop2=r.get("cop2_count"), quad=r.get("quad_count"), g_hint=r.get("g_hint"), flags=",".join(r.get("flags") or []))


@server.tool(annotations=READ_ONLY, description="Triage every function of a binary and return the easiest `limit` "
             "rows (optionally restricted to classes, e.g. 'trivial,easy') plus the class histogram.")
def triage_unit(binary: str | None = None, classes: str | None = None, limit: int = 40) -> dict[str, Any]:
    b = binary or BINARY
    # `--summary` prints the class histogram INSTEAD of the rows, and the TSV on stdout is capped
    # at TEXT_LIMIT, so the rows come from the JSON report and the histogram is built from them.
    data, r = triage_json(["--binary", b], b, timeout=600)
    records = (data or {}).get("functions") or []
    keep = {c.strip() for c in classes.split(",")} if classes else None
    histogram: dict[str, int] = {}
    for rec in records:
        histogram[rec.get("cls")] = histogram.get(rec.get("cls"), 0) + 1
    rows = [triage_row(rec) for rec in sorted(records, key=lambda rec: (rec.get("score", 0), rec.get("size", 0)))
            if keep is None or rec.get("cls") in keep]
    order = [k for k in CLASS_ORDER if k in histogram] + sorted(k for k in histogram if k not in CLASS_ORDER)
    return dict(binary=b, rows=rows[:limit], total_rows=len(rows), histogram=histogram,
                outside_image=len((data or {}).get("skipped") or []),
                summary=f"functions: {len(records)} triaged; class: " + " ".join(f"{k}={histogram[k]}" for k in order),
                rc=r["rc"], stderr=r["stderr"])


# ---------------------------------------------------------------- jobs (long-running)

def start_job(kind: str, tool: str, args: list[str]) -> dict[str, Any]:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    job_id = f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    log = JOBS_DIR / f"{job_id}.log"
    cmd = [PYTHON, "-B", str(TOOLS / tool)] + [str(a) for a in args]
    with open(log, "w") as fh:
        proc = subprocess.Popen(cmd, cwd=str(KIT), env=env(), stdout=fh, stderr=subprocess.STDOUT)
    (JOBS_DIR / f"{job_id}.json").write_text(json.dumps(dict(job_id=job_id, pid=proc.pid, command=shlex.join(cmd),
                                                              started=time.time(), log=str(log))))
    return dict(job_id=job_id, pid=proc.pid, log=str(log), note="poll with job_status(job_id)")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@server.tool(annotations=READ_ONLY, description="Status of a background job started by verify_unit_start or "
             "probe_toolchain_start: running/finished, elapsed seconds, the last `tail` lines of its log, and the "
             "JSON report path when the job wrote one.")
def job_status(job_id: str, tail: int = 25) -> dict[str, Any]:
    meta_path = JOBS_DIR / f"{job_id}.json"
    if not meta_path.is_file():
        return dict(error=f"no such job {job_id}")
    meta = json.loads(meta_path.read_text())
    log = Path(meta["log"])
    lines = log.read_text(errors="replace").splitlines() if log.is_file() else []
    running = pid_alive(meta["pid"])
    out = dict(job_id=job_id, running=running, elapsed_s=round(time.time() - meta["started"]),
               log_tail=lines[-tail:], report=meta.get("report"))
    if not running and meta.get("report") and Path(meta["report"]).is_file():
        try:
            rep = json.loads(Path(meta["report"]).read_text())
            out["totals"] = rep.get("totals")
            out["rows"] = rep.get("rows")
            out["mismatches"] = (rep.get("mismatches") or [])[:40]
        except ValueError:
            pass
    return out


@server.tool(annotations=LOCAL_WRITE, description="Start a whole-unit verification of the configured upstream in the "
             "background (minutes): every in-scope C TU compiled with the stock or contract toolchain and compared per "
             "function. Returns a job handle for job_status. baseline is an earlier report whose matches must not regress.")
def verify_unit_start(compiler: str = "stock", unit: str | None = None, baseline: str | None = None) -> dict[str, Any]:
    if not UPSTREAM:
        return dict(error="DECOMP_UPSTREAM is not configured")
    if compiler == "contract" and (why := contract_blocker()):
        return dict(error=why)
    u = unit or UNIT
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    report = JOBS_DIR / f"verify-{u}-{compiler}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    args = ["--upstream", str(UPSTREAM), "--unit", u, "--symbols", str(SYMBOLS), "--binary", str(GAME / BINARY),
            "--compiler", compiler, "--rootfs", str(ROOTFS), "--work", str(Path.home() / ".cache" / "decomp-verify-mcp"),
            "--docker-timeout", "1800", "--image", DOCKER_IMAGE, "--out", str(report)]
    if baseline:
        args += ["--baseline", baseline]
    job = start_job("verify", "verify_decomp.py", args)
    meta_path = JOBS_DIR / f"{job['job_id']}.json"
    meta = json.loads(meta_path.read_text()); meta["report"] = str(report); meta_path.write_text(json.dumps(meta))
    job["report"] = str(report)
    return job


@server.tool(annotations=LOCAL_WRITE, description="Verify one translation unit of the configured upstream now "
             "(tens of seconds): per-function classes match/hazard-nop/reorder/mixed/immediate/delay-slot/size-diff/"
             "unexplained under the stock or contract toolchain.")
def verify_tu(tu: str, compiler: str = "stock") -> dict[str, Any]:
    if not UPSTREAM:
        return dict(error="DECOMP_UPSTREAM is not configured")
    if compiler == "contract" and (why := contract_blocker()):
        return dict(error=why)
    unit = tu.split("/")[0] if "/" in tu else UNIT
    report = JOBS_DIR / f"verify-{tu.replace('/', '__')}-{compiler}.json"
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    r = run("verify_decomp.py", ["--upstream", str(UPSTREAM), "--unit", unit, "--tu", tu, "--symbols", str(SYMBOLS),
                                 "--binary", str(GAME / BINARY), "--compiler", compiler, "--rootfs", str(ROOTFS),
                                 "--work", str(Path.home() / ".cache" / "decomp-verify-mcp"), "--image", DOCKER_IMAGE,
                                 "--out", str(report), "-v"],
            timeout=900)
    rep = None
    if report.is_file():
        try:
            rep = json.loads(report.read_text())
        except ValueError:
            rep = None
    return dict(rc=r["rc"], text=r["stdout"], stderr=r["stderr"],
                per_tu=(rep or {}).get("per_tu"), mismatches=(rep or {}).get("mismatches"), warnings=(rep or {}).get("warnings"))


@server.tool(annotations=LOCAL_WRITE, description="Start the toolchain behaviour probe in the background (about a "
             "minute): what the configured cc1 + ee-as do on literal rounding, loop padding, hazard nops, move/break/"
             "la/jump-table encodings, vsqrt and small data, against what retail did. Defaults to the stock rootfs pair; "
             "pass cc1/as paths (as the probe tool takes them) and toolchains to probe a patched pair.")
def probe_toolchain_start(cc1: str | None = None, ee_as: str | None = None, toolchains: str | None = None,
                          cc1_flags: str | None = None, as_flags: str | None = None) -> dict[str, Any]:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    report = JOBS_DIR / f"probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    args = ["--rootfs", str(ROOTFS), "--json", str(report)]
    if cc1:
        args += ["--cc1", cc1]
    if ee_as:
        args += ["--as", ee_as]
    if toolchains:
        args += ["--toolchains", toolchains]
    if cc1_flags:
        args += ["--cc1-flags", cc1_flags]
    if as_flags:
        args += ["--as-flags", as_flags]
    job = start_job("probe", "probe_toolchain.py", args)
    meta_path = JOBS_DIR / f"{job['job_id']}.json"
    meta = json.loads(meta_path.read_text()); meta["report"] = str(report); meta_path.write_text(json.dumps(meta))
    job["report"] = str(report)
    return job


# ---------------------------------------------------------------- match

@server.tool(annotations=LOCAL_WRITE, description="Open a private sandbox for one function of the configured unit: "
             "copies the upstream sources, records the budget (forms ceiling, compiler, author), writes the evidence "
             "card, and refuses (UNVERIFIABLE) when the original refers to an address the verifier cannot resolve. "
             "Returns the sandbox path to pass to the other match_* tools, or the refusal.")
def match_init(function: str, forms: int = 30, compiler: str | None = None, author: str | None = None,
               no_progress_stop: int | None = None) -> dict[str, Any]:
    args = ["init", function, "--forms", forms]
    # match_kit picks the contract compiler whenever the pinned toolchains resolve, so an omitted
    # compiler is probed exactly when the kit would build with them.
    effective = compiler or match_kit_module().default_compiler()
    if effective == "contract" and (why := contract_blocker()):
        return dict(error=why, hint="pass compiler='stock' to match against the stock rootfs pair instead")
    if compiler:
        args += ["--compiler", compiler]
    if author:
        args += ["--author", author]
    if no_progress_stop:
        args += ["--no-progress-stop", no_progress_stop]
    r = run("match_kit.py", args, timeout=900)
    data = parse_json_tail(r["stdout"]) or {}
    data.update(rc=r["rc"], stderr=r["stderr"][-1500:] if r["rc"] else "")
    if r["rc"] == 3:
        data.setdefault("stopped", "init refused: see `refused` and `reasons`; more C cannot change this")
    return data


@server.tool(annotations=READ_ONLY, description="The <= 2 KB evidence card of a function: identity, callers and "
             "callees, argument registers read, member offsets per base register, strings, floats, COP2 flag, "
             "prototypes of matched siblings, declarations found. Read this before the first form; it replaces "
             "reading source files whole.")
def match_card(function: str) -> dict[str, Any]:
    r = run("match_kit.py", ["card", function], timeout=600)
    return dict(card=r["stdout"], rc=r["rc"], stderr=r["stderr"] if r["rc"] else "")


@server.tool(annotations=READ_ONLY, description="The original function's disassembly with call targets and globals "
             "named from the symbol table. range is START:END (offsets or addresses, half-open); brief drops the "
             "address and raw-word columns.")
def match_disasm(function: str, range: str | None = None, brief: bool = True) -> dict[str, Any]:
    args = ["disasm", function] + (["--range", range] if range else []) + (["--brief"] if brief else [])
    r = run("match_kit.py", args, timeout=600)
    return dict(disasm=r["stdout"], rc=r["rc"], stderr=r["stderr"] if r["rc"] else "")


class NoSandbox(Exception):
    pass


def _source_file(sandbox: Path):
    if not (sandbox / "budget.json").is_file():
        raise NoSandbox(f"no sandbox at {sandbox} (match_init first; match_list shows the existing ones)")
    b = budget_of(sandbox)
    return sandbox / "upstream" / b["path"], b


@server.tool(annotations=READ_ONLY, description="The function's current text in the sandbox source (its C body, or "
             "the INCLUDE_ASM stub it still is), the file's include/typedef prelude, and the source path.")
def match_source(sandbox: str, prelude_lines: int = 40) -> dict[str, Any]:
    sb = sandbox_path(sandbox)
    try:
        path, b = _source_file(sb)
    except NoSandbox as e:
        return dict(error=str(e))
    text = path.read_text(errors="replace")
    mk = match_kit_module()
    name = b["function"]
    body = mk.function_text(text, name)
    stub = re.search(r'^INCLUDE_ASM\("[^"]*",\s*' + re.escape(name) + r'\);\s*$', text, re.M)
    return dict(function=name, path=str(path), relative=b["path"], defined=body is not None,
                function_text=body, include_asm_line=stub.group(0) if stub else None,
                prelude="\n".join(text.splitlines()[:prelude_lines]), lines=text.count("\n") + 1)


def unique_anchor(lines: list[str], text: str, from_end: bool, limit: int = 6) -> str:
    """The shortest run of LINES at the stub's side (the last ones when from_end, else the
    first) that occurs exactly once in TEXT: usually the neighbouring INCLUDE_ASM line or a
    signature, so an edit elsewhere in the file (a typedef, an include) leaves it valid."""
    for n in range(1, min(limit, len(lines)) + 1):
        candidate = "\n".join(lines[-n:] if from_end else lines[:n])
        if candidate.strip() and text.count(candidate) == 1:
            return candidate
    return ""


@server.tool(annotations=LOCAL_WRITE, description="Write your C for the sandbox's function: replaces its INCLUDE_ASM "
             "stub (or its current body) with `c_text`; `declarations` (externs, typedefs) are inserted just before "
             "the function. This is the only edit the loop needs; then call match_check.")
def match_write_function(sandbox: str, c_text: str, declarations: str = "") -> dict[str, Any]:
    sb = sandbox_path(sandbox)
    try:
        path, b = _source_file(sb)
    except NoSandbox as e:
        return dict(error=str(e))
    text = path.read_text(errors="replace")
    name = b["function"]
    mk = match_kit_module()
    block = (declarations.rstrip() + "\n\n" if declarations.strip() else "") + c_text.strip() + "\n"
    # A rewrite replaces everything the stub replacement inserted (declarations, leading comment,
    # body), or every call leaves another copy of the externs and the comment above the function
    # (three copies after three forms on the first run). The inserted region is bracketed by the
    # file's own lines around the stub, recorded at the first write: they belong to the base TU, so
    # they survive a hand edit inside the block and a match_best restore, which a memo of the
    # block's text does not (roundtable, 2026-10-31).
    memo = sb / "mcp-write-anchors.json"
    try:
        anchors = json.loads(memo.read_text())
    except (OSError, ValueError):
        anchors = {}
    stub = re.search(r'^INCLUDE_ASM\("[^"]*",\s*' + re.escape(name) + r'\);[ \t]*\n?', text, re.M)
    warning = None
    if stub:
        anchors = dict(before=unique_anchor(text[:stub.start()].rstrip("\n").split("\n"), text, from_end=True),
                       after=unique_anchor(text[stub.end():].lstrip("\n").split("\n"), text, from_end=False))
        if not (anchors["before"] and anchors["after"]):
            anchors = {}
            warning = "the lines around the stub are not unique in the file: a rewrite will replace the body only"
        new = text[:stub.start()] + block + text[stub.end():]
        how = "replaced INCLUDE_ASM stub"
    else:
        start = end = -1
        if anchors.get("before") and anchors.get("after") and text.count(anchors["before"]) == 1 \
                and text.count(anchors["after"]) == 1:
            start = text.index(anchors["before"]) + len(anchors["before"])
            end = text.index(anchors["after"])
        if 0 <= start < end:
            region = text[start:end]                      # keep the blank lines the first write left around the block
            lead = region[:len(region) - len(region.lstrip("\n"))]
            trail = region[len(region.rstrip("\n")):]
            new = text[:start] + lead + block.rstrip("\n") + trail + text[end:]
            how = "replaced the previous write (declarations, comment and body)"
        else:
            body = mk.function_text(text, name)
            if body is None or body not in text:
                return dict(error=f"{name}: neither an INCLUDE_ASM stub nor a definition found in {b['path']}")
            new = text.replace(body + "\n", block, 1) if body + "\n" in text else text.replace(body, block.rstrip("\n"), 1)
            how = "replaced existing definition"
            warning = ("the lines recorded around the stub were not found once each (edited?): only the body was "
                       "replaced, declarations written earlier stay above it")
    path.write_text(new)
    if anchors:
        memo.write_text(json.dumps(anchors))
    out = dict(ok=True, how=how, path=str(path), function=name, bytes=len(new))
    if warning:
        out["warning"] = warning
    return out


@server.tool(annotations=LOCAL_WRITE, description="Exact single-occurrence text replacement in the sandbox source "
             "(for a declaration, a typedef, or a struct field in the TU-local header when `header` is true). "
             "Refuses when `old` is absent or ambiguous.")
def match_edit(sandbox: str, old: str, new: str, header: bool = False) -> dict[str, Any]:
    sb = sandbox_path(sandbox)
    try:
        path, b = _source_file(sb)
    except NoSandbox as e:
        return dict(error=str(e))
    if header:
        path = path.with_suffix(".h")
        if not path.is_file():
            return dict(error=f"no TU-local header at {path}")
    text = path.read_text(errors="replace")
    n = text.count(old)
    if n != 1:
        return dict(error=f"`old` occurs {n} times in {path.name}; it must occur exactly once")
    path.write_text(text.replace(old, new, 1))
    return dict(ok=True, path=str(path))


@server.tool(annotations=LOCAL_WRITE, description="Compile the sandbox TU and judge the function: charges one form. "
             "`hypothesis` is the one thing this form changes. Returns the verdict line (MATCH, REORDER, HAZARD-NOP, "
             "MIXED, IMMEDIATE, UNEXPLAINED, SIZE_DIFF, UNDECLARED, NOT_COMPILED, CHEAT, UNVERIFIABLE), diff_words, "
             "the differing instruction rows with context, and the stop state (exit 2 ceiling/no progress, 3 "
             "UNVERIFIABLE stop, 4 REGRESSION).")
def match_check(sandbox: str, hypothesis: str, full: bool = False, compiler: str | None = None) -> dict[str, Any]:
    sb = sandbox_path(sandbox)
    args = ["check", str(sb), hypothesis] + (["--full"] if full else []) + (["--compiler", compiler] if compiler else [])
    r = run("match_kit.py", args, timeout=900)
    lines = r["stdout"].splitlines()
    state = {0: "judged", 2: "STOP: form ceiling or no progress", 3: "STOP: UNVERIFIABLE (tooling)", 4: "REGRESSION"}.get(r["rc"], f"rc {r['rc']}")
    verdict = next((l for l in lines if l.startswith(("FORM", "NO PROGRESS", "FORM CEILING", "UNVERIFIABLE", "TOOLING"))), lines[0] if lines else "")
    return dict(verdict_line=verdict, state=state, rc=r["rc"], output=r["stdout"], stderr=r["stderr"][-1500:] if r["rc"] not in (0, 2, 3, 4) else "")


@server.tool(annotations=READ_ONLY, description="The machine verdict of a sandbox (JSON): verdict, compiler, "
             "tu_matches vs baseline, diff_words, best_form, forms_without_progress, regression, scope changes. "
             "Charges nothing; `full` prints every diff row.")
def match_verdict(sandbox: str, full: bool = False) -> dict[str, Any]:
    sb = sandbox_path(sandbox)
    r = run("match_kit.py", ["verdict", str(sb)] + (["--full"] if full else []), timeout=900)
    return dict(verdict=parse_json_tail(r["stdout"]), rc=r["rc"], text=r["stdout"] if full else "", stderr=r["stderr"][-800:] if r["rc"] not in (0, 1) else "")


@server.tool(annotations=LOCAL_WRITE, description="Restore the best form (lowest diff_words, never a CHEAT or "
             "REGRESSION form) into the sandbox source.")
def match_best(sandbox: str) -> dict[str, Any]:
    r = run("match_kit.py", ["best", str(sandbox_path(sandbox))], timeout=120)
    return dict(output=r["stdout"], rc=r["rc"], stderr=r["stderr"][-800:] if r["rc"] else "")


@server.tool(annotations=READ_ONLY, description="Unified diff of the function block (and the TU-local header when "
             "it changed) against the copy init saved; `against` splices it into a newer copy of the same file.")
def match_patch(sandbox: str, against: str | None = None) -> dict[str, Any]:
    args = ["patch", str(sandbox_path(sandbox))] + (["--against", against] if against else [])
    r = run("match_kit.py", args, timeout=120)
    return dict(patch=r["stdout"], rc=r["rc"], stderr=r["stderr"][-800:] if r["rc"] else "")


@server.tool(annotations=READ_ONLY, description="List the sandboxes under DECOMP_SANDBOXES with their function, "
             "forms used, and last verdict.")
def match_list() -> dict[str, Any]:
    out = []
    if SANDBOXES.is_dir():
        for d in sorted(SANDBOXES.iterdir()):
            bj = d / "budget.json"
            if bj.is_file():
                try:
                    b = json.loads(bj.read_text())
                    last = b["forms"][-1] if b.get("forms") else None
                    out.append(dict(sandbox=str(d), function=b.get("function"), tu=b.get("tu"),
                                    forms=f"{len(b.get('forms', []))}/{b.get('forms_max')}",
                                    compiler=b.get("compiler"), last_verdict=last and last.get("verdict"),
                                    last_diff_words=last and last.get("diff_words")))
                except (ValueError, KeyError):
                    out.append(dict(sandbox=str(d), error="unreadable budget.json"))
    return dict(sandboxes=out)


# ---------------------------------------------------------------- resources and prompt

@server.resource("kit://docs/{name}", name="kit docs", mime_type="text/markdown",
                 description="Kit documents: playbook, toolchain-differences, corpus, readme")
def doc_resource(name: str) -> str:
    rel = DOCS.get(name)
    if not rel:
        return f"unknown document {name!r}; one of {sorted(DOCS)}"
    return (KIT / "docs" / rel).resolve().read_text()


@server.resource("kit://sandbox/{function}/card", name="sandbox card", mime_type="text/markdown",
                 description="The evidence card written at match_init")
def card_resource(function: str) -> str:
    p = SANDBOXES / function / "card.md"
    return p.read_text() if p.is_file() else f"no card for {function} under {SANDBOXES}"


@server.resource("kit://sandbox/{function}/budget", name="sandbox budget", mime_type="application/json",
                 description="The budget and form history of a sandbox (read-only: never edit it)")
def budget_resource(function: str) -> str:
    p = SANDBOXES / function / "budget.json"
    return p.read_text() if p.is_file() else json.dumps(dict(error=f"no sandbox for {function}"))


@server.prompt(name="match_one_function", description="The stock-lane worker brief for matching one function")
def match_one_function(function: str, ceiling: int = 15) -> str:
    return f"""Match the function `{function}` to byte-identical, readable C.

Rules (the tool decides exactness; a verdict you did not get from match_check is not evidence):
1. match_init("{function}", forms={ceiling}) -> note the sandbox path. If it is refused (UNVERIFIABLE), stop and report the reasons.
2. Read match_card and match_disasm (brief). Do not read whole source files; match_source gives you the prelude and the stub.
3. Write the C with match_write_function; add externs/typedefs through `declarations` or match_edit.
4. match_check(sandbox, "<the one thing this form changes>"). Read the verdict line and the differing rows.
   - MATCH: call match_verdict and match_patch; report the fields, the closing hypothesis and the function verbatim.
   - CHEAT: a rule is broken (asm/.word, base+offset pointers, raw address casts, placeholder names, goto, do-while(0), register-pinning locals). Fix the source.
   - REORDER / HAZARD-NOP / MIXED / IMMEDIATE: statement or declaration order, a temporary's lifetime, signedness, literal width. Try store order before a structural rewrite.
   - UNEXPLAINED / SIZE_DIFF: control-flow shape, loop form, a missed call, wrong types or prototype.
   - UNDECLARED: you named something the binary never had; use the name the disassembly shows.
   - NOT_COMPILED: read the compiler error before the next check.
   - REGRESSION: another function in the TU stopped matching; undo that edit.
   - STOP (ceiling, NO PROGRESS, UNVERIFIABLE): match_best, then report the best remaining diff rows and your next hypothesis.
5. One changed hypothesis per check. A form whose bytes equal an earlier form's is wasted.
Report in at most 12 lines: verdict, compiler, tu_matches vs baseline, diff_words trend, the closing hypothesis or the next one, the function."""


if __name__ == "__main__":
    server.run("stdio")
