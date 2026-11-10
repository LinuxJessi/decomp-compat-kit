ROUND 2: code review of a diff before it is committed to a public toolkit for PS2 matching decompilation.

What it adds: "target mode". A JSON manifest (schema decomp-target/1) describes a stripped game: its binaries, a symbols CSV, units, per-TU sources with flags and a compile contract. verify_decomp.py compiles each TU with the original SCE ee-gcc 2.96 (in docker), applies relocations from the symbol table, and compares bytes with the retail binary. ledger.py keeps a table of functions proven byte-identical with nothing masked ("Tier A"). port_check.py compiles game A's source and judges it against game B. harvest_relocs.py reads symbol addresses back out of B's relocated fields at functions that are identical once those fields are masked. transfer_symbols.py --sibling maps functions between two builds of the same game.

Measured before this review: all tool self-tests pass; MCP server tests 24/24; the ledger re-proves 141/141 functions Tier A on the real binary in 24 s.

Find: correctness bugs; ways the verifier could report a MATCH that is not real (the worst failure for this tool: e.g. accepting a data reference "by content" when the bytes coincide by accident, HI16/LO16 pairing mistakes, a symbol resolved from its own FUN_/DAT_ spelling masking a wrong address); unsafe file handling; anything that only works on one machine. Give file and function for each finding and say how to test it. Ignore style.

==== git diff (tracked files; docs omitted) ====
diff --git a/mcp/server.py b/mcp/server.py
index 6b50d84..2281bd2 100644
(the diff under review followed here: the working tree of 2026-11-10 against 424c929, tools/ and mcp/server.py, plus the three new tools)
