# qwen/qwen3.8-max-0902

1. `verify_decomp.py:Object._anchored` — content match can prove the wrong address.  
   It accepts a data reference when the bytes at the encoded/annotated address happen to equal the object’s bytes. No uniqueness check, no symbol-table proof, no check that the original intended that address. Small objects, zero fills, common strings/floats can collide.  
   Test: make a 4-byte `.rodata` object (`00 00 00 00` or a common float) and an original image containing the same bytes at several addresses; force `_site_va`/`@rodata` to one wrong address. Confirm `_anchored` returns that VA. Refute only if `_anchored` is strengthened to require symbol/address uniqueness.

2. `verify_decomp.py:Object._site_va` — original reloc sites are read without validating instruction form.  
   For `hilo`, it reads two original words by offset and combines them; it does not check the first is LUI and the second is the matching immediate instruction. For `gp`, it does not check the opcode is a `$gp` load/store. If the original function is scheduled differently, arbitrary words can supply an address that content-proves by accident.  
   Test: synthetic object with a HI/LO reloc; original bytes at the corresponding offsets are non-LUI/non-ADDIU words whose halves combine to an address containing matching bytes. Confirm verifier can resolve/match. Refute if opcode/register validation is added.

3. `verify_decomp.py:Object._pair_by_offset` / `Object._section_lo_shared` — HI16/LO16 pairing heuristics can choose the wrong pair.  
   `_pair_by_offset` picks the first LO16 after the HI16 in the same function. `_section_lo_shared` picks the nearest previous HI16 before the LO16. With multiple HI/LO pairs, shared `%hi`, or out-of-order reloc entries, this can compute a wrong full address. If that wrong address content-matches, false MATCH.  
   Test: object with one HI and two candidate LOs with different low addends; verify which LO is paired. Test one LO with two preceding HIs against the same section symbol; verify `max(his)` picks the wrong HI. Refute if pairing is made addend/symbol-specific rather than positional.

4. `verify_decomp.py:Object._anchored` — positional `@rodata` mapping is fragile and can bind the wrong rodata object.  
   It maps sorted object starts to the annotation list by index. If annotation order/count does not exactly match the object’s reference starts, the wrong `@rodata` VA is used. Content equality at the wrong VA can then pass.  
   Test: two `.rodata` objects with identical or swapped content; provide `@rodata` addresses in the wrong order; confirm the wrong VA is accepted. Refute if `@rodata` entries are keyed by section offset or content hash.

5. `verify_decomp.py:Object._relocated_words` / `Object._anchored` — data content comparison masks whole words for any relocation.  
   Any reloc offset in the section zeroes 4 bytes in both object and original. For partial relocations (HI/LO, GPREL) or non-pointer relocs in data, non-relocated bits can differ and still be masked.  
   Test: data section word with a HI or LO reloc; change the non-relocated high bits in the original; confirm `_anchored` still matches. Refute if masking becomes reloc-type field-specific.

6. `verify_decomp.py:OverlaySymbols.value` / `target_object` — `@addr` and auto-names can override/fabricate addresses.  
   `@addr` names take precedence over the real symbol table. `FUN_/DAT_/LAB_/PTR_/…Ram` names are also resolved purely from the hex digits in their name. A collision or typo can silently relocate a symbol to a wrong address; content/byte coincidence can then produce MATCH. NOBITS section bases annotated this way are accepted without any byte proof.  
   Test: add `@addr existing_symbol 0xdeadbeef` and confirm `OverlaySymbols.value` returns the annotation, not the CSV address. Test `DAT_00000000`/`FUN_...` absent from the symbol table resolves to the spelled address. Refute if annotations only apply to otherwise-undefined names and auto-names are validated against the binary/symbol table.

7. `verify_decomp.py:Object._off_to_va` — target-mode fallback can map offsets outside the object’s own text.  
   It uses `f.get("obj_size") or f["size"]`. If `obj_size` is missing or zero, the original function size is used, allowing offsets beyond the compiled object to be mapped into the original function. That can resolve relocs that should remain unresolved.  
   Test: create/force a function entry with `obj_size=0` and a reloc offset beyond the object’s actual text but inside the original size; confirm `_off_to_va` returns a VA. Refute if `obj_size` is guaranteed nonzero and authoritative.

8. `verify_decomp.py:Object._relocate` — content-mismatch placeholders may not reliably prevent MATCH.  
   On some content mismatch paths it appends to `self.placeholders` and returns without writing a forced differing word (notably the HI16 path). Whether the function is later rejected depends on `is_clean` treating placeholders as fatal. If not, a mismatch can still classify as match.  
   Test: force a HI16 content mismatch and check whether the function is Tier A / `match`. Refute if `is_clean` is proven to reject all `placeholders` in the function span.

