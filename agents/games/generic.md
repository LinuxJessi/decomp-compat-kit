<!--
Game text for the generic agent set. tools/install_agents.py reads the "## key" sections:
"setup" fills {{setup}} in every template; a section named after a template or a role id
("matcher", "matcher-t3", "reviewer", ...) fills {{game}} for that agent. Text before the
first section (this comment) is ignored. Use "###" or deeper inside a section.
-->

## setup

`KIT` is the decomp-compat-kit checkout: `$DECOMP_KIT` when set, else `kit_root` in `~/.claude/decomp-compat-kit.json` (the installer writes it there, outside the project, because it is a path on this machine). With the `ps2decomp` MCP server registered (`KIT/mcp/README.md`) use its tools; otherwise run the CLI as `KIT/.venv/bin/python KIT/tools/<tool>.py` (plain `python3` when the kit has no venv; `match_kit.py init` needs rabbitizer). Paths and the game come from the environment (`DECOMP_UPSTREAM`, `DECOMP_TARGET`, `DECOMP_GAME_DIR`, `DECOMP_BINARY`, `DECOMP_UNIT`, `DECOMP_SYMBOLS`, `DECOMP_TOOLCHAINS`, `EE_ROOTFS`, `DECOMP_SANDBOXES`), never from a model. The kit compiles with SCE ee-gcc only: on a CodeWarrior or ProDG original (the doctor reports the family) only the ee-gcc minority can match, and `match_init` refuses sq-prologue functions.

## orchestrator

Before the first wave read the upstream project's README for its build, its whole-file gate command and its contribution rules; the gate command goes into every integrator prompt as GATE. A tree without the kit's native layout (`config/tu-build.json` plus per-TU configs) is served through a target manifest (`DECOMP_TARGET`; `KIT/docs/TOOLS.md`, "Targets"). What is known per title, including its compiler and flags, is on its page under `KIT/docs/games/`. Before planning and before any push, fetch the upstream and read what changed since your base; a claim about what the upstream has is checked against its current main branch and records the commit it was checked at.

## matcher

Your title's compiler, flags and known quirks are on its page under `KIT/docs/games/` when it has one; the check line names the compiler and the TU. The upstream project's published idioms (the TU's matched siblings) outrank generic style.

## reviewer

The upstream project's contribution rules (its README, its accepted pull requests) are the style authority over generic readability rules; cite the rule behind every rejection.

## integrator

GATE is the command named in your prompt, taken from the upstream project's README; it must compare every linked original whole-file. If the prompt names none, stop and ask: a match_kit verdict is not a gate.

## enricher

A sibling build of the same program (a demo, another region, a later revision) is the strongest donor: `transfer_symbols.py --sibling` maps it, and in target mode `ledger.py sibling NAME` checks matched rows against it.

## iop-matcher

When an IOP module kept its symbols, `dump_symbols.py --recursive` exports them (`KIT/docs/TOOLS.md`); IOP modules of middleware vendors are often unstripped even when the game's EE code is not (`find_symbols.py` lists which files carry names).
