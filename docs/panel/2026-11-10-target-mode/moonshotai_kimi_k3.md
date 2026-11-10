# moonshotai/kimi-k3

Findings, worst-first. Each has the test that confirms or refutes it.

**False-MATCH machinery**

1. **`verify_decomp.py` — `Object._anchored` / `_site_va`: the anchor address is read from the original itself, so the only independent check is "bytes at that address equal the object's span".** Two distinct objects with identical content (duplicated string literals, `""`, `0.0f`, zero padding) bind to the wrong address and still produce MATCH, and nothing in the report, `tier_a`, or the ledger records that the reference was proven by content rather than by symbol. Confirm: extend the `anchor-content` self-test with a second copy of `"hi!\0"` at `0x500040`, site encoding `0x500040` while the source semantically means the first object — `_anchored` returns `0x500040`. Refute by showing a report field that flags content-anchored references (I see none) or a ledger provenance bit.

2. **`verify_decomp.py` — `placeholders` detail never reaches the report, and `is_clean()` behaviour on placeholders is unverified.** The content-mismatch path ("the verdict is a diff, never a match") appends to `self.placeholders`, but `run_target` builds `function_skips` from `o.unresolved` only. If `is_clean()` counts placeholders as unresolved, a wrong data reference surfaces as "skip" (unverifiable), which does not fail `run_failed` and does not appear in the differing count. Test: TU whose `.rodata` object content differs from the original at the encoded site; assert the report shows a diff with the "content of … differs" detail and rc==1. If it shows skip/rc==0, this is a silent wrong-reference hole.

3. **`verify_decomp.py` — `_pair_by_offset` / `_section_lo_shared` pairing is only half-tested and one call site is unchecked.** `_section_lo_shared` calls `_rodata_table_va(sym["shndx"], addend, hoff, off)` with four args; the non-target call site predates this. If the signature wasn't updated, target mode raises TypeError on exactly the shared-`%hi` case it exists for. Also no self-test builds "one HI16 feeding two LO16s". Test: object with `lui %hi(x); lw %lo(x); sw %lo(x)` against a section symbol, run through `_relocate` in anchors mode; assert both LO16s resolve to the same VA.

**Injection / unsafe input**

4. **`verify_decomp.py` — `build_script` + `run_target`; `port_check.py` — `build`: shell injection via `@cflags` and manifest flags.** `parse_annotations` admits `[^*\n]+`, and `extra` / `cc1_flags` are interpolated unquoted into `build.sh` (`stock_cc1_flags`, `contract["cc1_flags"] + " " + extra`). A TU comment `/* @cflags -G0 $(touch /work/pwned) */` executes as root inside the container with `/work` rw; a public toolkit taking contributor TUs should not eval comments. Test: add that annotation, run `verify_decomp.py --target`, check for `/work/pwned`. Fix: `shlex.quote` each flag or pass them via the flags file/env, not the script text.

**Silent wrong-target / wrong-input**

5. **`verify_decomp.py` — `main`: `args.binary_given = "--binary" in sys.argv[1:]` misses `--binary=PATH`.** Same for `--symbols`, and the same bug in `symbols_to_splat.py` (`"--csv" not in sys.argv[1:]`). With the `=` form the explicit option is silently discarded and the target's binary is verified instead — the report then attributes results to the wrong ELF. Test: `verify_decomp.py --target t.json --binary=OTHER.ELF --out r.json`; inspect `r.json["binary"]`. Use `argparse` defaults (`default=None` + sentinel) instead of argv sniffing.

6. **`verify_decomp.py` — `Target.materialize` does not rewrite `tus[].path`.** `include` is made dest-relative but TU paths are copied verbatim; a manifest with absolute TU paths yields a sandbox that still compiles the *original* sources. `match_kit.cmd_init`/`judge` then verify the wrong tree: patches to the sandbox have no effect and "MATCH" reflects the upstream. Also non-directory source parts (`parts[0]` of a root-level TU) are silently skipped, ending in a `sha256_file` crash. Test: manifest with absolute `tus[].path`; materialize; corrupt the sandbox copy; re-verify — if it still matches, the sandbox is inert.

7. **`match_kit.py` — `probe`: two unverified references in the target branch.** (a) `image_segments(image)` is called bare; no `def image_segments` or import appears in the diff for this file — if it isn't defined elsewhere in match_kit, probe raises NameError in target mode (self-tests don't touch this path). (b) `data_ranges` excludes the entry-bearing segment, but PS2 ELFs commonly keep `.rodata` in the same RX segment as text, so string references there still produce "no symbol covers it" and block `init` spuriously. Test: `grep -n "def image_segments" tools/match_kit.py`; then probe a function referencing a string in a 2-segment ELF. Also confirm `symbols_to_splat.py` imports `os` (new code uses `os.path.expandvars`).

**Misclassification**

8. **`port_check.py` — `classify_port`; `harvest_relocs.py` — `immediate_only`: branch offsets count as benign immediates.** The predicate excludes opcodes 0x00, 0x1C, 0x10–0x13 but not 0x01–0x07 (REGIMM, beq/bne/blez/bgtz) or 0x14–0x17 (branch-likelies). A different branch target = different control flow, reported as "immediate" ("A's C with B's layout"), and with `--harvest-accept immediate` such functions feed the harvest; `ledger.py sibling` then reports a real code change as a layout difference. Test: pair `0x10850010` vs `0x10850124` (beq, offset moved) — currently classifies `immediate`; should be `other`.

