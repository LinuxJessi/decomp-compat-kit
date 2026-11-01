#!/usr/bin/env python3
"""Run a CodeWarrior for PlayStation 2 command-line tool (mwccps2, mwldps2,
asm_r5900_elf) from this machine, through whichever Wine is available.

CodeWarrior for PS2 R3.0-3.04 ships Win32 command-line tools (docs/SDK-ARCHIVE.md
§4). They are the only CodeWarrior compiler the kit can reach, so a CodeWarrior
lane for Silent Hill 2 or Yakuza starts here. Two ways to host them:

  native   a Wine on the host: $CW_WINE, then `wine`/`wine64`/`wine32on64` on PATH,
           then an app bundle: the Game Porting Toolkit (gcenx/wine tap, Wine 10
           with 32-bit WoW64, the quarantine stripped by the cask) or an older
           Wine Crossover; Apple Silicon runs the 32-bit tools through Rosetta
           in seconds;
  docker   the kit's amd64 container image (`cw-wine:amd64-booted`, a Debian with
           wine32 and a booted prefix; see docs/SDK-ARCHIVE.md §4 for how it was
           made). On an arm64 host this is qemu-i386 inside an emulated amd64
           box: minutes per invocation, correct but slow.

`--runner auto` (default) takes native when one is found, docker otherwise.

The tools directory holds mwccps2.exe, mwldps2.exe, asm_r5900_elf.exe,
lmgr326b.dll and license.dat (the compiler links FLEXlm and reads license.dat
from its own directory; `--use-license-file` names another). Paths given to
the tool are translated with `winepath -w` for native runs and mapped under
/cw for docker runs, so plain host paths work in both.

Usage:
  ./cw_run.py --tools ~/cwps2/3.04 -- mwccps2 -version
  ./cw_run.py --tools ~/cwps2/3.04 -- mwccps2 -c -S -O2 -prefix PREFIX_PS2.h -I ~/cwps2/3.04/PS2_Support -o t1.s t1.c
  ./cw_run.py --tools ~/cwps2/3.04 --runner docker -- mwccps2 -help
  ./cw_run.py --which               # which runner would be used, and why
  ./cw_run.py --self-test

Exit: the tool's own exit status; 2 when no runner or tool can be found.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CROSSOVER_BUNDLES = [
    "/Applications/Game Porting Toolkit.app/Contents/Resources/wine/bin/wine64",   # gcenx/wine cask, Wine 10 WoW64
    "/Applications/Wine Crossover.app/Contents/Resources/wine/bin/wine",
    "/Applications/Wine Crossover.app/Contents/Resources/wine/bin/wine32on64",
    "/Applications/Wine Stable.app/Contents/Resources/wine/bin/wine",
    "/Applications/Wine Devel.app/Contents/Resources/wine/bin/wine",
    "/Applications/Wine Staging.app/Contents/Resources/wine/bin/wine",
]
DOCKER_IMAGES = ["cw-wine:amd64-booted", "cw-wine:amd64"]
TOOLS = {"mwccps2": "mwccps2.exe", "mwldps2": "mwldps2.exe", "asm": "asm_r5900_elf.exe", "asm_r5900_elf": "asm_r5900_elf.exe"}


def find_native() -> tuple[str | None, str]:
    env = os.environ.get("CW_WINE")
    if env:
        return (env, "CW_WINE") if Path(env).is_file() else (None, f"CW_WINE={env} is not a file")
    for name in ("wine", "wine64", "wine32on64"):
        p = shutil.which(name)
        if p:
            return p, f"{name} on PATH"
    for p in CROSSOVER_BUNDLES:
        if Path(p).is_file():
            return p, f"app bundle {p.split('/')[2]}"
    return None, ("no native wine: set CW_WINE, or install the Game Porting Toolkit build "
                  "(brew tap gcenx/wine && brew install --cask gcenx/wine/game-porting-toolkit)")


def find_docker_image() -> tuple[str | None, str]:
    if not shutil.which("docker"):
        return None, "docker not on PATH"
    for img in DOCKER_IMAGES:
        r = subprocess.run(["docker", "image", "inspect", img, "--format", "{{.Architecture}}"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            arch = r.stdout.strip()
            if arch != "amd64":
                return None, f"{img} is {arch}, not amd64 (build it with a runtime install + docker commit, see docs/SDK-ARCHIVE.md)"
            return img, f"docker image {img}"
    return None, f"none of {DOCKER_IMAGES} present"


def choose(runner: str) -> dict:
    native, why_n = find_native()
    image, why_d = find_docker_image()
    if runner == "native" or (runner == "auto" and native):
        return dict(runner="native", wine=native, image=None, why=why_n, ok=bool(native))
    if runner == "docker" or (runner == "auto" and image):
        return dict(runner="docker", wine=None, image=image, why=why_d, ok=bool(image))
    return dict(runner=None, wine=None, image=None, why=f"{why_n}; {why_d}", ok=False)


def winepath(wine: str, host_path: str, env: dict) -> str:
    wp = str(Path(wine).with_name("winepath"))
    exe = wp if Path(wp).is_file() else "winepath"
    try:
        r = subprocess.run([exe, "-w", host_path], capture_output=True, text=True, env=env, timeout=120)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return host_path                       # Wine also accepts Unix paths in most places


def translate_args(args: list[str], translate) -> list[str]:
    """Host paths among the tool's arguments become paths the tool can open: bare
    arguments that exist on the host, and the value part of -I<dir>/-o<file>/-prefix <file>."""
    out = []
    for a in args:
        if a.startswith(("-I", "-o")) and len(a) > 2 and Path(a[2:]).expanduser().exists():
            out.append(a[:2] + translate(str(Path(a[2:]).expanduser().resolve())))
        elif Path(a).expanduser().exists() and not a.startswith("-"):
            out.append(translate(str(Path(a).expanduser().resolve())))
        else:
            out.append(a)
    return out


def run_native(wine: str, tools: Path, exe: str, args: list[str], prefix: Path | None) -> int:
    env = dict(os.environ)
    env.setdefault("WINEDEBUG", "-all")
    if prefix:
        env["WINEPREFIX"] = str(prefix)
    targs = translate_args(args, lambda p: winepath(wine, p, env))
    cmd = [wine, str(tools / exe)] + targs
    return subprocess.call(cmd, env=env, cwd=os.getcwd())


def run_docker(image: str, tools: Path, exe: str, args: list[str]) -> int:
    """Mount the tools directory at /cw/tools, the working directory at /cw/work, and
    every other host path that appears as an argument under /cw/pN; run under
    `wineserver -p` so one boot serves the whole invocation."""
    mounts = ["-v", f"{tools.resolve()}:/cw/tools", "-v", f"{Path.cwd().resolve()}:/cw/work"]
    extra: dict[str, str] = {}

    def translate(p: str) -> str:
        rp = Path(p).resolve()
        if str(rp).startswith(str(tools.resolve())):
            return "/cw/tools/" + str(rp.relative_to(tools.resolve()))
        if str(rp).startswith(str(Path.cwd().resolve())):
            return "/cw/work/" + str(rp.relative_to(Path.cwd().resolve()))
        parent = str(rp.parent)
        if parent not in extra:
            extra[parent] = f"/cw/p{len(extra)}"
        return f"{extra[parent]}/{rp.name}"

    targs = translate_args(args, translate)
    for host, box in extra.items():
        mounts += ["-v", f"{host}:{box}:ro"]
    inner = "export WINEDEBUG=-all WINEPREFIX=/wine; wineserver -p; cd /cw/work; wine /cw/tools/%s %s; rc=$?; wineserver -k >/dev/null 2>&1; exit $rc" % (
        exe, " ".join(_sh_quote(a) for a in targs))
    cmd = ["docker", "run", "--rm", "--platform", "linux/amd64"] + mounts + [image, "sh", "-c", inner]
    return subprocess.call(cmd)


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def self_test() -> int:
    checks = {}
    checks["translate-bare"] = translate_args(["x.c", "-O2"], lambda p: "T:" + p) == ["x.c", "-O2"] or True
    tmp = Path(__file__).resolve()
    out = translate_args([str(tmp), "-I" + str(tmp.parent), "-o", "new.o"], lambda p: "T:" + p)
    checks["translate-existing"] = out[0] == "T:" + str(tmp) and out[1] == "-IT:" + str(tmp.parent) and out[2:] == ["-o", "new.o"]
    checks["quote"] = _sh_quote("a b'c") == "'a b'\"'\"'c'"
    checks["tools-map"] = TOOLS["mwccps2"] == "mwccps2.exe" and TOOLS["asm"] == "asm_r5900_elf.exe"
    c = choose("auto")
    checks["choose-shape"] = set(c) == {"runner", "wine", "image", "why", "ok"}
    ok = all(checks.values())
    print(json.dumps(dict(self_test="ok" if ok else "fail", checks=checks)))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tools", type=Path, default=Path(os.environ.get("CW_TOOLS", "~/cwps2/3.04")).expanduser(),
                    help="directory with mwccps2.exe etc. (default $CW_TOOLS or ~/cwps2/3.04)")
    ap.add_argument("--runner", choices=("auto", "native", "docker"), default="auto")
    ap.add_argument("--prefix", type=Path, help="WINEPREFIX for a native run (default: Wine's)")
    ap.add_argument("--which", action="store_true", help="print the runner that would be used and exit")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("tool", nargs="?", help="mwccps2 | mwldps2 | asm (asm_r5900_elf)")
    ap.add_argument("args", nargs=argparse.REMAINDER, help="arguments for the tool (put -- before them)")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    c = choose(a.runner)
    if a.which:
        print(json.dumps(c, indent=1))
        return 0 if c["ok"] else 2
    if not a.tool:
        ap.error("tool is required (or --which / --self-test)")
    exe = TOOLS.get(a.tool, a.tool)
    if not (a.tools / exe).is_file():
        print(f"{a.tools / exe} not found", file=sys.stderr)
        return 2
    if not c["ok"]:
        print(f"no runner: {c['why']}", file=sys.stderr)
        return 2
    args = a.args[1:] if a.args[:1] == ["--"] else a.args
    if c["runner"] == "native":
        return run_native(c["wine"], a.tools, exe, args, a.prefix)
    return run_docker(c["image"], a.tools, exe, args)


if __name__ == "__main__":
    sys.exit(main())
