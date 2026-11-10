#!/usr/bin/env python3
"""Render and install the kit's Claude Code agents: a generic set and a Xenosaga set.

The agents (an orchestrator, four tiered matchers, a reviewer, two integrators, a stock
verifier, an evidence enricher and an IOP matcher) are defined once in agents/roles.json,
agents/templates/<template>.md and the keyed sections of agents/games/<flavour>.md. This
tool renders them into agents/generic/*.md and agents/xenosaga/*.md (checked in, so they
can be read on the web) and copies one set into a project's .claude/agents.

  install_agents.py render [--check]
        render both flavours into agents/generic and agents/xenosaga; --check writes
        nothing and exits 1 listing every file that differs, is missing or is stale
  install_agents.py detect [--project DIR] [--flavour auto|generic|xenosaga]
        print {flavour, game, reason} as JSON without writing anything
  install_agents.py install [--project DIR | --user] [--flavour auto|generic|xenosaga]
                            [--dry-run] [--force]
        copy the chosen set into DIR/.claude/agents (DIR must exist; default: the current
        directory) or ~/.claude/agents (--user needs an explicit --flavour), write
        .kit-flavour.json there and the kit's path to ~/.claude/decomp-compat-kit.json,
        remove kit-marked agents the installed set does not contain, print a JSON summary
  install_agents.py self-test
        temp-directory checks of rendering, drift detection, detection and installing

Detection (--flavour auto, the default; the first rule that fires wins and is reported):
  1. --flavour generic|xenosaga
  2. DECOMP_GAME: xenosaga, generic, or a game id (xenosaga-1/2/3; any other id means
     generic)
  3. a file in the project (three levels deep) named after a Xenosaga serial (SERIALS
     below)
  4. a git remote of the project matching xsg-i-decomp, xsg-ii-decomp, xsg-iii-decomp or
     "xenosaga"
  5. the project's config/originals.json or splat yaml naming a Xenosaga serial
  6. the DECOMP_TARGET manifest naming a Xenosaga serial or title (an unreadable manifest
     is named in the reason)
  7. DECOMP_BINARY or the DECOMP_GAME_DIR listing naming a Xenosaga serial
  otherwise generic. The project's own evidence (3-5) comes before the environment (6-7),
  which is often exported shell-wide for one game. When the flavour is xenosaga the
  remaining rules still run to find the episode; `game` stays null when none names it.
  --user runs no detection: one user-level set serves every project, so it takes the
  explicit --flavour and records game null.

Safety: without --force, a same-named file in the agents directory that lacks the kit
marker (a hand-written agent), or a kit agent edited since the kit wrote it, refuses the
whole install before anything is written; --force overwrites it after copying it to
NAME.md.kit-backup. A symlink in place of an agent or a state file is always refused.
Only kit-marked files whose names a kit flavour renders are replaced or removed. The
marker is the first body line after the frontmatter of every rendered agent:
  <!-- rendered by decomp-compat-kit tools/install_agents.py from agents/templates; flavour: X -->

Edit agents/roles.json, a template or a games file, then run `install_agents.py render`;
never edit a rendered file (render --check reports the drift).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
AGENTS = KIT / "agents"

# serial -> game id. Disc 2 of Episode II boots SLUS_211.33, byte-identical to SLUS_208.92
# (docs/games/xenosaga-2.md); the kit's docs list no other disc serials.
SERIALS = {
    "SLUS_204.69": "xenosaga-1",
    "SLUS_208.92": "xenosaga-2",
    "SLUS_211.33": "xenosaga-2",
    "SLUS_213.89": "xenosaga-3",
}
SERIAL_RE = re.compile(r"SLUS[_-]?(\d{3})\.?(\d{2})", re.I)
REMOTE_RE = re.compile(r"xsg-i(i|ii)?-decomp|xenosaga", re.I)
TITLE_RE = re.compile(r"xenosaga(?:[\s_-]*(?:episode|ep\.?))?[\s_-]*(iii|ii|i|3|2|1)?\b", re.I)
EPISODE = {"i": "xenosaga-1", "1": "xenosaga-1", "ii": "xenosaga-2", "2": "xenosaga-2",
           "iii": "xenosaga-3", "3": "xenosaga-3"}

MARKER = "<!-- rendered by decomp-compat-kit tools/install_agents.py from agents/templates; flavour: {flavour} -->"
MARKER_RE = re.compile(r"^<!-- rendered by decomp-compat-kit tools/install_agents\.py from agents/templates; "
                       r"flavour: ([a-z0-9-]+) -->$", re.M)
PLACEHOLDER = re.compile(r"\{\{([a-z0-9_:-]+)\}\}")
STATE_FILE = ".kit-flavour.json"
STATE_SCHEMA = "kit-flavour/1"
KIT_FILE_SCHEMA = "kit-root/1"
BACKUP_SUFFIX = ".kit-backup"
SCAN_DEPTH = 3
SCAN_LIMIT = 20000
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}


class RenderError(Exception):
    pass


# ---------------------------------------------------------------- rendering

def load_roles(root: Path = AGENTS) -> dict:
    return json.loads((root / "roles.json").read_text())


def parse_sections(text: str) -> dict:
    """'## key' sections of a games file; text before the first section is ignored."""
    sections, key, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^## (\S.*?)\s*$", line)
        if m:
            if key is not None:
                sections[key] = "\n".join(buf).strip()
            key, buf = m.group(1), []
            if key in sections:
                raise RenderError(f"duplicate section '## {key}'")
        elif key is not None:
            buf.append(line)
    if key is not None:
        sections[key] = "\n".join(buf).strip()
    return sections


def agent_name(role: dict, flavour: str) -> str:
    return role[f"{flavour}_name"]


def fill(text: str, values: dict, where: str) -> str:
    def sub(m):
        key = m.group(1)
        if key not in values:
            raise RenderError(f"{where}: unknown placeholder {{{{{key}}}}}")
        return values[key]
    return PLACEHOLDER.sub(sub, text)


def tier_table(roles: list, flavour: str) -> str:
    rows = ["| Tier | Agent | Model | Effort | Forms | Classes |", "| --- | --- | --- | --- | ---: | --- |"]
    for r in roles:
        if r["template"] not in ("matcher", "iop-matcher"):
            continue
        extra = ""
        if r["template"] == "iop-matcher":
            if r.get("next"):
                nxt = next(x for x in roles if x["id"] == r["next"])
                extra = f"; escalates to {nxt.get('tier', nxt['id'])}"
            else:
                extra = "; no tier above it (one same-lane retry with a changed hypothesis)"
        rows.append(f"| {r.get('tier', '')} | `{agent_name(r, flavour)}` | {r['model']} | {r['effort'] or 'default'} | "
                    f"{r['forms']} | {', '.join(r['classes'] or [])}{extra} |")
    return "\n".join(rows)


def next_tier_text(role: dict, by_id: dict, flavour: str) -> str:
    if role["template"] not in ("matcher", "iop-matcher"):
        return ""
    if not role.get("next"):
        if role["template"] == "iop-matcher":
            return ("there is no IOP tier above you (the EE matchers judge only match_kit sandboxes): the orchestrator "
                    "may relaunch you once with a changed hypothesis and budget left, otherwise the function is "
                    "recorded as a blocker, so write the most concrete next actions you can")
        return "there is no further tier: write the most concrete next actions you can"
    nxt = by_id[role["next"]]
    return (f"the orchestrator escalates to {nxt.get('tier', nxt['id'])} (`{agent_name(nxt, flavour)}`) "
            "with your report and next hypothesis as evidence")


def game_block(role: dict, sections: dict, episodes: list) -> str:
    parts = [sections.get(k, "") for k in dict.fromkeys((role["template"], role["id"]))]
    out = "\n\n".join(p for p in parts if p)
    eps = []
    for ep in episodes:
        keys = dict.fromkeys((f"all {ep}", f"{role['template']} {ep}", f"{role['id']} {ep}"))
        text = "\n\n".join(sections[k] for k in keys if sections.get(k))
        if text:
            eps.append(f"### {ep}\n\n{text}")
    if eps:
        out += ("\n\n" if out else "") + "## Episode notes (follow only the one `.kit-flavour.json` names)\n\n" + "\n\n".join(eps)
    return out


def yaml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_flavour(flavour: str, root: Path = AGENTS) -> dict:
    """{filename: text} for one flavour."""
    cfg = load_roles(root)
    if flavour not in cfg["flavours"]:
        raise RenderError(f"unknown flavour {flavour!r}")
    fl = cfg["flavours"][flavour]
    sections = parse_sections((root.parent / fl["games_file"]).read_text()
                              if not Path(fl["games_file"]).is_absolute() else Path(fl["games_file"]).read_text())
    episodes = fl.get("episodes", [])
    roles = cfg["roles"]
    by_id = {r["id"]: r for r in roles}
    names = {f"role:{r['id']}": agent_name(r, flavour) for r in roles}
    out = {}
    for r in roles:
        name = agent_name(r, flavour)
        where = f"{flavour}/{name}"
        base = dict(names, name=name, model=r["model"], effort=r["effort"] or "default", flavour=flavour,
                    tier=r.get("tier") or "", forms=str(r["forms"]) if r.get("forms") is not None else "",
                    classes=", ".join(r.get("classes") or []), no_progress_stop=str(cfg["no_progress_stop"]),
                    next_tier=next_tier_text(r, by_id, flavour), tier_table=tier_table(roles, flavour))
        base["setup"] = fill(sections.get("setup", ""), base, f"{where} setup")
        base["extra"] = fill(r.get(f"extra_{flavour}", ""), base, f"{where} extra")
        base["game"] = fill(game_block(r, sections, episodes), base, f"{where} game")
        tpath = root / "templates" / f"{r['template']}.md"
        if not tpath.is_file():
            raise RenderError(f"{where}: no template {tpath.name}")
        body = fill(tpath.read_text(), base, f"{where} template {tpath.name}")
        body = re.sub(r"[ \t]+\n", "\n", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
        front = ["---", f"name: {name}", f"description: {yaml_str(r[f'description_{flavour}'])}", f"model: {r['model']}"]
        if r.get("effort"):
            front.append(f"effort: {r['effort']}")
        if r.get("tools"):
            front.append(f"tools: {r['tools']}")
        if r.get("disallowed_tools"):
            front.append(f"disallowedTools: {r['disallowed_tools']}")
        if r.get("permission_mode"):
            front.append(f"permissionMode: {r['permission_mode']}")
        front.append("---")
        out[f"{name}.md"] = "\n".join(front) + "\n\n" + MARKER.format(flavour=flavour) + "\n\n" + body
    return out


def flavours(root: Path = AGENTS) -> list:
    return list(load_roles(root)["flavours"])


def render(out_root: Path = AGENTS, check: bool = False, root: Path = AGENTS) -> list:
    """Write (or with check, compare) both flavours under out_root/<flavour>/. Returns drift lines."""
    drift = []
    for fl in flavours(root):
        files = render_flavour(fl, root)
        d = out_root / fl
        present = {p.name for p in d.glob("*.md")} if d.is_dir() else set()
        for name, text in sorted(files.items()):
            p = d / name
            if not p.is_file():
                drift.append(f"missing {fl}/{name}")
            elif p.read_text() != text:
                drift.append(f"differs {fl}/{name}")
            if not check:
                d.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
        for stale in sorted(present - set(files)):
            drift.append(f"stale {fl}/{stale}")
            if not check:
                (d / stale).unlink()
    return drift


# ---------------------------------------------------------------- detection

def serials_in(text: str) -> list:
    found = []
    for a, b in SERIAL_RE.findall(text):
        s = f"SLUS_{a}.{b}"
        if s in SERIALS and s not in found:
            found.append(s)
    return found


def game_of_title(text: str):
    for m in TITLE_RE.finditer(text):
        if m.group(1):
            return EPISODE[m.group(1).lower()]
    return None


def scan_names(base: Path, depth: int = SCAN_DEPTH):
    """File and directory names under base, breadth-first, bounded."""
    seen, queue = 0, [(base, 0)]
    while queue:
        d, lvl = queue.pop(0)
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            seen += 1
            if seen > SCAN_LIMIT:
                return
            yield Path(e.path)
            if lvl + 1 < depth and e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS:
                queue.append((Path(e.path), lvl + 1))


def git_remotes(project: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(project), "remote", "-v"], capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def detectors(project: Path, env: dict, notes: list):
    """Yield (flavour, game, reason) candidates in rule order (rules 2-7).

    Evidence in the project (its files, git remotes, config) comes before the ambient
    DECOMP_TARGET / DECOMP_BINARY / DECOMP_GAME_DIR: those are often exported shell-wide
    for one game and would otherwise label every other checkout. Inputs that were skipped
    (an unreadable manifest, a missing project) are appended to notes."""
    g = (env.get("DECOMP_GAME") or "").strip().lower()
    if g:
        if g == "xenosaga":
            yield "xenosaga", None, "DECOMP_GAME=xenosaga"
        elif g in ("xenosaga-1", "xenosaga-2", "xenosaga-3"):
            yield "xenosaga", g, f"DECOMP_GAME={g}"
        else:
            yield "generic", (None if g == "generic" else g), f"DECOMP_GAME={g}"
    if not project.is_dir():
        notes.append(f"project directory {project.name or project} does not exist")
    if project.is_dir():
        for p in scan_names(project):
            ser = serials_in(p.name)
            if ser:
                yield "xenosaga", SERIALS[ser[0]], f"project holds {p.relative_to(project)}"
                break
    remotes = git_remotes(project) if project.is_dir() else ""
    for line in remotes.splitlines():
        m = REMOTE_RE.search(line)
        if m:
            url = line.split()[1] if len(line.split()) > 1 else line
            if m.group(0).lower().startswith("xsg-"):
                game = {None: "xenosaga-1", "i": "xenosaga-2", "ii": "xenosaga-3"}[m.group(1) and m.group(1).lower()]
            else:
                game = game_of_title(url)
            yield "xenosaga", game, f"git remote {url}"
            break
    cfgs = [project / "config" / "originals.json"]
    for pat in ("*.yaml", "config/*.yaml", "config/split/*.yaml", "splat/*.yaml"):
        cfgs += sorted(project.glob(pat)) if project.is_dir() else []
    for c in cfgs:
        try:
            if c.is_file() and c.stat().st_size < 4_000_000:
                ser = serials_in(c.read_text(errors="replace"))
                if ser:
                    yield "xenosaga", SERIALS[ser[0]], f"{c.relative_to(project)} names {ser[0]}"
                    break
        except OSError:
            pass
    tgt = env.get("DECOMP_TARGET")
    if tgt:
        blob = None
        try:
            man = json.loads(Path(tgt).expanduser().read_text())
            if not isinstance(man, dict):
                raise ValueError(f"top level is {type(man).__name__}, not an object")
            units = man.get("units") or {}
            if not isinstance(units, dict):
                raise ValueError("units is not an object")
            fields = [str(man.get("name", "")), str(man.get("resident", ""))]
            fields += [str(u.get("binary", "")) for u in units.values() if isinstance(u, dict)]
            blob = " ".join(fields)
        except (OSError, ValueError) as e:
            notes.append(f"DECOMP_TARGET unreadable, ignored: {type(e).__name__}: {str(e)[:120]}")
        if blob is not None:
            ser = serials_in(blob)
            if ser:
                yield "xenosaga", SERIALS[ser[0]], f"DECOMP_TARGET manifest names {ser[0]}"
            elif re.search(r"xenosaga", blob, re.I):
                yield "xenosaga", game_of_title(blob), "DECOMP_TARGET manifest names Xenosaga"
    b = env.get("DECOMP_BINARY")
    if b:
        ser = serials_in(Path(b).name)
        if ser:
            yield "xenosaga", SERIALS[ser[0]], f"DECOMP_BINARY={Path(b).name}"
    gd = env.get("DECOMP_GAME_DIR")
    if gd and Path(gd).expanduser().is_dir():
        for p in scan_names(Path(gd).expanduser(), depth=2):
            ser = serials_in(p.name)
            if ser:
                yield "xenosaga", SERIALS[ser[0]], f"DECOMP_GAME_DIR holds {p.name}"
                break


def detect(project: Path, flavour: str = "auto", env: dict | None = None) -> dict:
    env = dict(os.environ) if env is None else env
    project = Path(project).expanduser().resolve()
    if flavour == "generic":
        return dict(flavour="generic", game=None, reason="--flavour generic")
    chosen, game, reason = None, None, None
    if flavour == "xenosaga":
        chosen, reason = "xenosaga", "--flavour xenosaga"
    notes = []
    for fl, g, why in detectors(project, env, notes):
        if chosen is None:
            chosen, game, reason = fl, g, why
            if fl == "generic":
                break
        elif chosen == "xenosaga" and fl == "xenosaga" and game is None and g:
            game = g
            reason += f"; episode from {why}"
        if chosen == "xenosaga" and game:
            break
    if chosen is None:
        chosen, game, reason = "generic", None, "no Xenosaga serial, title or remote found"
    if notes:
        reason += "; " + "; ".join(notes)
    return dict(flavour=chosen, game=game, reason=reason)


# ---------------------------------------------------------------- install

def kit_commit():
    try:
        r = subprocess.run(["git", "-C", str(KIT), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def marker_flavour(path: Path, known: tuple = ()):
    """The flavour of a kit-rendered agent, or None.

    The marker counts only as the first non-empty line after the YAML frontmatter, and only
    with a known flavour, so a file that merely quotes the marker is not the kit's."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        text = path.read_text(errors="replace")[:8192]
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    body = text[end + 5:].lstrip("\n")
    first = body.split("\n", 1)[0]
    m = MARKER_RE.fullmatch(first)
    if not m:
        return None
    if m.group(1) not in (known or tuple(flavours())):
        return None
    return m.group(1)


