<!--
Game text for the Xenosaga agent set (Episodes I, II, III). tools/install_agents.py reads the
"## key" sections: "setup" fills {{setup}} in every template; a section named after a template
or a role id fills {{game}} for that agent; "<key> xenosaga-N" sections ("all xenosaga-2",
"matcher xenosaga-2", ...) become that agent's episode notes, appended to {{game}} under one
heading per episode ("all" applies to every agent). Text before the first section is ignored.
Sources: KOS-MOS pack docs/ov01-lane-lessons.md (sections 1-3) and docs/xs2-lane-lessons.md
(sections 2-4), condensed; the kit's docs/games/xenosaga-*.md, docs/MCP.md and mcp/README.md.
-->

## setup

`KIT` is the decomp-compat-kit checkout: `$DECOMP_KIT` when set, else `kit_root` in `~/.claude/decomp-compat-kit.json` (the installer writes it there, outside the project, because it is a path on this machine). Read `game` in `.kit-flavour.json` next to this agent file first: `xenosaga-1`, `xenosaga-2` or `xenosaga-3`; when it is null or the file is missing, the `doctor` tool's binary decides (`SLUS_204.69` = I, `SLUS_208.92` or disc 2's identical `SLUS_211.33` = II, `SLUS_213.89` = III). Follow only your episode's notes at the end. With the episode's ps2decomp MCP server registered (named in the notes; `KIT/mcp/README.md`) use its tools; otherwise run the CLI as `KIT/.venv/bin/python KIT/tools/<tool>.py` (plain `python3` without a venv; `match_kit.py init` needs rabbitizer) with the same environment the registration sets. Nothing is pushed, committed or opened as a pull request without the user's word.

## all xenosaga-1

Episode I, `SLUS_204.69`, unstripped, with ELF overlays (`OV01.OVL`, `OV10.OVL`, `OV11.OVL`, `OV12.OVL`, ...) and the IOP modules `SSD.IRX`/`RSSD.IRX`. Real names for main and the overlays come from the kit's `symbols/symbols.csv` (it has no IOP rows: the IOP modules carry their own ELF symbol tables). MCP: `ps2decomp` for main; `ps2decomp-ov01` (`DECOMP_UNIT=ov01`, `DECOMP_BINARY=OV01.OVL`) for OV01, where TU ids are `ov01/tuNNN`. Contract compiler: the upstream's pinned patched cc1 (`config/toolchain-identity.json`); `doctor` reports whether the kit's contract pair is current. Most of main is `-G8`; some TUs and the overlays are `-G0`. Upstream: OpokXeno/xsg-i-decomp; work goes through the user's fork and pull requests, never a push to the maintainer's repository, and the maintainer reads the whole merged file, not the diff. Page: `KIT/docs/games/xenosaga-1.md`.

## all xenosaga-2

