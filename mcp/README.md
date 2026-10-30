# ps2decomp: the kit as an MCP server

`mcp/server.py` exposes the PS2-Decomp Compatibility Kit to any Model Context
Protocol client (Claude Code, Claude Desktop, Codex, Cursor, a script) over
stdio. Every tool shells out to the kit's own `tools/*.py`, so a verdict reached
through the server is the verdict the command line gives, and the rules the kit
enforces stay on the server side: the anti-cheat lint, the no-progress stop, the
UNVERIFIABLE stop, the 2 KB evidence card. Tools return analyses, verdicts and
diffs; no tool returns game bytes.

Built on the Python SDK v2 (`MCPServer`, protocol revision 2026-07-28, stdio and
Streamable HTTP). Long-running work returns a job handle and is polled with
`job_status`, because the protocol's tasks extension is not in the SDK yet and a
handle passed as an ordinary argument is what the stateless revision asks for.

## Install

```sh
cd decomp-compat-kit
uv venv -p 3.14 .venv && VIRTUAL_ENV=$PWD/.venv uv pip install -r mcp/requirements.txt
.venv/bin/python mcp/server.py          # speaks MCP on stdin/stdout; Ctrl-C to stop
```

Register with Claude Code (user scope, so it is available in every project):

```sh
claude mcp add --scope user ps2decomp \
  -e "DECOMP_UPSTREAM=/path/to/xsg-i-decomp" \
  -e "DECOMP_GAME_DIR=/path/to/extracted/originals" \
  -e "EE_ROOTFS=$HOME/ee-rootfs" \
  -- /path/to/decomp-compat-kit/.venv/bin/python /path/to/decomp-compat-kit/mcp/server.py
```

Add `-e DECOMP_BINARY=OV01.OVL -e DECOMP_UNIT=ov01` to work on an overlay, and
`-e DECOMP_TOOLCHAINS=...` when the decomp's pinned (contract) toolchains live
somewhere other than `<upstream>/.work/toolchains`. Paths are configuration only;
the model never chooses them (the 2026-07-28 revision deprecates roots).

## Tools

| stage | tools |
|---|---|
| health | `doctor` |
| identify | `identify_fingerprint`, `identify_unwrap` |
| name | `name_lookup`, `name_transfer_symbols` |
| triage | `triage_function`, `triage_unit` |
| probe | `probe_toolchain_start` (job) |
| verify | `verify_tu`, `verify_unit_start` (job), `job_status` |
| match | `match_init`, `match_card`, `match_disasm`, `match_source`, `match_write_function`, `match_edit`, `match_check`, `match_verdict`, `match_best`, `match_patch`, `match_list` |

Resources: `kit://docs/{playbook|toolchain-differences|corpus|readme}`,
`kit://sandbox/{function}/card`, `kit://sandbox/{function}/budget`.
Prompt: `match_one_function(function, ceiling)`, the stock-lane worker brief.

The matching loop a client runs: `match_init` (one sandbox per function; refused
up front when the original refers to something the verifier cannot name) ->
`match_card` and `match_disasm` (evidence, never whole files) ->
`match_write_function` (replaces the `INCLUDE_ASM` stub; `declarations` for
externs and typedefs, `match_edit` for a TU-local header field) -> `match_check`
with one changed hypothesis per call -> repeat until MATCH or a stop ->
`match_verdict` and `match_patch`.

## First run (2026-10-30)

Exercised in-process with the SDK's `Client` against the Xenosaga I upstream
checkout, on an overlay unit (`DECOMP_UNIT=ov01`, `DECOMP_BINARY=OV01.OVL`),
which is what shook out the last main-executable assumptions in `match_kit.py`
(the resident image's `_gp` and `GLOBAL` names for an overlay, placement from
the program headers, the judge reading the object against the configured
binary's symbol table). `doctor`, lookup by name and by address, triage,
fingerprint, the resources and the prompt all answered; `match_init` opened a
sandbox for `dataMotAdrSet` (52 bytes, class easy) with a baseline of 29
matched functions in its TU; three forms were judged (best: `SIZE_DIFF`,
44 bytes against 52, with the full ours-versus-original row table), the header
split and the typedef were carried as `scope`, and `match_list` reported the
sandbox. The function itself is still open: the original forms the slot
address as actor plus (index plus 0x8DC) and keeps the scaled index in a second
register, a shape no typed spelling tried so far reproduces.

## Not yet

- The tasks extension once the SDK carries it (replaces the job handles).
- A live-emulator server on the flag-capture rig (snapshot/diff/watch, poke
  tests, savestate bisection) alongside mcp-pine's raw memory tools.
- Streamable HTTP for a shared box, and per-client sandbox namespaces.
