ROUND 3: review three tool changes for a public PS2 decomp toolkit before they are committed. All are stdlib Python.

1. fingerprint_elf.py: the rule that tells SCE ee-gcc 2.96 from 3.x by float constants was wrong (it counted every non-stack lwc1 as a "constant-pool load", so stripped 2.96 binaries read as 3.x). New rule: among all float loads (lwc1/ldc1 not off $sp, plus lui+mtc1 immediates), 3.x is called only if immediates are under 5 % AND there are >= 50 prologues and >= 100 float loads; otherwise the test abstains and says so.
   Measured on real binaries (prologues / lui+mtc1 immediates / lwc1 loads -> verdict now):
     Xenosaga II main, stripped, known 2.96:   3829 / 1761 / 6916 -> 2.96
     Xenosaga II battle overlay, stripped:      756 /  165 /  305 -> 2.96
     Xenosaga II boot overlay, stripped:         14 /    0 /   25 -> 2.96, abstains (was "3.x")
     Xenosaga I Reloaded, stripped, known 2.96: 2454 / 1493 / 7035 -> 2.96 (was "3.x")
     Xenosaga I, unstripped, known 2.96:       2435 / 1487 / 6990 -> 2.96
     Xenosaga III, stripped, 2.96:             4915 / 2603 / 8385 -> 2.96
     Final Fantasy X (2.9-991111 lineage):     5421 / 1473 / 5809 -> 2.9x 1999 (unchanged, by stride)
     Metal Gear Solid 3 (believed 3.x):        1361 /    5 / 2649 -> 3.x (unchanged)
   Known weakness: MGS3 is the only 3.x sample, and its 3.x label itself rests on this idiom plus Itanium-mangling absence; no independently confirmed 3.x binary is in the corpus.
2. dump_symbols.py: rewritten to also read ECOFF .mdebug (file descriptors, procedures, statics, labels, externals). On real IOP modules: KORGIVAG.IRX -> 4 files, 239 procedures (65 global + 174 static), 91 statics, 29 labels; CRI_ADXI.IRX -> 23 files, 275 procedures. .symtab output unchanged.
3. find_symbols.py: new. Scans a disc image byte by byte for ELF headers at any offset and toolchain text; reports which ELFs carry real symbols. On a 4.7 GB image: 27 MIPS ELF headers, 0 outside listed files, 2 with symbols, 13 s. Matches a separate hand-written scan.

Find: bugs; wrong ECOFF field layouts or offsets (HDRR/FDR/SYMR/EXTR, little-endian MIPS, as written by GNU as for the PS2 IOP); cases where the fingerprint rule would now give a wrong or overconfident verdict; ways find_symbols.py misses a symbol table that is really there (e.g. ELF magic spanning a chunk boundary, multi-extent or UDF-only files, big-endian, 64-bit class) or crashes on hostile data. Say how to test each. Ignore style.

==== git diff tools/fingerprint_elf.py ====
diff --git a/tools/fingerprint_elf.py b/tools/fingerprint_elf.py
index 2f64b36..dbb17bf 100755
--- a/tools/fingerprint_elf.py
