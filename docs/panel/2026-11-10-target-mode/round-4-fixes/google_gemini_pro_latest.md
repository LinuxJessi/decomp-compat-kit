# ~google/gemini-pro-latest

### Round-2 Fix Verification
1. **Sound:** `_force_diff` on failed proof, `$gp` range check, `CFLAG_OK` shell-injection guard, branch opcode exclusion in `immediate_only`, unknown-relocation rejection in `masked_compare`, and per-TU row binding in `ledger.py` are sound.

---

### Question 1: Fixes, Pairing, and `_relocated_pointers`

2. **`_pending_lo[noff]` overwrite when multiple `HI16` share one `LO16` (`verify_decomp.py`).**
   When two `HI16` relocations (e.g., on two control-flow paths) feed the same `LO16`, both calls to `_section_hi` resolve `noff` to that same `LO16` and write `self._pending_lo[noff] = va`. If the first `HI16` fails its content proof (`self._pending_lo[noff] = 0`) and the second succeeds (`self._pending_lo[noff] = va`), the `LO16` gets the valid `va` while the first `HI16` gets `_force_diff` (safe). However, if the two `HI16` instructions carry *different* high addends (`w & 0xFFFF`) that happen to converge on the same `%lo` instruction in the binary, the first `va` is silently overwritten by the second.
   *Evidence:* Construct a Diamond CFG where two branches load different section offsets with identical low 16 bits into the same register before merging into a single `addiu`/`lw` `LO16`.

3. **`_section_lo_shared` misses jump-table fallback on mismatch (`verify_decomp.py`).**
   In `_section_hi`, when `_rodata_table_va` fails with `"jump table entry..."`, it appends a placeholder, calls `_force_diff`, and returns `None` so the function can still be diffed. In `_section_lo_shared`, if `_rodata_table_va` fails, `va` remains `None`, and execution falls through to `self._anchored(...)`, which compares the raw unrelocated `.rodata` words (zeros) against the retail image's target addresses, failing with a misleading `"content ... the pointer at +0x... is ..."` or `"masked >= end - start"` instead of the jump-table diagnostic.
   *Evidence:* Trigger a shared `%lo` against a jump table where one case target differs.

4. **Register-based pairing limitations (`_pair_by_offset`, `_section_lo_shared`, `harvest_relocs.py`).**
   - *Register clobber / reuse:* If register `$rt` is loaded with `lui $rt, %hi(symA)`, consumed, and later reloaded with `lui $rt, %hi(symB)` before a second `%lo(symA)` that actually used a copied register (e.g., `addu $r2, $rt, $r0` or `addu $rt, $rt, $index` where `rs == rt`), filtering `same` by `(word >> 21) & 31 == rt` still works for `addu $rt, $rt, $index` followed by `lw $out, %lo(sym)($rt)` because `$rt` is preserved as `rs`. However, if the compiler copies the `lui` result to another register (`move $r2, $rt`) or puts the index in `rs` (`addu $r2, $index, $rt` -> `lw $out, %lo($r2)`), `same` is empty and it falls back to `cands[0]` / `max(his)`, which may pick an intervening unrelated `HI16`/`LO16` on the same section symbol.
   - *`%lo` at a lower address than `%hi`:* Both `_pair_by_offset` (`off < noff < hi_f`) and `_section_lo_shared` (`lo_f <= hoff < off`) require `hoff < loff`. If basic-block reordering or a backward loop branch places the `%lo` at a lower instruction offset than its dominating `%hi`, pairing fails (`HI16 without paired LO16` / `LO16 without a %hi`). This causes a false *skip* (`unresolved`), never a false match.
   *Evidence:* Inspect the assembly of the 2 failing functions in Xenosaga II (see Q2).

