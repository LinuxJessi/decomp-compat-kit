# openai/gpt-6-astra-pro

**Do not commit as-is.** The following need fixes or explicit limitations.

1. **The fingerprint still measures workload, not constant-pool use.** Increasing the sample size does not make non-stack `lwc1`/`ldc1` distinguish constants from fields, arrays or globals. A large 2.96 binary processing floats without materializing many constants can satisfy the new 3.x rule. The `plain * 60` test actually demonstrates this ambiguity; it does not establish compiler provenance.  
   **Test:** Compile constant-free float-array/struct processing with independently identified 2.96 and 3.x toolchains, strip the outputs, and exercise both size thresholds.

2. **The claimed 3.x validation is circular.** MGS3 is labeled using the same idiom being validated. Absence of Itanium names is not independent positive evidence for 3.x, especially in stripped or C code. “Medium” confidence is unsupported by this corpus.  
   **Test:** Obtain independently identified 3.x outputs, including several workloads and optimization modes. Until then, report “3.x-like float idiom,” not a validated compiler identification.

3. **Abstention is only explanatory text; the compiler verdict remains specific.** The small-overlay self-test explicitly requires `SCE ee-gcc 2.96`. A consumer reading only `compiler` still receives a classification the float test cannot support. Moreover, a sufficiently large genuine 3.x binary with ≥5% immediates falls through this test without an abstention explanation.  
   **Test:** Exercise the `sd` branch with no independent markers at 49/50 prologues, 99/100 float sites, and immediately below/at/above 5%. Inspect structured verdict fields, not merely reasons. Represent inconclusive evidence explicitly.

4. **“Read the verdict of the executable it was linked with” is not a safe inference.** Overlays and linked libraries can come from different compiler versions; mixed objects also distort the aggregate ratio.  
   **Test:** Link known 2.96 and 3.x objects in varying proportions and test separately built overlays. Treat another executable’s verdict as contextual evidence, not ground truth.

5. **The shown GNU little-endian MIPS ECOFF layouts are sound:** HDRR is 96 bytes with the stated field order; FDR is 72 bytes with the first six fields at offsets 0–20; SYMR is 12 bytes with `st` in the low six bits; EXTR is 16 bytes with SYMR at `+4`; the table offsets are ELF-file-relative. **Verify against the target GNU binutils external structures/swap routines and an independently generated fixture—not just this self-test.**

6. **ECOFF bounds checking is incomplete and can silently invent names.** `issMax`, `issExtMax` and FDR `cbSs` are not enforced. `_cstr` searches to the end of the entire file. Negative local `iss` or `iss_base` can address unrelated earlier bytes; negative `csym` silently suppresses records. Tables can also overlap unrelated ELF content without rejection.  
   **Test:** Mutate each string offset/base/length independently, including negative values and a terminator just beyond its string table. Set `csym=-1`. Require explicit rejection rather than plausible unrelated names or silent omission.

7. **The ECOFF fixture cannot establish compatibility with GNU output.** It encodes the same layout assumptions as the parser, leaves both FDR string lengths zero despite nonempty strings, and tests almost no packed fields or sentinel values. Real-module aggregate counts do not verify names, addresses or file attribution.  
   **Test:** Add an authentic GNU-as-produced fixture and independently expected rows, including nonzero storage classes/index bits, multiple FDR string bases, external flags/`ifd`, and undefined/absolute symbols. Compare exact records, not counts.

8. **Section and symbol discovery incorrectly depends on conventional section names.** `read_sections` discards `sh_type`, `sh_link` and `sh_entsize`; `symtab_rows` always pairs `.symtab` with `.strtab`. A valid symbol table can have another name or link to a differently named string table. Duplicate section names also overwrite entries.  
   **Test:** Rename an otherwise valid `SHT_SYMTAB` and its linked string table; add an unrelated `.strtab`; create duplicate names. Discovery and symbol decoding should follow section metadata.

9. **`dump_symbols` does not enforce its advertised ELF format.** `sections_of` checks only four magic bytes before interpreting everything as ELF32 little-endian; it never verifies class, byte order, version or MIPS machine. Unsupported files can be misparsed rather than cleanly rejected. Section-name strings are also not bounded by `.shstrtab`.  
   **Test:** Supply ELF64, big-endian, non-MIPS and malformed-ident fixtures, plus section-name offsets outside `.shstrtab`. Require explicit unsupported/malformed results.