9. **`port_check.py` — `load_map`/`run`; `ledger.py` — `sibling_verdicts`: static functions keyed by bare name.** Two TUs with a same-named `static` share one map entry; both get placed at one VA, and a coincidental field-masked identity makes harvest read symbol addresses from the wrong site into the CSV. `sibling_verdicts`' `by = {f["func"]: f}` has the same collision. Test: two TUs each defining `static int clamp(void)` with equal size; map one entry; observe both compared at the same VA and what lands in the harvest CSV.

10. **`transfer_symbols.py` — `drop_order_outliers`/`resolve_by_order`: duplicate unique hit sites are a coin flip, and resolved rows become anchors.** Two identical-body donors can each hold the same single acceptor VA as their one hit; the LIS keeps an arbitrary one as anchor and marks the other `<order>` instead of marking both ambiguous. Worse, rows resolved by order re-enter `anchors` next pass, so one false unique (easy with `--anchor-words 2`, which `ledger.remap` hardcodes along with `--min-size 8`) cascades placements at its shift. Test: two donor rows, one acceptor site; check which survives and whether a planted false anchor drags a multi-hit row to a wrong shift.

**Coverage / integrity gaps**

11. **`verify_decomp.py` — `run_target`: skipped TUs don't fail the run.** Preprocess failures go to `report["skipped"]` but never into `totals`; rc can be 0 with a shrunken denominator, and without a baseline nothing notices. Test: break an include path, run `--target`, check rc and that the printed percent is computed over fewer TUs.

12. **`verify_decomp.py` — `Object._gp_value` / GPREL16/LITERAL paths: gp==0 silently relocates against 0.** If the CSV has no `_gp` and the manifest no `gp`, `_gp_value()` returns 0 and `(va - 0) & 0xFFFF` is *written* rather than marked unresolved. Test: Symbols with `gp=None`, no manifest `gp`, TU with an sdata reference; assert the reference lands in `unresolved`, not in the byte stream.

13. **`port_check.py` — `run`/`build`: target mode ignores `@cflags`, crashes on missing toolchains, and silently falls back to stock.** `build()` never calls `parse_annotations`, so a TU whose flags live in `@cflags` is compiled with manifest flags (verify mode honours them; port/sibling mode doesn't — same source, different bytes). If the manifest defines contracts but no `toolchains`, `compiler` becomes `contract` and `docker_build` does `Path(None).resolve()` → TypeError. If a TU names an undefined contract, `contract_of` returns None and the TU builds stock while the report says `compiler: contract`. Tests: (a) `@cflags -G0` TU with manifest `-G8`; (b) contracts without toolchains; (c) TU naming a bogus contract.

14. **`rootfs.lock.json`: integrity lock is documentation.** The doc now says "No tool checks these hashes yet." A swapped/poisoned cc1 in `.work/toolchains` passes doctor and verifies normally. Test: flip one byte in the contract cc1; run doctor and a verify — both green.

15. **`OverlaySymbols` — `AUTO_NAME` fallback: a name is its own proof.** `known("DAT_00456780")` is True with an empty table (the self-test asserts it), which neuters `probe`'s "no symbol covers it" guard and lets a stale or guessed decompiler spelling resolve with no record in the report's `nosym` count. Test: empty symbol table, TU referencing `DAT_…`; check probe reports nothing and the verify report doesn't count the auto-resolved reference anywhere.

**Crashes / CI traps (minor)**

16. **`ledger.py` — `tier_a_rows`: `relative_to(target.root)` crashes for TUs outside the root** (absolute out-of-tree paths are otherwise legal). Test: external absolute TU path; `ledger.py promote` → ValueError traceback.

17. **`port_check.py`/`harvest_relocs.py`: exit 0 unconditionally** — zero functions compared or all `size-diff` still succeed, useless in CI. Test: map with no overlapping names; rc is 0.

18. **`mcp/server.py`: `UPSTREAM = UPSTREAM or TARGET.root` is wrong if `UPSTREAM` is a `Path`** — Paths are always truthy, so target mode never adopts `TARGET.root` (match_kit.py does it correctly with an explicit env check). Confirm UPSTREAM's definition; test with only `DECOMP_TARGET` set and inspect `str(UPSTREAM)` in doctor. Relatedly, a malformed `DECOMP_TARGET` kills the server at import, before doctor can report it.

19. **Small stuff:** `ledger.py cmd_sibling` picks `contract` for any mixed-lane unit (stock-lane rows then fail spuriously); `symbols_to_splat` `UNIT_FOR` keyed by binary basename (two units sharing a basename collide silently); `split_cflags` turns a bare trailing `-G` into `-G8` silently; `warnings` TU name uses `tag.replace("__","/")` which corrupts ids containing `__`; check that `--compiler`'s argparse default isn't `"stock"`, which would make `args.compiler or ...` in `run_target` dead code and disable contract auto-selection.

**Sound (one line):** the forced-`orig^1` diff trick guarantees byte inequality, `masked_compare`'s field masks and the j-vs-jal test are correct, the HI16-first reorder leaves non-target behaviour byte-identical, the ledger merge/roundtrip and LIS core are consistent with their self-tests, and `harvest_function`'s label-base arithmetic can't poison a name with a wrong address.