def read_json_file(path: Path, schema: str):
    """(data, error) for a state file the kit owns: refuses symlinks and foreign content."""
    if path.is_symlink():
        return None, f"{path.name} is a symlink"
    if not path.exists():
        return {}, None
    if not path.is_file():
        return None, f"{path.name} is not a regular file"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return None, f"{path.name} is not kit JSON ({type(e).__name__})"
    if not isinstance(data, dict) or data.get("schema") != schema:
        return None, f"{path.name} does not carry schema {schema}"
    return data, None


def git_ignored(dest: Path, name: str):
    """True/False when dest is in a git work tree, None otherwise."""
    probe = dest if dest.is_dir() else dest.parent
    while not probe.is_dir() and probe != probe.parent:
        probe = probe.parent
    try:
        r = subprocess.run(["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"], capture_output=True,
                           text=True, timeout=10)
        if r.returncode != 0 or r.stdout.strip() != "true":
            return None
        r = subprocess.run(["git", "-C", str(probe), "check-ignore", "-q", "--", str(dest / name)],
                           capture_output=True, text=True, timeout=10)
        return {0: True, 1: False}.get(r.returncode)
    except (OSError, subprocess.SubprocessError):
        return None


def free_backup(p: Path) -> Path:
    """NAME.md.kit-backup, or .kit-backup.N: the first name that is neither a file nor a link."""
    n = 0
    while True:
        b = p.with_name(p.name + BACKUP_SUFFIX + (f".{n}" if n else ""))
        if not b.is_symlink() and not b.exists():
            return b
        n += 1


