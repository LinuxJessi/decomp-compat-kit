# x-ai/grok-4.3

1. **Register-based HI/LO pairing in `_pair_by_offset` and `_section_lo_shared` is incomplete for copied/moved bases or delay-slot reuse.**  
   Evidence: `same = [n for n in cands if (self._word_orig(n)>>21)&31 == rt]` only checks the immediate LO's rs against the original lui rd; no dataflow or move tracking. A `move $tN,$tM; ... lw $tN,%lo` or delay-slot use will fall back to the first candidate or fail, producing either a wrong addend or "HI16 without paired LO16".

2. **_pair_by_offset assumes all LOs appear after their HI in file order inside the same function.**  
   Evidence: `cands = sorted(noff for ... if off < noff < hi_f)`; a %lo emitted before its %hi (rare but legal after scheduling) yields `cands=[]`, triggering unresolved or `_anchored` fallback that can silently accept a content match on the wrong address.

3. **_relocated_pointers does not handle R_MIPS_32 addends for section symbols in REL data sections correctly when the pointer target is a string or unnamed .rodata datum.**  
   Evidence: `if rsym["typ"]==STT_SECTION: expect = _local_symbol_va(...)` (which requires a covering named symbol); otherwise `expect=None` and the word is masked. A pointer to a literal string therefore becomes masked, allowing a different string address to compare equal.

4. **_anchored + _relocated_pointers can report a false MATCH when a masked pointer's expected value is never compared.**  
   Evidence: when `expect is None`, the four bytes are zeroed in both `ours` and `theirs`; if the remainder of the object is identical, the function returns a VA with no "content" reason, and the ledger records a verified match.

5. **upstream_target.py can silently assign a data label to the wrong binary.**  
   Evidence: `owner(u,va)` walks only the PT_LOAD segments of the resident image and the current unit; a label whose VA lies in an overlay that happens to overlap the resident range is attributed to the resident instead of its actual overlay unit.

6. **upstream_target.py drops TUs whose contract cannot be built, with only a stderr warning.**  
   Evidence: `if cname is None: warn(...); continue` before appending to `tus[]`; the resulting manifest omits the TU entirely, so `verify` never sees it and the ledger row for that function is never produced.

7. **The two remaining ".rodata HI16 without paired LO16" cases are unexplained.**  
   Evidence: no code path or comment identifies the ee-gcc 2.96 pattern that emits a lone %hi against a .rodata section symbol (e.g., `lui` for a constant, `la` macro split across functions, or a dead address computation); without the pattern, the pairing heuristic cannot be shown complete.

8. **Sound: ledger.py now ties each row to its own TU via `tu_of_row` and uses atomic write for the ledger file.**  
   Evidence: the diff shows exactly those two changes; no other ledger integrity issues remain.
