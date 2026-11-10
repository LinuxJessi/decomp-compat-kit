# openai/gpt-6-astra-pro

1. **The pointer-masking fix still permits partial, unproved content matches.** `_relocated_pointers()` returns `None` for unresolved targets **and unsupported relocation types**; `_anchored()` masks those words. The shown rejection condition rejects an entirely masked span, not an object containing both checked bytes and unchecked pointers. A matching tag plus a wrong unresolved pointer can therefore pass the apparent content proof. The excerpt ends before the final acceptance code, so that code also needs inspection.
   **Evidence:** Test an anonymous `{tag, pointer}` object with identical tags and different unresolved pointers, including pointers to anonymous `.rodata` strings. It must be unverifiable or different—not MATCH. Every relocated field in a claimed content proof must be resolved, recursively proved, or explicitly make the proof incomplete.

2. **Reference-derived spans are not established object boundaries.** `_object_span()` splits at references, including interior pointers; those are not necessarily separate objects. `_section_refs()` also omits additional shared LO16 references. Separately, `_rodata_table_va()` accepts a contiguous prefix of suitable relocations without proving the table’s full extent or that every relocation within it is supported. These mechanisms can prove less data than the code uses.
   **Evidence:** Change a structure’s tail beyond an interior reference, and change a jump table’s final entry to a different relocation kind. If the accessing function still matches, the proof is incomplete. Require established extents or clearly restrict the claimed guarantee to the checked spans.

3. **Register equality is not a safe HI16/LO16 pairing rule; the fallback makes it worse.** `_pair_by_offset()` and `_section_lo_shared()` do not track copies, clobbers, branches, delay slots, or reaching definitions. For example:
   ```asm
   lui   t0,%hi(A)
   move  t1,t0
   lui   t0,%hi(B)
   addiu t1,t1,%lo(A)
   addiu t0,t0,%lo(B)
   ```
   For section-symbol relocations, A and B can share the same relocation symbol. The first HI can then select B’s LO because its base register matches. `(same or cands)` also silently guesses when no register matches. Content anchoring does not independently validate that guessed relationship.
   **Evidence:** Add this fixture with distinct section addends, plus clobber, join, loop, and delay-slot variants. Compare every relocated word with the actual linker’s output. Ambiguous pairing must fail closed rather than choose the nearest candidate.

4. **The likely “HI16 without later LO16” pattern is a backward control-flow edge with a scheduled/rematerialized `lui`, often in its delay slot.** A shared consumer can appear earlier in address order:
   ```asm
   .Luse:
       addiu a0,v0,%lo(.LC0)
       # shared continuation ...
       # elsewhere in the same function:
   .Lother:
       b     .Luse
       lui   v0,%hi(.LC0)       # executes before .Luse
   ```
   Another HI may reach the same LO from another predecessor. Restricting candidates to `off < noff` rejects this valid layout. Copies and transformations of the base register can additionally defeat register matching.

   **Evidence:** The exact explanation for the two functions is **not established by the supplied material**. Obtain their raw relocations, disassembly, function bounds, and predecessor blocks. First establish how the retail linker pairs their REL records; separately validate the instruction relationship used for anchoring. Do not fix this by indiscriminately allowing earlier LO16s or crossing function boundaries.

5. **ABI relocation pairing and instruction dataflow are being conflated.** The statement that the assembler’s relocation order cannot be relied upon requires a reproducer. A relocation’s position relative to another *function’s* records does not itself establish incorrect ordering of its relevant HI/LO group. Instruction dataflow is useful for proving an address reference, but is not automatically a substitute for linker REL-addend rules. Multiple HIs claiming one LO also overwrite `_pending_lo[noff]` without a compatibility check.
   **Evidence:** Preserve actual `ee-as -g` objects exhibiting the alleged disorder and link them with the original linker. Compare reconstructed addends and relocated bytes, including shared LO16s and low-half carry boundaries. Reject incompatible claims on one pending LO.

6. **The ordinary named-symbol `R_MIPS_32` formula is sound: `(S + A) mod 2³²`; do not subtract `st_value` from A.** A section-symbol relocation normally stores the section-relative target offset in A because the section symbol’s value is zero. The unresolved issue is proving the target section’s retail mapping—not changing that addend convention.
   **Evidence:** Link fixtures for `&named`, `&named + offset`, a negative addend, `.data + offset`, and an interior `.rodata` string pointer. Compare object fields, symbol values, and linked words. Anonymous string pointers currently remain unchecked under finding 1; named-only resolution is insufficient.