def default_kit_file() -> Path:
    return Path.home() / ".claude" / "decomp-compat-kit.json"


def install(dest: Path, det: dict, dry_run: bool = False, force: bool = False, root: Path = AGENTS,
            kit_file: Path | None = None) -> tuple:
    kit_file = default_kit_file() if kit_file is None else kit_file
    known = tuple(flavours(root))
    files = render_flavour(det["flavour"], root)
    renders = {}
    for fl in known:
        for name, text in render_flavour(fl, root).items():
            renders.setdefault(name, set()).add(sha256_text(text))
    plan = dict(dest=str(dest), flavour=det["flavour"], game=det["game"], reason=det["reason"],
                write=[], replace=[], remove=[], refused=[], forced=[], dry_run=dry_run)
    errors = []
    if dest.is_symlink() and not dest.is_dir():
        errors.append(f"{dest} is a dangling symlink")
    state, err = read_json_file(dest / STATE_FILE, STATE_SCHEMA)
    if err:
        errors.append(err + ": not overwritten")
        state = {}
    _, err = read_json_file(kit_file, KIT_FILE_SCHEMA)
    if err:
        errors.append(err + f" ({kit_file}): not overwritten")
    recorded = state.get("hashes") if isinstance(state.get("hashes"), dict) else {}

    def edited(p: Path) -> bool:
        h = sha256_file(p)
        return not (h == recorded.get(p.name) or h in renders.get(p.name, ()) or h == sha256_text(files.get(p.name, "")))

    def claim(p: Path, why: str):
        (plan["forced"] if force else plan["refused"]).append(p.name if force else f"{p.name} ({why})")

    for name in sorted(files):
        p = dest / name
        if p.is_symlink():
            plan["refused"].append(f"{name} (symlink)")
        elif not p.exists():
            plan["write"].append(name)
        elif not p.is_file():
            plan["refused"].append(f"{name} (not a regular file)")
        elif marker_flavour(p, known) is None:
            claim(p, "no kit marker: a hand-written agent?")
        elif edited(p):
            claim(p, "kit agent edited since the kit wrote it")
        else:
            plan["replace"].append(name)
    kit_names = set(renders) | {f"{n}.md" for n in (state.get("agents") or []) if isinstance(n, str)}
    if dest.is_dir():
        for p in sorted(dest.glob("*.md")):
            if p.name in files or p.name not in kit_names or p.is_symlink() or marker_flavour(p, known) is None:
                continue
            if edited(p):
                plan["refused"].append(f"{p.name} (kit agent edited since the kit wrote it; not removed)")
            else:
                plan["remove"].append(p.name)
    if errors or plan["refused"]:
        plan["error"] = "; ".join(errors + (["refused: " + ", ".join(plan["refused"]) +
                                             "; rename or remove them, or rerun with --force (symlinks never)"]
                                            if plan["refused"] else []))
        return plan, 1
    ignored = git_ignored(dest, STATE_FILE)
    if ignored is False:
        plan["git_note"] = (f"{dest} is in a git work tree and the agents and {STATE_FILE} are not ignored there; "
                            "they hold no machine paths, but commit them only if the project should carry the agents")
    plan["kit_file"] = str(kit_file)
    if dry_run:
        return plan, 0
    dest.mkdir(parents=True, exist_ok=True)
    for name in plan["forced"]:
        shutil.copyfile(dest / name, free_backup(dest / name))
    for name, text in files.items():
        (dest / name).write_text(text)
    for name in plan["remove"]:
        (dest / name).unlink()
    state = dict(schema=STATE_SCHEMA, flavour=det["flavour"], game=det["game"], reason=det["reason"],
                 kit_commit=kit_commit(), agents=sorted(n[:-3] for n in files),
                 hashes={n: sha256_text(t) for n, t in sorted(files.items())})
    (dest / STATE_FILE).write_text(json.dumps(state, indent=2) + "\n")
    kit_file.parent.mkdir(parents=True, exist_ok=True)
    kit_file.write_text(json.dumps(dict(schema=KIT_FILE_SCHEMA, kit_root=str(KIT), kit_commit=kit_commit()),
                                   indent=2) + "\n")
    plan["state_file"] = str(dest / STATE_FILE)
    return plan, 0


