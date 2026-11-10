# x-ai/grok-4.3

1. **tools/verify_decomp.py:OverlaySymbols.value (and AUTO_NAME)**: resolves FUN_/DAT_/LAB_ spellings to the numeric address they encode, so a wrong decompiler name still supplies a concrete VA. This can produce a successful relocation + byte match for an incorrect symbol.
   - Evidence: lines 1003-1005 (`m = AUTO_NAME.match(name); if m: return int(m.group(1),16)`); called from target_object + _anchored.
   - Test: feed a TU whose source contains a FUN_0x123456 name whose spelled address is not the real callee; run verify with --target and confirm MATCH is emitted.

2. **tools/verify_decomp.py:Object._anchored (content check)**: accepts any VA whose non-relocated bytes match the original after masking, so two distinct data objects that happen to contain identical bytes produce a false-positive anchor.
   - Evidence: lines 580-582 (`if ours == theirs: return va, None`); the mask only covers words listed in _relocated_words.
   - Test: create two .rodata objects whose non-reloc bytes are identical; reference the wrong one from a TU annotation and verify that MATCH is still reported.

3. **tools/verify_decomp.py:Object._pair_by_offset**: always takes the first LO16 (by reloc-table order) after a HI16 inside the same function; the -g assembler does not guarantee this is the correct pair when one %hi feeds multiple %lo.
   - Evidence: lines 340-345 (`cands = sorted(...); noff = cands[0]`); used unconditionally when anchors is set.
   - Test: TU with HI16 followed by two LO16 against the same symbol (different offsets); inspect the computed addend and resulting bytes_for() vs. original.

4. **tools/verify_decomp.py:run_target + docker_build (stock path)**: extra cc1 flags from annotations/manifest are passed via stock_cc1_flags only when compiler != "contract", but the same flags are also written into the per-TU flags file that later consumers read; a mismatch between the two paths can silently use different compiler options on the verification run.
   - Evidence: lines 985 (build_script), 1143 (docker_build call with stock_cc1_flags=extra), 1203 (flags written).
   - Test: annotate a TU with -fno-strict-aliasing under stock, run verify, diff the actual cc1 command line inside the container against the flags file.

5. **mcp/server.py + tools/match_kit.py:locate (target branch)**: when TARGET is active it returns a manifest-relative path without checking that the file still exists at that relative location after materialize(); subsequent open() can therefore read the wrong file or raise on a machine where the sandbox layout differs.
   - Evidence: lines 140-148 (target locate), 661 (card_data), 727 (materialize).
   - Test: run cmd_init with a target manifest whose include/ paths are outside the copied sources; observe locate succeeding but the later read using a stale absolute path.
