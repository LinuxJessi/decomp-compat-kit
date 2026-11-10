# openai/gpt-6-astra-pro

Review is based on the supplied code; the reported tests do not exercise the counterexamples below.

1. **Blocker — equal data bytes are treated as proof of object identity.**  
   `tools/verify_decomp.py:Object._anchored`, `_object_span`  
   An unanchored reference takes its address from the retail instruction. Equal initial bytes do not identify writable objects, and separate references to the same object can resolve to different retail addresses. Reference offsets also split objects into potentially tiny comparison spans.  
   **Test:** Create two distinct initialized globals with identical contents. Make retail reference both while the candidate references just one twice. This must not earn Tier A.

2. **Blocker — data relocations are masked without proving their targets.**  
   `tools/verify_decomp.py:Object._anchored`, `_relocated_words`  
   Every relocated data word is zeroed in both buffers, regardless of relocation type or target. A pointer-only object therefore passes with no meaningful bytes compared. Function-pointer tables with entirely wrong callees can be accepted.  
   **Test:** Change a referenced table from `{correct_callback}` to `{wrong_callback}`, preserving its size. Require a mismatch or unresolved result, not Tier A.

3. **Blocker — HI16/LO16 pairing is an instruction-proximity heuristic.**  
   `tools/verify_decomp.py:Object._pair_by_offset`, `_section_lo_shared`; `tools/harvest_relocs.py:harvest_function`  
   First following LO16 and nearest preceding HI16 are not generally governing pairs. Interleaved address calculations against one section symbol can use different registers and high addends. The verifier and harvester also apply different pairing rules.  
   **Test:** Assemble interleaved `HI(A), HI(B), LO(A), LO(B)` against the same section, using different registers and different high halves. Compare relocation and harvested addresses against an actual linker, including carry-boundary cases.

4. **Blocker — GP-relative relocation silently truncates an out-of-range address.**  
   `tools/verify_decomp.py:Object._relocate`  
   The new path writes `(va - gp) & 0xffff` without checking signed-16-bit representability. A symbol displaced by `0x10000` can produce identical instruction bytes even though the instruction cannot address that symbol.  
   **Test:** Move an authoritative GP-relative symbol address by `0x10000`; require relocation overflow, not MATCH.

5. **High — address-spelled names become trusted without independent evidence.**  
   `tools/verify_decomp.py:OverlaySymbols.value`, `known`  
   Any base lookup raising `KeyError` falls back to the numeric suffix; `known()` then advertises that name as known. This can bypass missing-symbol or foreign-build rejection and silently trust an obsolete donor address. Existing successful base lookups do take precedence.  
   **Test:** Use a `DAT_`/`FUN_` name absent from the selected binary but present in another build. Require explicit address authorization, rather than accepting its spelling as target evidence.

6. **Blocker — TU filename encoding is not injective.**  
   `tools/verify_decomp.py:run_target`; `tools/port_check.py:build`  
   Replacing `/` with `__` makes `main/a/b` and `main/a__b` share preprocessing, flag, assembly and object paths. One TU can overwrite another and its object can then be attributed to both sources.  
   **Test:** Put those two IDs in one unit with different function bodies. Reject the manifest or produce independently named artifacts and correct per-source verdicts.

7. **High — unsupported relocations can hide arbitrary instruction changes.**  
   `tools/harvest_relocs.py:masked_compare`, `harvest`; `tools/port_check.py:classify_port`  
   Unknown relocation types mask the entire word, yet still permit `identical` and harvesting. Reporting the type does not make that evidence valid. Even `R_MIPS_NONE` currently hides a whole word.  
   **Test:** Attach an unsupported relocation or `R_MIPS_NONE` to an instruction and change its opcode in retail. Require refusal, not `identical`.

8. **High — B’s function length is not established.**  
   `tools/port_check.py:load_map`, `classify_port`; `tools/harvest_relocs.py:load_placements`, `harvest`  
   Port checking compares object size with **donor** size, then reads that many B bytes. Harvesting discards supplied placement sizes altogether. Neither establishes that the matching window is the complete B function rather than a prefix or adjacent code.  
   **Test:** Supply a B function whose matching prefix has the donor’s length but whose actual extent is longer. It must not be described as same-length function identity.

9. **High — harvested addresses remain hypotheses, even without conflicts.**  
   `tools/harvest_relocs.py:harvest_function`, `section_or_symbol`, `summarize`, `write_csv`  
   Masked identity does not prove that B references the same symbol or uses the same addend. Subtracting A’s addend can infer a consistently wrong B base. Repeated sites can repeat the same mistake; they are not independent proof.  
   **Test:** Change B from `array + 4` to another element while preserving instruction shape, at several sites. Ensure the resulting CSV is marked inferred and cannot become independent Tier-A evidence through a harvest/reverify round trip.

