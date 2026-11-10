# The `ps2decomp` MCP server: what it does and why it helps

`mcp/server.py` exposes the kit to any Model Context Protocol client (Claude
Code, Claude Desktop, Codex, Cursor, a script) over stdio. Install and
registration are in [`../mcp/README.md`](../mcp/README.md); this page is the
plain-language explanation.

## The problem it solves

A matching decompilation is slow, repetitive work with one hard rule: a
function is done when the bytes it compiles to are identical to the original's.
Language models are good at the repetitive part (reading disassembly, proposing
C, adjusting a declaration order) and bad at the rule: left to themselves they
will report "matched" on a function that is close, write inline assembly to
force bytes, invent a name the binary never had, or spend thirty attempts on a
function that could never be verified. The first weeks of running models
against the Xenosaga I gate produced every one of those.

The server puts the kit's judgement between the model and the claim. A model
using it can read evidence, write C and ask for a verdict. It cannot compile
with its own flags, cannot see game bytes, cannot edit the budget, cannot
declare a match, and cannot keep going after the tool has said stop.

## What a session looks like

```
doctor                      is everything configured? which compiler family is the original?
identify_fingerprint        which compiler built this image, with the evidence
name_lookup / triage_*      what is this function, how hard is it, what does it call
match_init(function)        one sandbox per function; refused up front if unverifiable
match_card / match_disasm   the evidence (a 2 KB card, named disassembly), never whole files
match_write_function        the model's C goes in here, and only here
match_check(hypothesis)     compile, compare with the original, classify the difference
  ... one changed hypothesis per check ...
match_verdict / match_patch MATCH, or a stop; then a patch against the upstream file
```

Long-running work (`verify_unit_start`, `probe_toolchain_start`) returns a job
handle and is polled with `job_status`. The playbook, the toolchain catalogue,
the corpus and the archive notes are served as resources (`kit://docs/...`), and
the worker brief is a prompt (`match_one_function`).

## What the server enforces

- **The tool decides exactness.** Every verdict is compiled bytes compared with
  the original by `verify_decomp.py`. There is no tool that accepts a claim.
- **One compiler per lane, with its identity recorded.** Stock ee-gcc 2.96 or
  the decomp's own pinned pair; the verdict names which, and the upstream's
  pin is checked.
- **No cheating.** A byte match whose source uses inline asm, `.word`,
  base-plus-offset pointers, raw address casts, placeholder names, `goto`,
  `do {} while (0)` barriers or register-pinning locals is reported as `CHEAT`.
- **Stops that cannot be argued with.** A form ceiling; a no-progress stop after
  K checks without a lower diff or a higher TU match count; `UNVERIFIABLE`
  (a reference the verifier cannot name) before any work and at most twice;
  `REGRESSION` when another function in the unit stops matching.
- **The right compiler for the binary.** `doctor` fingerprints the configured
  original. On a CodeWarrior or ProDG title, `match_init` reads the function's
  prologue first and refuses `sq`-saving functions, because the kit's lanes
  compile with ee-gcc and no form could match; the ee-gcc minority (Sony's SDK
  and middleware) still opens, with a warning attached.
- **No game bytes in tool results.** Tools return analyses, verdicts and diffs.

## How it helps, concretely

- A model gets the same evidence a careful human would gather first (card,
  disassembly with names, sibling prototypes) in a few kilobytes, instead of
  reading source files whole.
- Every attempt is recorded (`forms/NN.c`, `forms/NN.diff`, `budget.json`), so
  a reviewer sees what closed a function and what was tried; the form history
  is the receipt.
- Verdicts are comparable across people, models and days, because the compile
  command is the same for all of them.
- The whole-unit verify and the toolchain probe run as background jobs, so a
  client can ask "what changed" without blocking.
- The same server works for an overlay unit (the resident image's symbols for
  an overlay's external calls) and, through the family check, says honestly
  when a binary is outside what the kit can judge.

## Pointing it at another game

The server is configured by environment only. For the Xenosaga I upstream that is
`DECOMP_UPSTREAM` and friends; for any other tree, `DECOMP_TARGET` names a
`decomp-target/1` manifest (`TOOLS.md` "Targets"), and the server takes the
originals, the unit's binary, the symbol table and the contract toolchains from it.
`DECOMP_SYMBOLS` overrides the symbol table either way. Measured on Xenosaga II
(2026-11-05, in-process with the SDK `Client`): `doctor` reported the target (3 units,
28 TUs, 3 contracts); `name_lookup` and `triage_function` answered from its table;
`match_init` opened a sandbox for `xglFlagsGet` (TU `main/xgl_flags`, baseline 4);
`match_check` judged the unchanged Xenosaga I source `MATCH` under the contract lane;
`verify_tu` and `match_list` answered.

## What it is not

It is not a decompiler and it does not write C. It does not run the game. It
has no CodeWarrior or ProDG lane yet: CodeWarrior's `mwccps2` now compiles on
this machine through `tools/cw_run.py`, and turning that into a judged lane
(compile, byte-compare, the same stops) is the next step.
