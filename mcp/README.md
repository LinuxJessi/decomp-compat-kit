# ps2decomp: the kit as an MCP server

The plain-language explanation of what the server does and why it helps is
[`../docs/MCP.md`](../docs/MCP.md); this file is the install, the tool list and
the run log.

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

Resources: `kit://docs/{playbook|toolchain-differences|corpus|codewarrior|sdk-archive|tools|mcp|history|games|readme}`,
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
  `$HOME/Desktop`. Fixed: `doctor` now runs `docker_sees()` (the pinned cc1 must
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

## Third run (2026-11-03): OV01 on Opok's master d31fadb

`DECOMP_UPSTREAM` is the pack checkout, merged with Opok's master `d31fadb` (lane 2's
72 OV01 functions merged upstream, his lq/sq and VU0 macro headers, fsv12 pin). A second
Claude Code registration `ps2decomp-ov01` (`DECOMP_UNIT=ov01`, `DECOMP_BINARY=OV01.OVL`)
is needed for the overlay: the default server verifies MAIN against `symbols/symbols.csv`
and answers `unknown symbol` for an OV01 TU. With the overlay configuration `verify_tu
ov01/tu017` (m_cam) is 21/21 identical, 9 unverifiable, `contract cc1 matches the
upstream pin`. TU ids are `ov01/tuNNN` (config/tu/ov01/), not source file names.

## Xenosaga II (2026-11-10): Opok's xsg-ii-decomp as a target

OpokXeno/xsg-ii-decomp (SLUS_208.92, OV01.OVL, OV02.OVL) has no ELF symbol table, no
`config/tu-build.json`, a `main.compile.json` whose C TUs say `mode: asm` and carry `-O0`
or `-O2` and an assembler option (`-g`) per TU, and sources written against splat's auto
names (`func_A9CC70`, `D_61B2D0`). It is served through a generated target manifest, not
through the Xenosaga I adapter.

What changed (nothing is keyed on the game; the Xenosaga I path is untouched):

- `tools/upstream_target.py` (new) reads a checkout's `config/symbols/` and
  `config/objects/*.compile.json` + `config/toolchain-identity.json` and writes
  `symbols/xs2-symbols.csv` (9,868 rows: 8,909 functions with the upstream's estimated
  sizes, 950 data labels of size 0, the libgcc helper names, `_gp` from the ELF's
  `.reginfo`) and `symbols/xs2-target.json` (353 C TUs, one contract: fsv12 cc1 + the
  swap-mask assembler with `-g`; `-O0`/`-G` stay per-TU flags). Assembler-local labels the
  upstream lists as data names (`$LC0`) are left out: with them every TU's first `.rodata`
  constant resolved to one address and nine functions differed. Rebuild both after a rename:

  ```sh
  tools/upstream_target.py --upstream ~/kosmos/xsg/.work/xsg2 --game-dir ~/kosmos/xsg/.work/xs2-game \
      --symbols-out symbols/xs2-symbols.csv --target-out symbols/xs2-target.json
  ```
- `verify_decomp.py`: a target manifest may name its source tree with `root` (the manifest
  then lives outside a read-only checkout; a sandbox copy drops it), and splat's auto names
  resolve to the address they spell next to Ghidra's (target mode only).
- `match_kit.py`: the `init` probe no longer refuses a call into another image (an overlay
  function called from the main executable) in target mode; the verifier resolves it.
- `server.py`: `doctor` checks each target contract's tools against the upstream's cc1 pin and
  describes the target manifest as such; `verify_tu` / `verify_unit_start` compare a unit
  other than the configured one with that unit's own original (`ov01/tu000` with OV01.OVL).

Registration (a third server; `DECOMP_TARGET` is what selects the adapter, the manifest
expands `$DECOMP_UPSTREAM`, `$DECOMP_GAME_DIR` and `$DECOMP_TOOLCHAINS`):

```sh
claude mcp add --scope user ps2decomp-xs2 \
  -e "DECOMP_TARGET=$HOME/Desktop/decomp-compat-kit/symbols/xs2-target.json" \
  -e "DECOMP_UPSTREAM=$HOME/kosmos/xsg/.work/xsg2" \
  -e "DECOMP_GAME_DIR=$HOME/kosmos/xsg/.work/xs2-game" \
  -e "DECOMP_BINARY=SLUS_208.92" -e "DECOMP_RESIDENT=SLUS_208.92" \
  -e "DECOMP_SYMBOLS=$HOME/Desktop/decomp-compat-kit/symbols/xs2-symbols.csv" \
  -e "DECOMP_TOOLCHAINS=$HOME/kosmos/xsg/.work/toolchains-gate" \
  -e "EE_ROOTFS=$HOME/xs3-match/rootfs" \
  -e "DECOMP_SANDBOXES=$HOME/.cache/decomp-match-xs2" \
  -- $HOME/Desktop/decomp-compat-kit/.venv/bin/python $HOME/Desktop/decomp-compat-kit/mcp/server.py
```

Verified at upstream `ba20cba`, contract compiler, on a scratch copy and (doctor, `verify_tu`)
on the registered checkout, which stays untouched:

```
main   identical: 1985/1985  (100.0%)   differing: 0   unverifiable: 2   unknown symbol: 0   build failures: 0
ov01   identical: 655/655  (100.0%)   differing: 0   unverifiable: 0   unknown symbol: 0   build failures: 0
ov02   identical: 8/8  (100.0%)   differing: 0   unverifiable: 0   unknown symbol: 0   build failures: 0
```

(235 + 82 + 3 TUs built, 14 of them `-O0`; 30 listed TUs have no C file yet.) Over stdio with
the SDK `Client`: `doctor` `ok: true` (`visible_in_docker: true` for the toolchains under
`~/kosmos`, contract `current: true` against pin `58a63b84ed438510`); `name_lookup` by name
and by address; `match_init enepc_001F6680` with `compiler=contract` (`baseline_matches: 1`,
the TU's one C function); `match_card`, `match_disasm`; `verify_tu main/tu073` `match=1`,
`main/tu054` `match=28`, `ov01/tu000` `match=8`; and on `Resigne` one throwaway form through
`match_write_function` + `match_check`: `FORM 1/30: UNEXPLAINED   TU main/tu073 matches 1
(baseline 1)   compiler=contract`, 14/51 words, three `offset-cast` cheats flagged. Both proof
sandboxes were removed afterwards.

Still open:

- `MemcardTest` (main/tu060) and `gifStateEffectFrame` (main/tu136) are unverifiable:
  `.rodata HI16 without paired LO16` (the offset pairing finds no `%lo` after the `%hi` in
  the same function).
- Function sizes are the upstream's estimates and every data label has size 0, so a card
  names a global only at its exact address, and an access into unnamed `.bss` is refused at
  `init` until the upstream names it.
- A call from the main executable into an overlay is shown by address (`func_A9F0A8`) in
  `match_disasm` and the card, not by the overlay's name: OV01 and OV02 share one load window.
- The card has no matched siblings and no state: this tree has no per-TU ledger.
- The table and the manifest are snapshots: rerun `upstream_target.py` after a pull.

## Not yet

- The tasks extension once the SDK carries it (replaces the job handles).
- A live-emulator server on the flag-capture rig (snapshot/diff/watch, poke
  tests, savestate bisection) alongside mcp-pine's raw memory tools.
- Streamable HTTP for a shared box, and per-client sandbox namespaces.