5. **`_relocated_pointers` masks all pointers to anonymous `.rodata` strings and anonymous `.data`/`.sdata`/`.bss` objects (`verify_decomp.py`).**
   In `_relocated_pointers`:
   ```python
   if rsym["typ"] == STT_SECTION:
       if self.sections[rsym["shndx"]]["name"] == ".text":
           expect = self._off_to_va(addend)
       else:
           expect = self._local_symbol_va(rsym["shndx"], addend)
   ```
   For `R_MIPS_32` in REL objects, `addend` is the 32-bit word in the section (which is the target section offset when `rsym` is `STT_SECTION`; this matches GNU `ee-as`). However, anonymous strings in `.rodata` (`.LC0`, stripped by the assembler into `.rodata + addend` with no symbol in `.symtab`) have no covering entry in `self.symbols`. Thus `_local_symbol_va` returns `None`, `expect` is `None`, and the pointer is **masked** (`masked += 4`).
   - If the anchored struct consists *entirely* of pointers to anonymous strings (e.g., `static const char *tbl[] = {"a", "b"};`), `masked >= end - start` rejects it as unverifiable (safe, but rejects valid code).
   - If the anchored struct has *at least one non-pointer byte* alongside pointers to anonymous strings (e.g., `struct { int id; const char *name; } tbl[] = {{1, "foo"}, {2, "bar"}};`), the `id` bytes satisfy `masked < end - start`, and the `name` pointers are **completely unchecked** (`expect is None`), allowing a false MATCH if the strings differ!
   *Evidence:* Compile a TU with `static struct { int x; const char *s; } d = {1, "wrong_string"};` in target mode where the original has `{1, "right_string"}`; `_anchored` will mask `s`, match `x == 1`, and report a false Tier-A MATCH. Fix: recursively verify the target bytes at `got` in the retail image against the target section span at `(rsym["shndx"], addend)`.

6. **`_section_refs` caches `_pair_addend` before `_fbounds` or in-order relocation state is settled (`verify_decomp.py`).**
   `_section_refs` calls `self._pair_addend(i, off, w)`, which in target mode calls `_pair_by_offset`. If a second `LO16` shares a `HI16` (`_section_lo_shared`) and targets a *different* offset in the same section (e.g. `sym+0` and `sym+4` sharing one `%hi`), `_section_refs` only records the first `LO16`'s addend because it only iterates `rt == R_MIPS_HI16`! The shared `LO16`'s target offset is omitted from `refs[sym["shndx"]]`, merging two adjacent objects into one span in `_object_span`.
   *Evidence:* Check `_section_refs()` loop: it has `if rt == R_MIPS_HI16:` and `elif rt in (R_MIPS_GPREL16, R_MIPS_LITERAL):`, completely ignoring `R_MIPS_LO16` entries that carry distinct offsets from a shared `HI16`.

---

### Question 2: The Two ".rodata HI16 without paired LO16" Functions

7. **Why `ee-gcc 2.96` emits a `.rodata` `HI16` with no `LO16` at a higher offset in the same function:**
   Three patterns in `ee-gcc 2.96` produce this:
   - **Case A (Loop / branch reordering — `%lo` before `%hi`):** A loop header or cold/hot block split places the `lui $reg, %hi(.rodata+X)` at the bottom of the function (e.g., in a loop tail or branch delay slot jumping backward) while the consuming `addiu`/`lw`/`lwc1` `%lo(.rodata+X)` is inside the loop body at a *lower* instruction offset (`noff < off`). Because `_pair_by_offset` filters with `off < noff < hi_f`, `cands` is empty.
   - **Case B (Constant-folded `%lo == 0` or CSE hoisting where `%lo` is deleted):** When a reference lands on an exact 64 KB boundary in the object file (`.rodata + 0x0000`) *would* still need a relocation because the final VA isn't 64K-aligned, so GCC never drops `%lo` for symbolic addresses unless dead-code elimination deletes the consumer instruction (e.g. an unused address calculation where `addiu` was eliminated as dead, leaving a dead `lui`). If the `lui` is dead, the original binary also has a dead `lui` at `off`, and its immediate is simply `self._image_word(self._off_to_va(off)) & 0xFFFF`.
   - **Case C (`STT_FUNC` boundary missing or wrong in `.symtab`):** If a static helper function in the TU has no `STT_FUNC` symbol or `hi_f` stops early at an internal label typed `STT_FUNC`, `off < noff < hi_f` clips off the `LO16`.

   *How to confirm and fix:*
   Run `ee-objdump -dr` on those two `.o` files.
   - If the `R_MIPS_LO16` exists in the same function with `noff < off` (Case A): in `_pair_by_offset`, when no candidate has `off < noff < hi_f`, fall back to searching `lo_f <= noff < off` where `(self._word_orig(noff) >> 21) & 31 == rt`.
   - If no `R_MIPS_LO16` exists for that register anywhere in the function (Case B — orphaned dead `lui`): there is no memory access; check whether `rt` is overwritten before being read, or pair it with the adjacent relocation entry in `self.relocs` if `ee-as` emitted a dummy/adjacent pair outside `[off, hi_f)`.