9. `harvest_relocs.py:masked_compare` and `port_check.py:classify_port` — unknown relocation types are masked whole and can still yield `identical`.  
   `FIELD_MASK.get(t, 0)` means any reloc type not explicitly known compares as always equal. The function can be reported `identical` even if that word is completely different. This is a direct false-identical path for port/sibling checks and harvest gating.  
   Test: synthetic object with an unsupported reloc type (e.g. `R_MIPS_32` in text or a fake type) and a differing retail word; confirm `classify_port` returns `identical`. Refute if unknown relocs make the function non-identical.

10. `port_check.py:classify_port` / `harvest_relocs.py:immediate_only` — branch offset changes can be classified as “immediate”.  
   The “immediate” test only excludes a small opcode set. Common branch opcodes (e.g. BEQ/BNE/REGIMM) are not excluded, so differing branch offsets with same high bits are treated as harmless immediate differences. If anyone uses `--harvest-accept immediate`, addresses can be harvested from functions whose control flow differs.  
   Test: change only a branch displacement word; confirm `immediate_only`/`classify_port` says immediate. Refute if branches/jumps are excluded or immediate class is never trusted.

11. `harvest_relocs.py:covering` — zero-size symbols can cover arbitrary later offsets.  
   If a zero-size symbol has no next symbol, its end becomes `val + (1 << 30)`. A later section-relative reference can be attributed to that symbol, producing a false harvested address.  
   Test: object with one zero-size symbol at offset 0 and a section reference at `0x1000`; confirm `covering` returns the zero-size symbol and harvest emits an address. Refute if zero-size symbols are ignored/bounded.

12. `harvest_relocs.py:harvest_function` — LO16 pairing uses nearest previous HI16 and can harvest the wrong symbol/base.  
   Same class of bug as #3: `max(his)` can pick an unrelated HI16 against the same symbol/section. The harvested address is then wrong.  
   Test: two HI16 relocs before one LO16, same symbol index, different addends; confirm the later HI is chosen. Refute if pairing is verified by addend consistency.

13. `transfer_symbols.py:resolve_by_order` / `drop_order_outliers` — sibling order resolution can place wrong rows and discard correct ones.  
   `resolve_by_order` accepts a row when exactly one hit lies between two anchors, even if that hit is spurious. `drop_order_outliers` keeps one LIS and marks all other unique rows ambiguous; with multiple valid LIS it can mark correct rows `<order>`. These mappings feed `ledger.py sibling` and harvest.  
   Test: craft rows where a multi-hit row has one accidental hit inside the anchor window; confirm it is resolved. Craft unique rows with two valid increasing chains; confirm one valid row is marked ambiguous. Refute if order resolution requires additional byte/anchor proof.

14. `ledger.py:cmd_promote` — promotes Tier A rows from unvalidated reports.  
   It reads any JSON report and appends rows. It only notes if `report["target"]` differs; it does not validate binary, symbols, toolchain, or that the report belongs to this target. A forged/stale report can poison the ledger.  
   Test: create a report JSON with fake `tier_a` entries and run `ledger.py promote`; confirm rows are written. Refute if promote validates target identity and proof metadata.

15. `ledger.py:cmd_all` — ledger rows are not bound to the source/TU that proved them.  
   `cmd_all` compiles all TUs implied by the rows, then flattens all Tier A functions into a set of `(name, va, size)`, dropping the TU. If another TU in the same unit emits a matching function with the same name/address, a row whose `source` is wrong can still verify.  
   Test: ledger row says source A, but function is Tier A only in source B; confirm `ledger.py all` passes. Refute if Tier A rows are checked against the row’s source/TU.

16. `ledger.py:tier_a_rows` and `verify_decomp.py:Target.materialize` — absolute/outside TU paths are not safely normalized.  
   `tier_a_rows` does `relative_to(target.root)` and can crash for paths outside the root. `Target.materialize` does not rewrite `tus[*].path`; absolute paths remain absolute, so a sandbox manifest can still point at the original tree.  
   Test: manifest with a TU `path` outside the root or absolute; run `materialize`/`ledger all`; confirm crash or use of original absolute path. Refute if all TU paths are canonicalized and required to stay under the manifest root.