10. **A malformed symbol name can terminate the entire disc scan.** `describe_elf` calls `symtab_rows` without catching `ValueError` or `struct.error`. An out-of-range `st_name` or missing terminating NUL escapes into `scan`. A malicious section named `.symtab` with `SHT_NOBITS` can also bypass the payload-end calculation and provoke an unpack failure. `dump_symbols` likewise aborts and can leave partial CSV output.  
    **Test:** Put each malformed ELF before a valid symbol-bearing ELF. The report must record the malformed candidate and still find the later one.

11. **“No ELF carries a symbol table” can be false even for supported ELFs.** `with_symbols` excludes tables containing only named `STT_NOTYPE` symbols—common for assembly labels—even though `symtab` reports their count. Conversely, undefined FUNC/OBJECT imports qualify as finds without distinguishing definitions. `.dynsym` is ignored entirely.  
    **Test:** Use separate fixtures containing defined NOTYPE labels, undefined FUNC imports, and only dynamic symbols. Report named symbols, code/data definitions and imports separately.

12. **Debug-section presence is mistaken for validated symbol content.** Any nonempty `.mdebug` qualifies, even with invalid magic or zero meaningful records. For a valid header, counts are copied without validating its tables; `mdebug_rows` is never called. Nonempty `.stabstr` or `.debug_line` likewise does not establish named functions. Compressed `.zdebug_info` is missed.  
    **Test:** Include invalid/empty ECOFF headers, impossible counts, string-only stabs, line-only DWARF and compressed DWARF. Distinguish “debug section present,” “validated symbols” and “malformed.”

13. **Several valid ELF representations are silently missed.** The seven-byte search pattern excludes big-endian and ELF64 headers. `describe_elf` rejects ordinary section counts ≥4096, ELF extended section numbering, and payload spans above 256 MiB. `dump_symbols` also lacks extended numbering. These restrictions are not all stated in the scanner’s public description.  
    **Test:** Add BE/64-bit MIPS fixtures, a 4096-section ELF, extended-count/string-index fixtures, and a sparse large-offset ELF. Either support them or report unsupported coverage rather than asserting absence.

14. **Noncontiguous files are not reconstructed.** Raw scanning finds an ELF header but `describe_elf` assumes every ELF-relative offset maps to `off + offset` in the image. Multi-extent ISO files and fragmented UDF files violate that assumption, losing symbol tables in later extents or reading unrelated bytes. A contiguous UDF-only ELF remains discoverable; lack of UDF listing alone affects attribution, not raw detection.  
    **Test:** Split a valid ELF before its string/symbol/section tables across nonadjacent extents in ISO and UDF images. Compare results with scanning the extracted logical file.

15. **Filesystem attribution has additional failure cases.** `iso_files` requires the PVD at sector 16 rather than walking volume descriptors, so a preceding boot descriptor defeats listing. Extended-attribute blocks and interleaving are not accounted for. `locate` checks only the latest-starting interval, so overlapping extents can produce false “outside” results.  
    **Test:** Use a boot-record-first ISO, extended-attribute/interleaved files, and nested overlapping intervals. Check both path attribution and outside-file counts.

16. **Hostile images can exhaust memory or CPU.** Every ELF-magic occurrence is retained without a cap. Repeated candidates can each trigger a read up to 256 MiB; `rows` materializes every named symbol. Repeated string offsets and long strings add substantial repeated scanning/copying. ISO directory output is also unbounded across directories.  
    **Test:** Generate dense magic hits, many headers pointing into large plausible tables, and millions of symbol entries sharing long names. Enforce resource budgets and report incomplete coverage explicitly.

17. **Chunk-boundary matching is sound for the current patterns:** 64 bytes of overlap exceeds every pattern length, and the end-position check avoids duplicate complete matches. **Test every split position around `CHUNK`, including a match ending exactly at the boundary; the current small self-test never crosses one.**

18. **Recursive CSV output loses binary identity.** Both writers use `path.name`, so `IOP/A.IRX` and `DEMO/A.IRX` become indistinguishable.  
    **Test:** Put distinct symbols in two same-basename files under different directories. Export paths relative to `--code-dir`.
