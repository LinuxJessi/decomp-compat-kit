# Review round: target mode, before its first commit (2026-11-10)

Seven models were sent the diff (target mode in `verify_decomp.py`, `match_kit.py`,
`transfer_symbols.py --sibling`, the MCP server, and the new `ledger.py`, `port_check.py`,
`harvest_relocs.py`) and asked for ways the verifier could report a match that is not real.
Five answered in full (Gemini, GPT, Kimi, Grok, Qwen); GLM returned nothing usable and
DeepSeek did not answer. Their answers are beside this file,
unedited. Each objection was checked against the code; a model's say-so decided nothing.

State before and after the fixes below: every tool self-test passes, the MCP tests pass
(24), `ledger.py all` re-proves 141/141 Xenosaga II rows, and Xenosaga I's totals are
unchanged on both lanes (contract 2360/2471, stock 2032/2461 identical, before and after).
A second review of the fixes themselves is in `round-4-fixes/`.

## Confirmed and fixed

| Finding (who) | What was wrong | Fix | Test |
|---|---|---|---|
| Pointers in anchored data were masked, not checked (Gemini 2, GPT 2) | A data object accepted "by content" had every relocated word zeroed on both sides. A table of nothing but pointers compared zero bytes and was accepted at whatever address the original named. | `_relocated_pointers`: a pointer whose target resolves (named symbol, or `.text` offset) must equal the original's word; an object that is only unresolvable pointers is refused as unproven. | `anchor-pointers` |
| HI16/LO16 pairing by position alone (Gemini 5, Grok 3, GPT 3, Kimi 3) | Two `%hi` against one section symbol that both precede their `%lo` (`lui a; lui b; addiu b; addiu a`) paired with the same first `%lo`. The result was a wrong address, so a false difference rather than a false match, but wrong. | Pair by the register the `lui` loaded, in `_pair_by_offset`, `_section_lo_shared` and `harvest_relocs.harvest_function`; position is the fallback. | `pair-by-register` |
| A failed content proof was not forced to differ on the `%hi` (Gemini 4) | The `%lo` was set to 0 and the `%hi` left as assembled; the comment promised "never a match" but relied on no original holding those values. | `_force_diff` writes a field that cannot equal the original's, on both halves. | covered by `anchor-content` path |
| `$gp` offsets not range-checked (GPT 4) | `(va - gp) & 0xffff` let an address 0x10000 away produce the same bytes. | Out-of-range is now unresolved, not written. | by construction |
| `@cflags` and manifest flags reached a shell line unquoted (Gemini 8, Kimi 4, GPT 15) | A comment in a TU could run a command inside the build container (which mounts the work directory). | Flags must match `-name[=value]`; anything else is refused before the script is written. | `cflags-refused` |
| Branch offsets counted as "immediate" differences (Kimi 8) | `port_check` and the harvest's `immediate` class excluded only SPECIAL/COP opcodes, so a different branch target read as a moved struct offset. | Branch and jump opcodes excluded. | `branch-not-immediate` |
| Unknown relocation types masked the whole word and still allowed `identical` (GPT 7) | | A word with a relocation the tool has no field for now counts as a difference. | existing harvest tests |
| A ledger row could be proven by another TU's function of the same name and address (GPT 12, Gemini 11) | `ledger.py all` pooled Tier-A results across TUs. | Rows are checked against their own recorded source's TU. | 141/141 still |
| TU ids were turned into file names by `/` → `__`, not injectively (Gemini 9, GPT 6) | | Ids containing `__` or colliding are refused. | by construction |
| `--binary=PATH` / `--symbols=PATH` / `--csv=PATH` were not recognised as given (Kimi 5) | The target's own file silently won. | Both spellings recognised. | by construction |
| Ledger written in place (GPT 18) | An interrupted write could truncate it. | Written to a temp file and swapped in. | |

## Accepted as limits, documented, not changed

- **Equal bytes are not identity (GPT 1, Kimi 1, Gemini 3).** A reference proven by content
  shows the original holds the same bytes at the address it uses; two different zero-filled
  or equal-valued objects cannot be told apart. Tier A means "these function bytes are
  identical with nothing masked", and that stays true. What the function refers to is as
  strong as the data is distinctive.
- **`FUN_`/`DAT_` names and `@addr` lines are assertions by the TU's author (Grok 1,
  Gemini 1, GPT 5).** They resolve to the address they state. A wrong one yields different
  bytes unless the original holds that very address, in which case the bytes are right and
  only the name is in question. Names are not what the ledger proves.
- **Same-named statics in two TUs** collide in `port_check`'s map and in
  `ledger.py sibling`, which key by name (Kimi 9, Gemini 11). Seen once on Xenosaga II
  (`frame_init`); the report already calls it a false pairing. Open.
- **`Target.materialize` keeps absolute TU paths and follows symlinks (GPT 14, Kimi 6).**
  Manifests in use are relative. Open.
- **Reports are not bound to hashes of source, binary and toolchain (GPT 13).**
  `ledger.py all` recompiles, so the ledger is re-proved rather than trusted; `promote` from
  an old report is the weak path. Open.
- **Preprocessing uses the host's `clang -E` (Gemini 10, GPT 19).** By design since the
  first release (the original cpp is not needed for these sources); documented in
  `verify_decomp.py`.

## Dismissed

- Kimi 3 (`_rodata_table_va` called with the wrong number of arguments) and Kimi 7a
  (`image_segments` undefined in `match_kit.py`): both exist with those signatures.
- Grok 4 and 5: the stock and contract flag paths read one list; `locate` returns the path
  the sandbox was built from.