10. **High — sibling resolution can create non-monotonic “resolved” mappings.**  
    `tools/transfer_symbols.py:resolve_by_order`  
    Anchors are fixed for each pass. Newly resolved rows reserve only their start addresses, not order or extent. Two rows can therefore resolve in reverse order inside the same anchor window; no final outlier pass catches them.  
    **Test:** Anchors: donor `0→100`, `100→300`; intermediate donors `10`, `20` have hits `{180,400}` and `{160,500}` respectively, all size 4. Both currently qualify, producing the reversed pair `180,160`.

11. **High — longest increasing subsequence does not identify true anchors.**  
    `tools/transfer_symbols.py:drop_order_outliers`  
    The implementation arbitrarily selects one maximum chain when several exist. A unique accidental body hit can survive, a correct mapping can be discarded, and the survivor can seed further inferred mappings. Link-order preservation itself is only an assumption.  
    **Test:** Construct two equally long monotonic chains, one containing a deliberately false hit. Require ambiguity rather than promoting whichever chain the algorithm happens to retain.

12. **Blocker — ledger reproof loses source/TU identity.**  
    `tools/ledger.py:cmd_all`, `sibling_verdicts`  
    `cmd_all` pools matches across TUs using only `(name, address, size)`. A row can pass because another selected TU emits that function, even when its recorded source no longer does. Sibling results collapse further to function name alone.  
    **Test:** Put duplicate names in two TUs, remove the function from one recorded source, and recheck both rows. The removed source’s row must fail.

13. **High — promotion accepts unrelated or stale reports as current proof.**  
    `tools/ledger.py:cmd_promote`, `tier_a_rows`; `tools/verify_decomp.py:run_target`  
    A different target produces only a warning. Reports lack source, binary, symbol-table and toolchain content identities; promotion stamps today’s date without rechecking them. Unknown TU IDs even fall back to being used as source paths.  
    **Test:** Generate a report, change the source or binary, then promote it; also promote another target’s report. Both must be rejected or explicitly recorded as unverified historical imports.

14. **High — sandbox copies can still operate on original files.**  
    `tools/verify_decomp.py:Target.materialize`; `tools/match_kit.py:cmd_init`  
    Absolute TU paths remain absolute in the copied manifest. Source symlinks are preserved, so edits through sandbox paths can modify external originals. Top-level source files are not copied because `materialize()` copies only directories.  
    **Test:** Exercise an absolute TU path, a symlinked `.c` file pointing outside the tree, and a root-level `.c` file. Verify compilation uses the sandbox and edits never alter the original.

15. **High — manifest/source flags enter generated shell commands.**  
    `tools/verify_decomp.py:run_target`, `build_script`; `tools/port_check.py:build`  
    Flags are joined into shell text, including source-controlled `@cflags`. Contract command paths and flag strings also lack demonstrated shell-safe handling. This requires an explicit trust boundary; Docker’s writable work mount is not protection against forged build artifacts.  
    **Test:** Supply a flag containing `; touch /work/injection-marker #` and a tool path containing spaces. Inspect and execute the generated script in an isolated fixture. The marker must not appear, and paths must remain single arguments.

16. **High — port checking does not reproduce the target compile contract.**  
    `tools/port_check.py:run`, `build`  
    Target verification lets `@cflags` override manifest flags; port checking never reads them. Port checking also accepts contract assembler warnings that `run_target()` treats as build failures. A missing target contract is passed onward as `None` rather than explicitly rejected.  
    **Test:** Use a TU whose annotation changes `-G8` to `-G0`, an assembler-warning fixture, and an undefined contract. Both tools must use the same effective contract and failure policy.

17. **Medium — explicit configuration overrides are silently ignored.**  
    `tools/verify_decomp.py:main`; `tools/symbols_to_splat.py:main`; `mcp/server.py:tree_args`, `verify_tu`, `verify_unit_start`  
    Literal argv membership misses `--binary=…`, `--symbols=…` and `--csv=…`. MCP’s target binary selection bypasses `DECOMP_GAME_DIR`, while `tree_args()` does not carry a `DECOMP_UPSTREAM` override into target verification.  
    **Test:** Compare split and equals-form options using distinct sentinel inputs. Set each advertised environment override separately and inspect the actual files opened.

18. **Medium — ledger writes can truncate or lose updates.**  
    `tools/ledger.py:write_ledger`, `cmd_check`, `cmd_promote`  
    Read/merge/write is unlocked and writes directly to the destination. Concurrent promotions lose rows; interrupted writes truncate the ledger; a destination symlink is followed.  
    **Test:** Synchronize two promoters after their reads, interrupt a write, and use a symlink to a disposable sentinel. Require locking, atomic replacement and an explicit symlink policy.

19. **Medium — preprocessing and compiler identity remain machine-dependent.**  
    `tools/verify_decomp.py:run_target`, `build_script`; `tools/port_check.py:build`  
    Preprocessing uses host `clang` without a pinned version or target, exposing host predefined macros and header search behavior. Tool existence—not measured identity—is checked; the lock file explicitly says its hashes are unenforced.  
    **Test:** Compare preprocessed hashes on x86-64 and ARM hosts using architecture-sensitive conditionals. Replace a compiler payload at the same path. Require deterministic preprocessing and rejection or explicit reporting of identity changes.
