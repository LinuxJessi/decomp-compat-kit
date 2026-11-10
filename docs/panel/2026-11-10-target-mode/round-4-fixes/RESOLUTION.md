# Review round: the fixes, and the upstream-target generator (2026-11-10)

The fixes from the first review were sent back, with `tools/upstream_target.py` (written
the same day in a second working session). Gemini, GPT, GLM and Grok answered. By now a
second Xenosaga II target existed to test on: the upstream decomp, 353 TUs.

Result on that tree, contract compiler: main 1985/1985 with 2 functions unverifiable
before this round; **main 1987/1987, overlay 1 655/655, overlay 2 8/8, nothing
unverifiable** after it. The other target's ledger stays 141/141.

## Confirmed and fixed

| Finding (who) | What was wrong | Fix | Test |
|---|---|---|---|
| A pointer to unnamed data was still masked when the object also held plain bytes (Gemini 5, GPT 1, GLM 5, Grok 3-4) | `struct { int id; const char *name; }` was accepted on `id` alone; `name` could lead to any string. | `_pointee_matches`: the original's pointer is followed and the bytes there must be the bytes this object's pointer leads to (to depth 4). Only a pointer with nothing to follow (uninitialised data) stays uncompared. | `anchor-string-pointer` |
| A `%lo` placed before its `%hi` could not be paired (all four; the two unverifiable functions) | ee-gcc fills a backward branch's delay slot with a copy of the `lui` at the branch target, so the copy's `%lo` is earlier in the function. Found by reading `MemcardTest`: `bne` at +0x163c, `lui v0` in its slot, `addiu a2,v0,%lo` at +0x274. | When no `%lo` follows, a `%lo` before it is accepted if exactly one in the function uses the register the `lui` loaded. | the two functions now verify |
| Shared `%lo` offsets were not object boundaries (Gemini 6, GPT 2) | One `%hi` feeding `%lo(sym)` and `%lo(sym+4)` left the second object merged into the first span. | `_section_refs` adds each shared `%lo`'s offset. | 2,650 functions unchanged |
| Contract label reused for a different tool/option set (Gemini 8, GPT 10, GLM 16) | `upstream_target.py` overwrote the earlier contract, silently changing how earlier TUs compile. | A second contract under the same names gets its own key (`name#2`). | generator output unchanged for the current upstream (one contract) |
| `-G` flags skipped the flag check (GPT 8) | `-G8;...` reached the shell line. | `-G` must be `-G<digits>`. | `cflags-refused` |
| A word with an unknown relocation could still class as "immediate" (GPT 9) | | Equal words are never an immediate difference. | |

## Found while testing the fixes

**Trailing alignment padding.** Once `MemcardTest` could be paired it reported two differing
words. The cause was the content check itself: a string followed by an aligned jump table
is padded to the table in the object file (13 zero bytes here) but by only 5 in the linked
game, because padding depends on the address. The span ran to the next referenced object
and so compared padding against the start of the table. The check now lets up to
`alignment - 1` trailing zero bytes of the object differ, and nothing else
(`anchor-padding` covers the accepted case, a real differing byte, and padding longer than
the alignment allows).

## Accepted as limits, not changed

- **Register pairing is not dataflow (all four).** A `lui` result copied to another register
  before its `%lo` falls back to position. A wrong pairing gives a wrong address and so a
  reported difference; it cannot manufacture a match, because the bytes compared are the
  original's. No such case exists in 2,650 verified functions; if one appears it will show
  as an unexplained difference.
- **Spans are split at references, not at true object boundaries (GPT 2).** An interior
  pointer shortens the compared span. Known; the alternative needs object sizes the
  assembler does not record for anonymous data.
- **`_local_symbol_va` and zero-size symbols (GPT 7).** Unchanged from the first release.
- **`upstream_target.py` drops silently in several places (GLM 13, 14, 18; GPT 11-14;
  Grok 5, 6):** a function in `<unit>.txt` missing from the estimates, a label outside every
  image, a TU whose tools are not found, a `PROVIDE` tag that is not a plain word, only the
  resident `_gp`. On the current upstream none of these fires (353 TUs in, 353 out; the
  verify totals above). They should become counted warnings; open, with the generator's
  author.
- **No RELA guard (GLM 6).** ee-as writes REL; a RELA object would be misread. Open.

## Dismissed

- GLM 7 (the all-pointers case "not reviewable"): the brief was cut there; the code refuses
  it, and `anchor-pointers` tests it.
- GPT 5 (the claim that the `-g` assembler lists a HI16 after a later function's LO16 needs
  a reproducer): `pair-by-offset` is that reproducer, taken from a real object.