---

### Question 3: `tools/upstream_target.py`

8. **Contract collision silently overwrites compiler/assembler definitions (`upstream_target.py:173-179`).**
   In `target_manifest`:
   ```python
   for cname, c in contracts.items():
       if (c["cc1"], c["as"], c["as_flags"]) == (cc1, ee_as, as_flags):
           return cname
   contracts[label] = {"cc1": cc1, "as": ee_as, "cc1_flags": ..., "as_flags": as_flags}
   return label
   ```
   `label` is `f"{v.get('compiler_profile')}+{as_key}"` (e.g., `"default+ee-as"`). If two TUs share the same `compiler_profile` and `as_key` but have *different* per-TU `assembler_options` (for example, one TU has `assembler_options: ["-g"]` and another has `assembler_options: []`), their `(cc1, ee_as, as_flags)` tuples differ, so the loop does not match, and `contracts[label] = ...` **overwrites** `contracts["default+ee-as"]` in place! Every earlier TU that returned `"default+ee-as"` now gets compiled with the last TU's `as_flags`.
   *Evidence:* Check any `compile.json` where two TUs share `compiler_profile` and `assembler` but differ in `assembler_options`. Fix: include the normalized `opts` in `label` or disambiguate `label` when `label in contracts`.

9. **Cross-overlay duplicate symbol names are silently dropped (`upstream_target.py:103-107`).**
   In `symbol_rows`:
   ```python
   def add(unit, name, va, size, typ, shndx=1):
       key = (units[unit], name)
       if key in seen ...:
           return
   ```
   Wait: for `labels[u]` (data labels) and `*.externals.ld`, `owner(u, va)` maps any address inside the resident binary (`main`) to `resident`. If `u.txt` for an overlay defines a function or data symbol with the *same name* as another symbol in the same unit (or if `estimated-functions.json` has placeholder names), the second one is silently dropped. Worse: in `verify_decomp.py`, how does `self.syms.value(name)` index the CSV? If `verify_decomp.py` loads the CSV for all units without scoping by `binary` (or only loads `resident` + current overlay), any symbol in an overlay whose address happens to fall inside `resident`'s `memsz` (e.g., overlays linked at an address overlapping `main`'s BSS segment!) will cause `owner(unit, va)`:
   ```python
   for u in (unit, resident):
       if any(lo <= va < lo + n for lo, n in segs.get(u, [])):
           return u
   ```
   to assign it to `unit` when scanned from `unit.txt`, **except** in `*.externals.ld`:
   ```python
   o = m.group(3) if m.group(3) in units else owner(resident, int(m.group(2), 16))
   ```
   Here `owner(resident, va)` only checks `(resident, resident)`! If an `.externals.ld` entry has no `/* unit */` comment (or has `/* libgcc */` for a helper located in an overlay), it is either dropped (`o is None`) or misassigned to `resident` if the overlay VAs overlap `resident`'s `PT_LOAD` `memsz`.
   *Evidence:* Inspect `*.externals.ld` in `xsg-ii-decomp` for lines without a unit comment or with `/* libgcc */` that point into an overlay's VA range.

10. **`ROW` regex in `upstream_target.py` drops symbols when splat comments use `/* ... */` or trailing whitespace without `//` (`upstream_target.py:42`).**
    `ROW = re.compile(r"^\s*([A-Za-z_.$][\w.$]*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;\s*(?://\s*(.*))?$")`
    Splat `symbol_addrs.txt` files allow decimal addresses, upper-case `0X`, or inline `/* ... */` comments before `//`. Any line not matching `0x...` or carrying a `/* ... */` comment fails `ROW.match(line)` and is silently ignored without warning.
    *Evidence:* Run `grep -vE '^\s*$|^\s*//' config/symbols/*.txt` and check whether every non-empty, non-comment line matches `ROW`.
