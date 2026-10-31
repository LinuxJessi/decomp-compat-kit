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

Resources: `kit://docs/{playbook|toolchain-differences|corpus|codewarrior|sdk-archive|readme}`,
`kit://sandbox/{function}/card`, `kit://sandbox/{function}/budget`.
Prompt: `match_one_function(function, ceiling)`, the stock-lane worker brief.

The matching loop a client runs: `match_init` (one sandbox per function; refused
up front when the original refers to something the verifier cannot name) ->
`match_card` and `match_disasm` (evidence, never whole files) ->
`match_write_function` (replaces the `INCLUDE_ASM` stub; `declarations` for
externs and typedefs, `match_edit` for a TU-local header field) -> `match_check`
with one changed hypothesis per call -> repeat until MATCH or a stop ->
`match_verdict` and `match_patch`.

## The compiler-family gate (2026-10-31)

The kit compiles with SCE ee-gcc only. `doctor` now fingerprints the configured
original once (`binary_compiler`: family, verdict, startup shape) and, when it
is Metrowerks CodeWarrior or SN ProDG (or the census cannot tell them apart),
`match_init` reads the function's prologue through `match_kit.py disasm` before
opening anything: an `sq`-saving prologue is refused with `rc 3` and the reason,
because no ee-gcc form can reproduce it; an `sd`-saving one (the Sony SDK and
middleware minority) opens with a `warning` and the `compiler_family` attached.
`verify_tu` and `verify_unit_start` carry the same warning. The evidence behind
the rule, read from the CodeWarrior for PlayStation R4/R5 discs and measured on
Silent Hill 2 and Yakuza, is `kit://docs/codewarrior`.

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

## Second run (2026-10-31): the OV01 lane-2 output over stdio