17. `verify_decomp.py:build_script` / `docker_build` / `run_target`; `port_check.py:build` — compiler flags are passed into a shell script without visible quoting.  
   `stock_cc1_flags` and contract `cc1_flags` are raw strings derived from manifest flags or source `@cflags`. If they are interpolated unquoted into `build.sh`, flags can inject shell commands inside the build container.  
   Test: target/source flags containing `;`, `$(...)`, backticks, or spaces; generate `build.sh` and inspect/execute in a disposable container. Refute if all flag strings are shell-quoted or passed via argv only.

18. `verify_decomp.py:Target._abs/materialize`; `ledger.py:write_ledger/remap`; `match_kit.py:cmd_init` — manifest paths are not sandboxed; symlinks are preserved.  
   `_abs` expands `~` and environment variables and accepts `..`/absolute paths. `materialize` uses `copytree(..., symlinks=True)`. `write_ledger` and `remap` write to manifest-controlled paths. A malicious target manifest can read/write outside the intended tree.  
   Test: manifest with `ledger: ../evil`, sibling `map: ../evil.map`, TU/include paths containing `..`, or a source symlink to `/etc/passwd`; run relevant commands. Refute if all outputs/inputs are confined to the target root and symlinks are refused.

19. `mcp/server.py` top-level / `match_kit.py` top-level — target-mode symbol fallback can silently use the wrong symbol table.  
   If a target has no `symbols`, both wrappers keep the kit’s Xenosaga I `symbols.csv` instead of failing. Verification can then run a target against an unrelated symbol table.  
   Test: set `DECOMP_TARGET` to a manifest without `symbols`; inspect `SYMBOLS`/verify argv. Refute if wrappers error when the target lacks a symbol table.

20. `mcp/server.py:_load_target` / top-level — `UPSTREAM = UPSTREAM or TARGET.root` cannot work as intended.  
   `Path` objects are always truthy, so `UPSTREAM or TARGET.root` never replaces `UPSTREAM`. The target root may not be used even when `DECOMP_UPSTREAM` is unset. Also, `_load_target` has no exception handling; an invalid `DECOMP_TARGET` crashes server import.  
   Test: unset `DECOMP_UPSTREAM`, set `DECOMP_TARGET`; assert `UPSTREAM == TARGET.root` (currently likely false). Set `DECOMP_TARGET` to an invalid file and start the MCP server; observe unhandled exception. Refute if explicit `is None`/try-except is added.

21. `verify_decomp.py:run_target`; `port_check.py:build` — host `clang` preprocessing is unpinned and machine-dependent.  
   Target mode preprocesses with `clang` from `PATH`, not the original toolchain and not in the pinned Docker image. Missing clang crashes with `FileNotFoundError`; different clang versions can preprocess differently.  
   Test: run on a machine/container without `clang`; run with two clang versions and compare `.i` output for a tricky header. Refute if preprocessing is pinned/run inside the container or validated.

22. `port_check.py:build/run` — target mode ignores source annotations used by the verifier.  
   `verify_decomp.run_target` honors `@cflags` and `@addr`; `port_check.build` uses only manifest `flags` and does not apply annotations or `OverlaySymbols`. Sibling/port verdicts can be based on different codegen than the Tier A proof.  
   Test: a source with `@cflags -G0` while manifest says `-G8`; compare `verify_decomp` and `port_check` objects/verdicts. Refute if port_check intentionally requires manifest-only flags and documents that sibling checks are weaker.

23. `port_check.py:build/run` — contract selection can call Docker with `compiler="contract"` and `contract=None`.  
   If a TU’s contract is missing, `contract_of` returns `None`, group key becomes `"stock"`, but the global `compiler` can still be `"contract"`. `docker_build` may then run contract-mode with no contract dict.  
   Test: target manifest with `default_contract` undefined or a TU naming a missing contract; run `port_check.py --compiler contract`. Refute if compiler is downgraded per group or validated before build.

24. `rootfs.lock.json` doc — toolchain hashes are not checked by any tool.  
   The diff’s doc string says “No tool checks these hashes yet”. If the stock/contract tools are wrong or modified, verification can still report MATCH under a false toolchain identity.  
   Test: alter a pinned `cc1`/`ee-as` byte and run verify/doctor; observe no hash failure. Refute if hash verification is added.

25. `harvest_relocs.py:ObjFile/Image`; `verify_decomp.py:read_elf` — ELF parsing assumes well-formed files.  
   Multiple `struct.unpack_from` and `bytes.index(b"\0", ...)` calls lack defensive bounds/format checks. Truncated or malicious ELFs can crash tools rather than be rejected.  
   Test: feed truncated ELF/REL files to `harvest_relocs.py`, `port_check.py`, and `verify_decomp.py`. Refute if parsers catch and report malformed input.

Sound in the narrow sense: `ledger.py` TSV round-trip/merge itself appears internally consistent.