7. **`_local_symbol_va()` is neither reliably “last covering symbol” nor a proof of ownership.** It returns the first covering symbol. For size-zero symbols, extending ownership to the next symbol or section end can attribute anonymous trailing data to an unrelated named object and bypass content anchoring. Also, `sorted((value, dict), ...)` raises `TypeError` when two eligible symbols share a value.
   **Evidence:** Test overlapping symbols, aliases at the same offset, and a size-zero named label followed by anonymous data. Sort with an explicit key; select ownership deliberately; do not treat a guessed extent as established ownership.

8. **`@cflags` filtering closes the shown annotation injection path, but `split_cflags()` has a validation bypass.** Tokens beginning with `-G`, and the operand consumed after bare `-G`, bypass its validation. If manifest or other flag lists reach this function directly, values such as `-G8;...` can still reach a shell. The annotation parser protects its own caller, not every caller.
   **Evidence:** Exercise `split_cflags()` directly and through manifest flags with shell metacharacters in both forms. Validate every token before dispatch and require a numeric `-G` operand; preferably pass compiler arguments without shell interpolation.

9. **Unknown relocations no longer produce `identical`, but can still become `immediate`.** An unsupported relocation appends a difference even when `a == b`; the subsequent opcode test can classify that equal-word difference as immediate. Any harvesting mode accepting immediate-only differences may therefore consume an explicitly unjudged relocation. Both comparison and pointer maps also silently collapse multiple records at one offset.
   **Evidence:** Test an unknown relocation on an otherwise identical `addiu` under every harvest acceptance mode. Require an explicit unsupported/unverifiable result. Test duplicate relocation offsets and reject unsupported combinations rather than taking the last record.

10. **`upstream_target.py` can silently replace a contract already assigned to earlier TUs.** `contract()` uses a label based on compiler/assembler identifiers, not options. Two TUs with the same identifiers but different assembler options create different contracts under the same key; the later assignment overwrites the earlier one. Deduplication also omits `cc1_flags`.
    **Evidence:** Generate two TUs differing only in `-g`, and two identity references sharing tool paths but having different driver flags. Check each TU’s final effective command. Key contracts by their complete effective configuration and generate collision-free names.

11. **Symbol conflicts are silently resolved by input order.** `add()` discards later `(binary, name)` records without checking address, size, or type. `labels[u][va] = name` discards aliases. A stale `.txt` definition can override an external definition or the `_gp` recovered from `.reginfo`.
    **Evidence:** Supply the same name at two addresses, two names at one address, and an incorrect existing `_gp`. Conflicting values must cause an error with both provenances; valid aliases must survive.

12. **Several paths silently omit functions or whole TU inventories.**
    - `load()` turns malformed JSON and unreadable files into absence.
    - A missing `<unit>.txt` executes `continue`, skipping even the estimated-function fallback.
    - If `own[u]` is nonempty, a newly listed function absent from it is ignored despite having a size.
    - Unexpected TU schemas/fields are skipped; absent `in_scope` becomes false.
    - Unknown overlay units are skipped, and duplicate TU IDs are not checked here.

    **Evidence:** Corrupt/remove each input separately and compare an explicit source-inventory count against emitted, excluded, and rejected records. Required-input failure and unexplained omission must produce a nonzero exit. The reported 353 TUs does not establish completeness without that reconciliation.

13. **External-symbol ownership can be wrong, especially for overlays.** `PROVIDE` accepts only `\w+` in its ownership tag: a tag such as `overlay-1` is not captured. It then falls back to resident ownership. Explicit tags are not checked against the corresponding image, and segment membership cannot uniquely identify ownership where overlay address ranges overlap. Treating the first unit as resident also silently assumes that `main` exists or sorting selects the right image.
    **Evidence:** Test `/* overlay-1 */`, overlapping load ranges, an invalid tagged address, and a missing `main`. Use explicit validated ownership and reject ambiguity. Missing image files should not silently turn data labels into omissions.

14. **Tool/flag translation is lossy and insufficiently validated.** `driver_flags()` can choose a contract for the wrong assembler and drops all flags outside `-f*`/`-m*`. Assembler-option filtering removes `-G` but leaves its separate operand; removes `-I` but leaves a non-`.` operand. `.split()` is not an argv parser. Paths and options taken from upstream also need validation if subsequently interpolated into shell commands.
    **Evidence:** Snapshot and compare upstream versus generated effective commands for every unique contract. Include `-G 8`, `-I include-dir`, quoted arguments, and a missing exact compiler/assembler pairing. Unsupported translation should fail, not approximate silently.

15. **The remaining shown fixes are sound within their stated paths:** forced low-field inequality, target-mode GP range rejection, branch/jump exclusion from “immediate,” and TU-qualified ledger membership address the cited defects. **Evidence still required:** adversarial regression fixtures and confirmation that unresolved/placeholder states cannot later be promoted to MATCH. The unchanged totals and passing positive corpus do not establish those negative guarantees.