Episode II, `SLUS_208.92` (disc 2's `SLUS_211.33` is byte-identical), `OV01.OVL`, `OV02.OVL`, all stripped. No symbol table: the `func_`, `enepc_`, `objx_`, `runtime_` and `D_` names in the upstream are the maintainer's estimates; keep them and never rename one. MCP: `ps2decomp-xs2`, served through a target manifest and symbol CSV that `KIT/tools/upstream_target.py` writes from the checkout (`DECOMP_TARGET`, `DECOMP_SYMBOLS`); both are local files, never committed, copied or quoted as a name source. Contract: Episode I's compiler (patched ee-gcc 2.96, cc1 with the fsv patches up to fsv12, the swap-mask assembler with `-g`). The kit's default pair lacks `-g` and leaves the inline-asm `MMath*` functions one word short, so confirm that line 1 of a check names the `-g` contract (`ee-tu-c296-G8-g` on the kit page) before trusting a REORDER or HAZARD-NOP. Some TUs are `-O0`. Cards show no matched siblings here: grep the tree's `src/` and `include/` for a callee or `D_` symbol and reuse the declaration other files use. Upstream: OpokXeno/xsg-ii-decomp; work goes through the user's fork and pull requests, never a push to the maintainer's repository, and the maintainer reads the whole merged file, not the diff. Page: `KIT/docs/games/xenosaga-2.md`.

## all xenosaga-3

Episode III, `SLUS_213.89` and overlays at `0xa7f000`, stripped. The kit docs name no public upstream decomp, so the gate, branch and contribution rules are those of the Episode III project the user names (ask when the launch prompt does not say). The kit's part: names transferred from Episode I (`transfer_symbols.py --donor SLUS_204.69 --donor-symbols KIT/symbols/symbols.csv --acceptor SLUS_213.89 --mask loose`), triage, and verify/match through that project's own target manifest. OV04 was built `-O2 -G0 -fno-strict-aliasing`. Page: `KIT/docs/games/xenosaga-3.md`.

## orchestrator

Lessons from the Xenosaga lanes: gate on the upstream's own whole-file build only, with one gate and one landing at a time (build steps time out under load and give false rejects). Before planning and again before any push, fetch and read what the upstream changed since your base (`git log BASE..origin/<main branch>`, `git diff --stat BASE..origin/<main branch> -- tools config include src`) and record the commit every claim about the upstream was checked at. Diff a sandbox against its merge-base, never the moving branch tip. Settle reviewer drift with a two-family panel. Park byte-exact forms that need byte-offset arithmetic as leads for the maintainer. Resume a worker that stopped with budget left and a changed hypothesis instead of relaunching; record last-tier conclusions as decisions for the maintainer.

Merging: workers of different sandboxes may share one TU file when their function sets are disjoint and each puts its declarations directly above its own functions; refresh a cloned tree's index (`git update-index -q --refresh`) before `git apply --3way`; after a merge look for a prototype that now sits below the new definition. A worker that dies mid-attempt leaves exact and in-progress bodies mixed: check each converted function, splice the non-matching ones back to their `INCLUDE_ASM` line, drop declarations only they used, gate, land.

## orchestrator xenosaga-1

The gate: `./configure.py`, then `ninja gate` (whole-file SHA-256 of all six targets), read from `build/<unit>/status.json` (`"result": "pass"`, `"identical": true`) written during that run. The maintainer's accepted pull requests are small (one commit, one to three functions, one file); a larger one must justify itself. Overlay waves need the `ps2decomp-ov01` registration. The IOP sound driver goes to `{{role:iop-matcher}}`, never to the EE tiers.

## orchestrator xenosaga-2

The gate: `./configure.py`, then `ninja gate`, covering `SLUS_208.92`, `OV01.OVL` and `OV02.OVL`. The maintainer's accepted pull requests are small (one commit, one to three functions, one file); a larger one must justify itself. A TU with no source file yet (scaffold-only, such as `tu068`) needs an entry in the maintainer's import registry; creating `src/main/tuNNN.c` alone changes nothing, so leave it to him. The tree's `tools/tu_edit.py` refuses every write outside the maintainer's production queue: revert a body by splicing `tu_edit.py extract` output back to its `INCLUDE_ASM` line by hand. Compiler-emitted data (float literals, jump tables, string literals, initialised local arrays) must be carved: `tools/tu/data_carve.py derive --write` on the TU object, reconfigure, gate (minutes, holding the gate; batch functions); a carve is refused while still-asm functions reference the same strings. Regenerate the kit's target manifest and CSV with `upstream_target.py` after every pull.

## orchestrator xenosaga-3

Naming first (transfer from Episode I, triage), then matching through the Episode III project's target manifest and its own gate; take the gate command and contribution rules from that project's README, never from the Episode I or II notes.

## matcher

Lessons from the Xenosaga lanes: keep only the declarations the exact function reads; every lead for an unmatched function goes in your report, never in the source; a changed published declaration needs its receipt (the first differing instruction under the old spelling) in your form history and a one-line comment; byte-offset arithmetic, integer-cast bases and `void **` stride cursors are not landable even when byte-exact, so a typed form is the hypothesis to keep trying; a fixed hardware address (the EE scratchpad, an IPU buffer base) is written with the typed macro the tree already defines for it, never a new raw cast, and when the tree has none, report it as a lead instead of inventing one; a callee-saved register swap that survives every statement order points at a type or declaration difference, not at order; a signed range check is two nested ifs; an equality-chain switch takes an unsigned selector; a one-store sign flip is a ternary; a packed colour is a union; declare locals in the original's stack-slot order.

## matcher xenosaga-1

`Java_xeno_*` natives: the `.java` caller the launch prompt names (if any) shows what each argument means. Overlay functions need the `ps2decomp-ov01` server (or `DECOMP_UNIT=ov01 DECOMP_BINARY=OV01.OVL` for the CLI); the default server answers `unknown symbol` for an OV01 TU.

## matcher xenosaga-2

Codegen idioms proven on Episode II under the maintainer's gate (the fsv12 cc1 with the `-g` swap assembler); under any other compiler named on line 1 of a check they are leads, the schedule-sensitive ones (branch-likely slot stealing, movz/movn) most of all. A loop exit written `xglSleep(); return;` or `if (!pressed) { xglSleep(); continue; }` instead of `break` keeps the exit test from rotating to the bottom; a float zero from `mtc1 $0` is a chained assignment (`v.x = v.y = v.z = 0.0f`, stored z, y, x) or a loop-local `float zero = 0.0f`; repeated `daddu` copies of a record address go away with index-form access (`records[index].field`) and `float coords[4]` array members; argument load order is argument order, so read a field into a named local before an intervening store; `if (c) line = next; return line;` gives movz, `if (c == 0) return line; return next;` movn; a copy loop whose test stays at the top is `for (;;) { if (a && b) { ... } else break; }`; a digit test written as a range loads the byte once; a `switch` on the kind instead of an if-chain stops branch-likely delay-slot stealing; a callee declared `int` instead of `void` gives `$3` temporaries; one-member unions keep store/load order; `printf("%f", f)` is plain C (the compiler calls the libgcc conversion). Repository rules: no `#pragma` and no `__attribute__` (the kit lint does not flag them; reviewers reject them); a form that makes the compiler emit data (float literal, jump table, string, initialised local array) needs a carve the orchestrator runs: say so in your report; a string the original references by its own symbol is `extern const char D_x[];` (no carve); an assert ending in a bare `break 0` is `DEBUG_BREAK();` from `debug_break.h` (class ee-assert-break, counted exact_c_with_asm: name it in your report), and `__builtin_trap` does not exist in this cc1. An elevator assert is a bare `if`, not `do { } while (0)`; that was proved for the elevator asserts only, so for other asserts it is a lead.

## reviewer

Judge inline asm only against the file's own published idiom (the ee-vu-cop2 class is admitted where the file already uses it). A view struct on a shared global is acceptable only with a comment naming the unmodelled shared field; the shared-header fix is a lead for the integrator. A verdict that contradicts another reviewer's on the same evidence goes to a two-family panel, not to a third single opinion.

## reviewer xenosaga-2

The estimated names stay: a rename request is not a rework item. In the Episode II lane 8 of 20 reviewed commits came back `rework` (5 of 21 in the first batch); most blockers were refuted by tree precedent, proven forced, or came from stale evidence, and one was valid (a raw fixed address replaced by the tree's named buffer-base macro). The reviewer sees the patch, not the tree, but blockers are not usually wrong: check each one against the tree's precedent (per-TU render models, typed fixed-address macros, the declarations the file already has) and against the gate and compiler (a removed extern whose name is not in the tree's `config/symbols`; an unprototyped callback that avoids an incompatible-pointer warning). A claim the tree or the gate already answers is not a blocker; a claim neither answers stays one. `DEBUG_BREAK()` is the admitted assert trap; `#pragma` and `__attribute__` are rejected.

## integrator

After each landing check the merged file the way a maintainer reads it, whole: no duplicate prototype or extern, no declaration that disagrees with a definition, no comment that still calls a now-matched function "still asm" or "proposed", no declaration left for a reverted function.

## integrator xenosaga-1

Episode I: in the integration tree `./configure.py`, then `ninja gate`; all six targets must pass. Read `build/<unit>/status.json` (`"result": "pass"`, `"identical": true`) and its modification time, never a status file from an earlier run. The branch lives on the user's fork of OpokXeno/xsg-i-decomp.

## integrator xenosaga-2

Episode II: in the integration tree `./configure.py`, then `ninja gate`; `SLUS_208.92`, `OV01.OVL` and `OV02.OVL` must come out identical. A landing whose C makes the compiler emit data includes the carve (`tools/tu/data_carve.py derive --write`, reconfigure, gate) in the same landing. The kit's generated symbol CSV and target manifest never enter the tree. The branch lives on the user's fork of OpokXeno/xsg-ii-decomp.

## integrator xenosaga-3

Episode III: GATE is the Episode III project's own whole-file command named in your prompt; if the prompt names none, stop and ask.

## verifier

Episode I is the reference: the upstream's C under the stock compiler was byte-identical for 817 of 857 functions in the first run and 2,037 of 2,459 for the whole of main, nearly every other one in the documented classes. A function that differs under stock but passes the maintainer's gate is expected; only unexplained and size-diff ones are leads.

## verifier xenosaga-2

Episode II verifies through the target manifest: `verify_decomp.py --target $DECOMP_TARGET --unit main --compiler stock` (or the `ps2decomp-xs2` server). Two functions are unverifiable (`.rodata HI16 without paired LO16`, `KIT/mcp/README.md`): not findings.

## enricher

Episode I is the donor for the series: its unstripped symbols name 428 Episode III functions through `transfer_symbols.py`, and its C reproduces 136 Episode II functions byte for byte (`port_check.py`, `KIT/docs/games/xenosaga-2.md`). A `Java_xeno_*` native bridges the game's Java layer: when the launch prompt names a directory of decompiled Java classes, open at most 3 callers, read only the lines around the call, and write what each argument evidently is (for example "arg1 = frame count: callers pass 30/60 after fade") with file:line.

## enricher xenosaga-2

The estimated names stay: an Episode I or III name for a twin goes into HINTS as a lead with its source, never as a rename.

## enricher xenosaga-3

Episode III twins: transfer from Episode I with `--mask loose` (428 of 428 found); names from a separate Episode III project are leads from another binary.

## iop-matcher

Episode I's IOP sound driver: `IOP/SSD.IRX` and `IOP/RSSD.IRX` (ELF type 0xff80, R3000 code, 442 FUNC symbols in SSD, unstripped). Names come from the module's own ELF symbol table (`dump_symbols.py --recursive`, `KIT/docs/TOOLS.md`) or from the upstream tree; the kit's `symbols/symbols.csv` has no IOP rows. The upstream recovers the driver through its per-function IOP plan, not TU mode; its IOP comparison at the original addresses is your verdict. Documentation of the disc-side sound formats the driver consumes, when the launch prompt names it, is the source for structure and field names.
