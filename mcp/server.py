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
    tc = os.environ.get("DECOMP_TOOLCHAINS") or (str(UPSTREAM / ".work" / "toolchains") if UPSTREAM else None)
    checks["toolchains"] = dict(path=tc, ok=bool(tc and Path(tc).is_dir()))
    try:
        d = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=20)
        checks["docker"] = dict(ok=d.returncode == 0, version=d.stdout.strip() or d.stderr.strip()[-200:])
    except (OSError, subprocess.TimeoutExpired) as e:
        checks["docker"] = dict(ok=False, error=str(e))
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
    r = run("triage.py", ["--elf", str(GAME / b), "--symbols", str(SYMBOLS), "--binary", b] + sel + ["--json", "-"],
            timeout=300)
    data = parse_json_tail(r["stdout"])
    if data is None and r["stdout"]:
        return dict(tsv=r["stdout"], rc=r["rc"], stderr=r["stderr"])
    return dict(result=data, rc=r["rc"], stderr=r["stderr"])


@server.tool(annotations=READ_ONLY, description="Triage every function of a binary and return the easiest `limit` "
             "rows (optionally restricted to classes, e.g. 'trivial,easy') plus the class histogram.")
def triage_unit(binary: str | None = None, classes: str | None = None, limit: int = 40) -> dict[str, Any]:
    b = binary or BINARY
    r = run("triage.py", ["--elf", str(GAME / b), "--symbols", str(SYMBOLS), "--binary", b, "--summary"], timeout=600)
    lines = [l for l in r["stdout"].splitlines() if l.strip()]
    header = next((l for l in lines if l.startswith("name\t")), None)
    rows, summary = [], []
    keep = {c.strip() for c in classes.split(",")} if classes else None
    for l in lines:
        if l.startswith("name\t") or l.startswith("#"):
            continue
        parts = l.split("\t")
        if header and len(parts) >= 4 and parts[1].startswith("0x"):
            if keep is None or parts[3] in keep:
                rows.append(dict(zip(header.split("\t"), parts)))
        else:
            summary.append(l)
    return dict(binary=b, rows=rows[:limit], total_rows=len(rows), summary="\n".join(summary[-20:]),
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
    u = unit or UNIT
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    report = JOBS_DIR / f"verify-{u}-{compiler}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    args = ["--upstream", str(UPSTREAM), "--unit", u, "--symbols", str(SYMBOLS), "--binary", str(GAME / BINARY),
            "--compiler", compiler, "--rootfs", str(ROOTFS), "--work", str(Path.home() / ".cache" / "decomp-verify-mcp"),
            "--docker-timeout", "1800", "--out", str(report)]
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
    unit = tu.split("/")[0] if "/" in tu else UNIT
    report = JOBS_DIR / f"verify-{tu.replace('/', '__')}-{compiler}.json"
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    r = run("verify_decomp.py", ["--upstream", str(UPSTREAM), "--unit", unit, "--tu", tu, "--symbols", str(SYMBOLS),
                                 "--binary", str(GAME / BINARY), "--compiler", compiler, "--rootfs", str(ROOTFS),
                                 "--work", str(Path.home() / ".cache" / "decomp-verify-mcp"), "--out", str(report), "-v"],
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
    stub = re.search(r'^INCLUDE_ASM\("[^"]*",\s*' + re.escape(name) + r'\);[ \t]*\n?', text, re.M)
    if stub:
        new = text[:stub.start()] + block + text[stub.end():]
        how = "replaced INCLUDE_ASM stub"
    else:
        body = mk.function_text(text, name)
        if body is None or body not in text:
            return dict(error=f"{name}: neither an INCLUDE_ASM stub nor a definition found in {b['path']}")
        new = text.replace(body + "\n", block, 1) if body + "\n" in text else text.replace(body, block.rstrip("\n"), 1)
        how = "replaced existing definition"
    path.write_text(new)
    return dict(ok=True, how=how, path=str(path), function=name, bytes=len(new))


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