Driven from a script with the SDK's `Client(StdioServerParameters(...))` (result
fields are snake_case: `resource_templates`, `uri_template`, `structured_content`),
against the lane worktree `~/kosmos/xsg/.work/ov01-l2` at `f2c148c` (branch
`ov01-lane2`, 72 lane functions, merged with Opok's master `9fb2a68`). Discovery
needs the private TU manifest, which Opok's tree does not carry: `config/tu-build.json`
and `config/tu/ov01/` were copied into the worktree as ignored files.

- `verify_unit_start` stock: 611 match, 167 skip, 97 classified (mixed 42,
  size-diff 18, unexplained 15, hazard-nop 13, immediate 5, delay-slot 3,
  reorder 1); 16 of the 72 lane functions differ under the unpatched pair.
- `verify_unit_start` contract (the kit-default fsv3 pair): 694 match, 161 skip,
  20 differ; of the 72 lane functions 66 match, 2 differ by 2-4 words
  (`entryPhase20`, `entryPhase35`, like seven `entryPhase*` already in master:
  the kit's contract cc1 is older than Opok's current pin, whose recipe in
  `config/toolchain-identity.json` now lists fsv1..fsv12), 4 are skipped.
  Opok's whole-file gate on the same tree: OV01.OVL byte-identical.
- The match loop on `tgtUnitAdrGet` (blocked in Opok's link: `pTgtUnit`/`pTgtUnit2`
  sit in an unclaimed BSS gap, so the kit is the only judge it has): init, card,
  disasm, source, edit, write, check, verdict, best, patch, list, the three
  resources and the prompt all answered. Five forms, best `SIZE_DIFF` 14 words
  (96 B vs 92 B): the original hoists `&pTgtUnit` once and takes a `bnel` on
  `mode < 8`; no spelling of the range chain reproduced that yet.

Found on the way (all four fixed the same day; the probe, the memo and the
temp file are in `server.py`, re-tested over stdio against the same tree):


- `~/kosmos` is its own APFS volume and colima does not share it, so docker sees
  an empty `/toolchains`; cc1 fails with `cannot open shared object file` (rc 127)
  and every TU reports BUILD FAILED. `doctor` says the toolchains are fine because
  its check is a host-side `is_dir()`. Point `DECOMP_TOOLCHAINS` at a copy under
  `/Users/jessi/Desktop`. Fixed: `doctor` now runs `docker_sees()` (the pinned cc1 must
  be listable through the mount, `visible_in_docker`), and `verify_tu`, `verify_unit_start`
  and `match_init` refuse a contract run up front with the same reason (`contract_blocker()`).
- `match_write_function` on an existing definition re-inserts `declarations`
  every call: after three writes the patch carried three copies of the externs
  and the prototype (legal C, so the verdict is unaffected, but the patch is not).
  Fixed: the block of the previous write (declarations, comment, body) is remembered in
  `SANDBOX/mcp-last-write.json` and replaced whole on the next call.
- `triage_unit` runs `triage.py --summary`, which prints only the histogram, so
  its `rows` are always empty. Fixed: the default table is parsed and the histogram
  built from its class column.
- A tracked file named `-` sat in the kit root: `triage_function` passed `--json -` and
  triage.py takes a file path. Fixed: a temp file, read and deleted; the stray file is removed.
- `doctor` also reports `manifest`: the private `config/tu-build.json` the TU discovery needs.

## Roundtable on the fixes (2026-10-31)

Five seats reviewed the diff (openai gpt-6-sol, google gemini-3.1-pro, deepseek
v4-pro, moonshot kimi-k3, alibaba qwen3.8-max, through Hermes' `nous` provider;
brief = the diff plus the findings above; transcripts under
`~/kosmos/xsg/.work/moa/roundtable-mcp/`). Checked against the code and applied:

- `triage_unit` built its rows from stdout, which `run()` caps at `TEXT_LIMIT`
  (12 KB; the OV01 table is 58 KB), so it silently returned 233 of 496 easy rows
  (openai). Both triage tools now read the `--json` report (`triage_json`).
- The `match_init` guard had to mirror `match_kit.default_compiler()` for an
  omitted compiler (5/5): it now probes exactly when the kit would build with
  the contract pair.
- A memo of the previous block's text breaks on a `match_best` restore and on a
  hand edit inside the block (5/5): rewrites are now bracketed by the shortest
  unique run of the file's own lines beside the stub (`mcp-write-anchors.json`),
  which survive both; losing them falls back to the body with a `warning`.
- A probe timeout (the first run pulls the image) read as "unshared" (4/5): the
  probe reports `kind` (`docker` vs `unshared`), its timeout is 180 s, successes
  are cached per process and failures never are (5/5).
- `DECOMP_IMAGE` is passed to the verify runs, so the probe and the build use one
  image (alibaba, openai); the probe tests the kit-default pair by name, else any
  `<name>/bin/cc1` (openai, alibaba).

Taken as leads, not applied: `--mount` instead of `-v` (the build itself binds
with `-v`; the probe mirrors it), marker comments around the block (they would
land in matched source), dropping `--platform linux/386` (the build uses it).
Claims they asked to verify, verified: Opok's tree carries no
`config/tu-build.json` (git-ignored there); the `-` file was committed in
79ad44e; the unshared volume was observed directly with an alpine `ls` through
the same bind. Tests: `mcp/test_server.py` (13, no docker).

## Lessons applied (2026-10-31, after the PR round)

- **Discovery without the private manifest.** `verify_decomp.discover_tus` now reads
  the upstream's public `config/objects/overlays.compile.json` (units.<unit>.tus:
  ordinal, source, kind, in_scope, flags, compiler id) when `config/tu-build.json`
  is absent, and `DECOMP_MANIFEST_DIR` points at a private manifest kept OUTSIDE the
  tree. Reason: the upstream's own configure reads an in-tree `config/tu-build.json`
  as the provider contract and refuses on a cc1 mismatch, so the kit and that gate
  could never share a worktree. `doctor` reports which manifest discovery uses and
  warns about an in-tree private copy.
- **The upstream pin is tracked.** Every contract report carries `upstream_pin`
  (the cc1 SHA-256 the upstream's toolchain-identity.json pins), the cc1 actually
  used and `contract_current`; the verify prints "contract cc1 matches the upstream
  pin" or tells you to reproduce it. `doctor` shows the same under `contract`. With
  the fsv12 pin reproduced into the kit's toolchains directory, calc verifies 93/93
  under the pair the upstream really uses.
- **Skips are itemized.** `function_skips` names each skipped function and why
  (unresolvable relocation, address outside the image, object/symbol size mismatch).
- **`lint_tu`.** Static maintainer checks on one TU (prototypes after their
  definition, unused defines/prototypes, gotos, raw masks, long lines), optionally
  only for what the file adds over a base copy. The compiler-warning diff against a
  base build stays in the pack's `kit/public-lane/prepush-check.py`.

## Not yet

- The tasks extension once the SDK carries it (replaces the job handles).
- A live-emulator server on the flag-capture rig (snapshot/diff/watch, poke
  tests, savestate bisection) alongside mcp-pine's raw memory tools.
- Streamable HTTP for a shared box, and per-client sandbox namespaces.