# ---------------------------------------------------------------- self-test

def self_test() -> int:
    fails = []

    def ok(cond, what):
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            fails.append(what)

    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        a, b = t / "a", t / "b"
        render(a)
        render(b)
        same = all((a / fl / p.name).read_text() == p.read_text() for fl in flavours() for p in (b / fl).glob("*.md"))
        n = sum(1 for _ in (a / "generic").glob("*.md"))
        ok(same and n == len(load_roles()["roles"]), f"render is deterministic ({n} agents per flavour)")
        ok(render(a, check=True) == [], "check on a fresh render reports no drift")
        victim = next((a / "generic").glob("*.md"))
        victim.write_text(victim.read_text() + "edited\n")
        (a / "xenosaga" / "stale-agent.md").write_text("x\n")
        gone = next((a / "xenosaga").glob("*-orchestrator.md"))
        gone.unlink()
        drift = render(a, check=True)
        ok(any(d.startswith("differs generic/") for d in drift) and "stale xenosaga/stale-agent.md" in drift
           and any(d.startswith("missing xenosaga/") for d in drift), f"check reports drift: {drift}")
        texts = render_flavour("xenosaga")
        ok(all(MARKER_RE.search(x) and x.startswith("---\nname: ") for x in texts.values()),
           "every rendered agent has frontmatter and the marker")
        ok(all("{{" not in x for x in texts.values()), "no placeholder left in a rendered agent")
        gen_names = set(render_flavour("generic"))
        ok(not (gen_names & set(texts)) and all(n.startswith("kit-") for n in texts),
           "the Xenosaga names carry the kit- prefix and differ from the generic ones")

        # unknown placeholder is an error
        broken = t / "broken" / "agents"
        shutil.copytree(AGENTS, broken, ignore=shutil.ignore_patterns("generic", "xenosaga"))
        (broken / "templates" / "verifier.md").write_text("{{nonsense}}\n")
        try:
            render_flavour("generic", broken)
            ok(False, "unknown placeholder fails the render")
        except RenderError as e:
            ok("nonsense" in str(e), f"unknown placeholder fails the render ({e})")

        # detection
        empty = t / "empty"
        empty.mkdir()
        ok(detect(empty, env={})["flavour"] == "generic", "empty directory -> generic")
        x2 = t / "x2"
        (x2 / "game").mkdir(parents=True)
        (x2 / "game" / "SLUS_208.92").write_text("")
        d = detect(x2, env={})
        ok(d["flavour"] == "xenosaga" and d["game"] == "xenosaga-2", f"SLUS_208.92 in the project -> {d}")
        d = detect(x2, env={"DECOMP_GAME": "generic"})
        ok(d["flavour"] == "generic", f"DECOMP_GAME=generic wins over a serial -> {d}")
        d = detect(empty, env={"DECOMP_BINARY": "/somewhere/SLUS_211.33"})
        ok(d["game"] == "xenosaga-2", f"disc 2 serial in DECOMP_BINARY -> {d}")
        man = t / "target.json"
        man.write_text(json.dumps({"schema": "decomp-target/1", "name": "Xenosaga Episode III (USA)",
                                   "units": {"main": {"binary": "MAIN.ELF"}}}))
        d = detect(empty, env={"DECOMP_TARGET": str(man)})
        ok(d["flavour"] == "xenosaga" and d["game"] == "xenosaga-3", f"manifest title -> {d}")
        bad = t / "bad-target.json"
        bad.write_text("[1, 2]")
        d = detect(empty, env={"DECOMP_TARGET": str(bad)})
        d2 = detect(empty, env={"DECOMP_TARGET": str(t / "missing.json")})
        ok(d["flavour"] == "generic" and "DECOMP_TARGET unreadable" in d["reason"]
           and "DECOMP_TARGET unreadable" in d2["reason"], f"a malformed or missing manifest is named -> {d['reason']}")
        x1 = t / "x1"
        (x1 / "config").mkdir(parents=True)
        (x1 / "config" / "originals.json").write_text('{"units": {"main": {"file": "SLUS_204.69"}}}')
        d = detect(x1, env={})
        ok(d["game"] == "xenosaga-1" and "originals.json" in d["reason"], f"config/originals.json -> {d}")
        d = detect(empty, flavour="xenosaga", env={})
        ok(d["flavour"] == "xenosaga" and d["game"] is None, f"--flavour xenosaga without an episode -> {d}")
        d = detect(empty, env={"DECOMP_GAME": "final-fantasy-x"})
        ok(d["flavour"] == "generic" and d["game"] == "final-fantasy-x", f"another game id -> {d}")
        gdir = t / "x1-originals"
        gdir.mkdir()
        (gdir / "SLUS_204.69").write_text("")
        d = detect(x2, env={"DECOMP_GAME_DIR": str(gdir)})
        ok(d["game"] == "xenosaga-2", f"the project's serial wins over a shell-wide DECOMP_GAME_DIR -> {d}")
        d = detect(empty, env={"DECOMP_GAME_DIR": str(gdir)})
        ok(d["game"] == "xenosaga-1", f"DECOMP_GAME_DIR alone -> {d}")
        if shutil.which("git"):
            x3 = t / "x3"
            x3.mkdir()
            subprocess.run(["git", "init", "-q", str(x3)], check=True)
            subprocess.run(["git", "-C", str(x3), "remote", "add", "origin", "https://example.org/someone/xsg-iii-decomp"],
                           check=True)
            d = detect(x3, env={})
            ok(d["game"] == "xenosaga-3" and "remote" in d["reason"], f"git remote xsg-iii-decomp -> {d}")
            subprocess.run(["git", "-C", str(x3), "remote", "set-url", "origin", "https://example.org/someone/xsg-ii-decomp"],
                           check=True)
            ok(detect(x3, env={})["game"] == "xenosaga-2", "git remote xsg-ii-decomp -> xenosaga-2")
            plan, rc = install(x3 / ".claude" / "agents", dict(flavour="generic", game=None, reason="test"),
                               dry_run=True, kit_file=t / "kit-x3.json")
            ok(rc == 0 and "git_note" in plan, "install in a git work tree notes that the agents are not ignored")

        # install: refusal, force, flavour swap, ownership
        kf = t / "home" / ".claude" / "decomp-compat-kit.json"
        proj = t / "proj"
        dest = proj / ".claude" / "agents"
        dest.mkdir(parents=True)
        (dest / "matcher-t1.md").write_text("---\nname: matcher-t1\n---\nmine\n")
        (dest / "my-helper.md").write_text("---\nname: my-helper\n---\nmine\n")
        gen = dict(flavour="generic", game=None, reason="test")
        plan, rc = install(dest, gen, kit_file=kf)
        ok(rc == 1 and plan["refused"][0].startswith("matcher-t1.md") and (dest / "matcher-t1.md").read_text().endswith("mine\n")
           and not (dest / "decomp-orchestrator.md").exists() and not kf.exists(),
           "install refuses a hand-written agent and writes nothing")
        plan, rc = install(dest, gen, dry_run=True, force=True, kit_file=kf)
        ok(rc == 0 and not (dest / "decomp-orchestrator.md").exists(), "dry run writes nothing")
        plan, rc = install(dest, gen, force=True, kit_file=kf)
        ok(rc == 0 and marker_flavour(dest / "matcher-t1.md") == "generic"
           and (dest / ("matcher-t1.md" + BACKUP_SUFFIX)).read_text().endswith("mine\n"),
           "--force replaces it and keeps a backup")
        st = json.loads((dest / STATE_FILE).read_text())
        kd = json.loads(kf.read_text())
        ok("kit_root" not in st and kd.get("kit_root") == str(KIT),
           "the kit's path goes to the user-level file, never into the project's state file")
        # local edit of a kit agent
        m1 = dest / "matcher-t1.md"
        m1.write_text(m1.read_text() + "MY LOCAL NOTE\n")
        plan, rc = install(dest, gen, kit_file=kf)
        ok(rc == 1 and "MY LOCAL NOTE" in m1.read_text() and any("edited" in r for r in plan["refused"]),
           "an edited kit agent refuses the install")
        plan, rc = install(dest, gen, force=True, kit_file=kf)
        ok(rc == 0 and "MY LOCAL NOTE" not in m1.read_text()
           and "MY LOCAL NOTE" in (dest / ("matcher-t1.md" + BACKUP_SUFFIX + ".1")).read_text(),
           "--force overwrites an edited kit agent and keeps a second backup")
        # a foreign file quoting the marker, and one with an unknown flavour, are not the kit's
        quote = dest / "notes-about-kit.md"
        quote.write_text("---\nname: notes\n---\n\nsee:\n" + MARKER.format(flavour="generic") + "\n")
        alien = dest / "other-tool.md"
        alien.write_text("---\nname: other-tool\n---\n\n" + MARKER.format(flavour="zzz") + "\n")
        xen = dict(flavour="xenosaga", game="xenosaga-2", reason="test")
        plan, rc = install(dest, xen, kit_file=kf)
        left = sorted(p.name for p in dest.glob("*.md"))
        ok(rc == 0 and not any(marker_flavour(dest / n) == "generic" for n in left)
           and "my-helper.md" in left and "kit-shion-orchestrator.md" in left
           and quote.exists() and alien.exists()
           and len(plan["remove"]) == len(load_roles()["roles"]), f"flavour swap removes the generic set only ({len(left)} files)")
        st = json.loads((dest / STATE_FILE).read_text())
        ok(st["flavour"] == "xenosaga" and st["game"] == "xenosaga-2" and len(st["hashes"]) == len(load_roles()["roles"]),
           "state file records flavour, game and the written hashes")
        # symlinks are refused: a dangling agent link and a linked state file
        q = t / "q" / ".claude" / "agents"
        q.mkdir(parents=True)
        outside = t / "OUTSIDE.md"
        (q / "matcher-t1.md").symlink_to(outside)
        plan, rc = install(q, gen, force=True, kit_file=kf)
        ok(rc == 1 and not outside.exists(), "a dangling symlink named like an agent is refused, even with --force")
        r = t / "r" / ".claude" / "agents"
        r.mkdir(parents=True)
        precious = t / "precious.txt"
        precious.write_text("keep\n")
        (r / STATE_FILE).symlink_to(precious)
        plan, rc = install(r, gen, force=True, kit_file=kf)
        ok(rc == 1 and precious.read_text() == "keep\n", "a symlinked state file is refused")
        foreign = t / "s" / ".claude" / "agents"
        foreign.mkdir(parents=True)
        (foreign / STATE_FILE).write_text('{"something": "else"}\n')
        plan, rc = install(foreign, gen, kit_file=kf)
        ok(rc == 1 and json.loads((foreign / STATE_FILE).read_text()) == {"something": "else"},
           "a state file without the kit schema is not overwritten")
        # command line: a missing project and --user without --flavour are refused
        ok(main(["install", "--project", str(t / "nope" / "deeper"), "--dry-run"]) == 1
           and not (t / "nope").exists(), "install into a missing project directory is refused")
        ok(main(["install", "--user", "--dry-run"]) == 1, "--user without an explicit --flavour is refused")

    print(f"{'FAILED' if fails else 'passed'}: {len(fails)} failure(s)")
    return 1 if fails else 0


