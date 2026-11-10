# Review round: fingerprint float rule, `dump_symbols.py` .mdebug, `find_symbols.py` (2026-11-10)

Gemini, GPT and Grok answered; GLM returned nothing; the others had not answered when this
was written. Their answers are beside this file, unedited.

## Confirmed and fixed

| Finding (who) | Fix |
|---|---|
| The 3.x verdict is circular: MGS3 is the only 3.x sample and its label rests on this idiom plus its unpadded `sqrt.s` (GPT 1-2, Grok 4, Gemini 3) | The float idiom alone now gives `SCE ee-gcc 3.x` at **low** confidence, and the reason says it is unconfirmed and what would look the same. The rule itself stays: 18-39 % immediates on six 2.96 binaries against 0.2 % is a real separation, but it is a lead, not an identification. |
| Abstaining was only a sentence in `reasons` (GPT 3) | `verdict["float_constants"]` is `"2.96-like"`, `"3.x-like"`, `"abstains"` or `None` (test not run). |
| A small body with old GNU mangling was downgraded for no reason (Gemini 2) | Markers or old mangling keep their confidence; only a small body with neither drops to low. |
| A bad name offset or string table could raise out of the scan (Gemini 4, 11; GPT 10) | `symtab_rows` never raises on a name; `describe_elf` records `unreadable: ...` and the scan goes on (`hostile-survived`). |
| ECOFF strings were not bounded by their table; a negative index read earlier bytes (Gemini 6, GPT 6) | `_cstr` stays inside `issMax` / `issExtMax`; negative indexes and counts are refused (`mdebug-negative-iss`). |
| The symbol table was found by name, not type (GPT 8) | First `SHT_SYMTAB` section and the string table it links to. |
| ELF class and byte order were not checked in `dump_symbols.py` (GPT 9) | 32-bit little-endian or nothing (`elf64-refused`). |
| A table of only labels was not a find; an empty debug section was (GPT 11-12, Gemini 10) | Any named symbol counts; a debug section counts only with real content, `.mdebug` only with tables that read to the end. |
| Two files of one name collided in recursive CSV output (GPT 18) | With `--recursive` the binary column is the path under `--code-dir`. |
| Unbounded candidates and 256 MiB reads (GPT 16, Grok 8) | 100,000 candidates, 64 MiB per ELF, both reported when hit. |
| No chunk-edge test (GPT 17) | `chunk-edge` re-scans with the chunk size set so a magic number straddles the edge. |

## Checked and found right

- **EXTR layout (Grok 1).** Decoded with the symbol record at +4, the externals of the real
  modules agree with `.symtab` by name and address: KORGIVAG 127/128, CRI_ADXI 327/435 (the
  rest are undefined imports, which `.symtab` lists at 0). Local procedures agree 239/239
  and 275/275. GPT and Gemini also read the layouts as correct.

## Stated as limits in the tool's own help

32-bit little-endian ELF only; an ELF stored in pieces is reported unreadable, not
reconstructed; 2352-byte raw sectors are not handled; names come from ISO 9660 only (a
UDF-only file is still scanned). `.dynsym` and compressed DWARF are not looked for: no PS2
executable carries them.

## Open

A second, independently identified ee-gcc 3.x binary, to turn the float rule from a lead
into a test (GPT 2, Gemini 3: build one from the SCE 3.x toolchain at -O0 and -O2).