# ---------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("render", help="render both flavours into agents/")
    r.add_argument("--check", action="store_true", help="write nothing; exit 1 listing drift")
    d = sub.add_parser("detect", help="print the detected flavour and game as JSON")
    d.add_argument("--project", default=".", help="project directory (default: the current directory)")
    d.add_argument("--flavour", choices=["auto", "generic", "xenosaga"], default="auto")
    i = sub.add_parser("install", help="install one flavour into .claude/agents")
    where = i.add_mutually_exclusive_group()
    where.add_argument("--project", default=None, help="existing project directory (default: the current directory)")
    where.add_argument("--user", action="store_true", help="install into ~/.claude/agents (needs --flavour)")
    i.add_argument("--flavour", choices=["auto", "generic", "xenosaga"], default="auto")
    i.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    i.add_argument("--force", action="store_true",
                   help="overwrite same-named files that lack the kit marker or were edited (a .kit-backup copy is kept)")
    sub.add_parser("self-test", help="temp-directory checks of this tool")
    a = ap.parse_args(argv)

    try:
        if a.self_test or a.cmd == "self-test":
            return self_test()
        if a.cmd == "render":
            drift = render(check=a.check)
            if a.check:
                for line in drift:
                    print(line)
                print(f"{len(drift)} file(s) drift" if drift else "agents are up to date")
                return 1 if drift else 0
            print(json.dumps(dict(rendered={fl: sorted(render_flavour(fl)) for fl in flavours()},
                                  changed=drift), indent=2))
            return 0
        if a.cmd == "detect":
            print(json.dumps(detect(Path(a.project), a.flavour), indent=2))
            return 0
        if a.cmd == "install":
            if a.user:
                if a.flavour == "auto":
                    print("install --user needs --flavour generic|xenosaga: one user-level set serves every project, "
                          "so the current directory must not pick the flavour or the episode", file=sys.stderr)
                    return 1
                dest = Path.home() / ".claude" / "agents"
                det = dict(flavour=a.flavour, game=None,
                           reason=f"--user --flavour {a.flavour} (episode left to the doctor in each project)")
            else:
                project = Path(a.project or ".").expanduser().resolve()
                if not project.is_dir():
                    print(f"install: project directory {project} does not exist", file=sys.stderr)
                    return 1
                dest = project / ".claude" / "agents"
                det = detect(project, a.flavour)
            plan, rc = install(dest, det, dry_run=a.dry_run, force=a.force)
            print(json.dumps(plan, indent=2))
            return rc
    except RenderError as e:
        print(f"render error: {e}", file=sys.stderr)
        return 2
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
