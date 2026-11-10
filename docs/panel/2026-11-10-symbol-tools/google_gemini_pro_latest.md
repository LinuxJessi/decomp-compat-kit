# ~google/gemini-pro-latest

ECOFF struct layouts (`HDRR`, `FDR`, `SYMR`, `EXTR`) for little-endian 32-bit MIPS and the chunk overlap math in `find_symbols.py` are sound.

### `fingerprint_elf.py`

1. **False `3.x` verdict on integer/fixed-point codebases with small float helper routines.**
   A stripped 2.96 binary with `>= 50` prologues and `>= 100` struct/array `lwc1` copies (e.g., an audio/scripting overlay or math pass that moves float vectors via `lwc1`/`swc1` without inline float literals) has `float_immediates == 0`, triggering `pool = True` and an unconditional `"SCE ee-gcc 3.x"` verdict.
   *Test:* Compile a 60-function C file under `ee-gcc` 2.96 that copies `struct { float x, y, z, w; }` pointers without float literal constants, strip it, and run `fingerprint_elf.py`; confirm whether it misidentifies 2.96 as 3.x.

2. **Confidence downgrade on small binaries ignores `old_gnu` mangling.**
   Line 444: `conf = "medium" if conf == "high" and not s["gcc2_markers"] else conf`. An unstripped C++ overlay compiled with 2.96 (`--no-gcc2-markers` or stripped of local `gcc2_compiled.` symbols) that has `< 50` prologues or `< 100` float loads will have `conf == "high"` from `s["mangling"]["old_gnu"] > 2`, which cannot occur in 3.x (Itanium ABI). Line 444 downgrades it to `"medium"` and emits the misleading reason `"too small to tell 2.96 from 3.x by float constants"`.
   *Test:* Run `fingerprint_elf.py` on an ELF with 10 prologues, 0 float loads, 0 `gcc2_compiled.` markers, and 5 old-GNU mangled symbols (`func__1C`); verify `conf` drops to `"medium"` despite definitive 2.x mangling.

3. **3.x float-pool rule is unproven against 3.x flags and 2.96 `-O0` / literal-free code.**
   As noted in the prompt, the corpus has zero verified `ee-gcc` 3.x binaries (MGS3 is assumed). Furthermore, `ee-gcc` 2.96 at `-O0` or without `-fshort-double` can emit `.rodata` constant loads rather than `lui`+`mtc1`.
   *Test:* Build a reference binary using an actual SCE `ee-gcc` 3.2.x SDK toolchain (at `-O0`, `-O2`, `-Os`) and `ee-gcc` 2.96 at `-O0`, strip them, and verify the immediate-to-load ratios before committing the 5% threshold.

### `dump_symbols.py`

4. **Crash (`ValueError`) on malformed or truncated string tables in `symtab_rows` and `mdebug_rows`.**
   `read_sections` catches `ValueError` inside `sections_of`, but `symtab_rows` (line 115: `data.index(b"\0", str_off + name_off)`) and `_cstr` (line 138: `data.index(b"\0", off)`) are called outside `try/except`. If `.strtab` or `.mdebug` string tables lack a trailing NUL or a corrupt symbol `name_off` / `iss` points past the file or into un-terminated bytes, `dump_symbols.py` (and `find_symbols.py` calling `ds.symtab_rows`) crashes with an unhandled `ValueError`.
   *Test:* Feed `symtab_rows` or `mdebug_rows` an ELF where `name_off` is `len(data) + 5` or where `.strtab` has no `b"\0"` byte; confirm unhandled `ValueError`.

5. **Silent overwrite of duplicate section names in `read_sections`.**
   Line 65: `secs[name.decode(errors="replace")] = (offset, size)` overwrites earlier entries when an ELF contains multiple sections with the same name (e.g., relocatable `.o` or non-standard `.irx` files with multiple `.symtab` or empty-named sections). If a dummy/empty `.symtab` follows a populated one, symbols are missed.
   *Test:* Construct an ELF with two `.symtab` sections (the first populated, the second 0-length) and verify `has_symbols` returns `False`.

6. **Unchecked negative `iss` in `mdebug_rows` reads arbitrary preceding file bytes.**
   Line 168: `_cstr(data, h["cbSsOffset"] + iss_base + iss)` and line 172: `_cstr(data, h["cbSsExtOffset"] + iss)`. `iss` is unpacked as signed `<i`. In ECOFF, `iss == -1` (or `0` pointing to `\0`) denotes an anonymous symbol. If `iss < 0` (e.g. `-1` when `iss_base == 0`), `cbSsOffset - 1` remains `>= 0`, passing the check in `_cstr` and reading the last byte of whatever precedes the string table.
   *Test:* Run `mdebug_rows` on a synthetic `.mdebug` record with `iss = -1` and `iss_base = 0` where the byte before `cbSsOffset` is non-zero; verify it emits garbage text instead of `""`.

### `find_symbols.py`

7. **Misses ELF headers in raw Sector Mode 2 / 2352-byte `.bin`/`.img` CD dumps if sections span sector ECC/headers, and `iso_files` fails on 2352-byte images.**
   Many PS2 CD-ROM titles are dumped as 2352 bytes/sector (Mode 2 Form 1, 24-byte sync/header + 2048-byte payload + 280-byte EDC/ECC). `iso_files` hardcodes `SECTOR = 2048`, so directory parsing fails (`pvd` check at `16 * 2048` misses). Worse, `describe_elf` reads contiguous bytes (`fh.seek(off + shoff)` and `fh.read(end)`), which shifts section header and string table offsets by 304 bytes per sector crossed, corrupting `sections_of` and missing symbols in any ELF larger than one sector.
   *Test:* Create a 2352-byte/sector Mode 2 image containing an unstripped 10 KB ELF and run `find_symbols.py`; confirm `listed_files == 0` and `describe_elf` fails to parse the section table.

8. **Misses files in UDF-only or ISO 9660 multi-extent listings (`iso_files`).**
   `iso_files` only reads the ISO 9660 Primary Volume Descriptor; files present only in the UDF bridge (common on dual-layer DVD-9 titles like *Xenosaga* or games hiding assets from ISO 9660) or split across multiple ISO directory records (multi-extent files, bit 7 of `flags`) are either omitted from `files` or truncated to the last extent in `locate()` because `bisect_right` assumes non-overlapping intervals and multi-extent files emit multiple tuples. (The raw scan still finds the ELF if contiguous, but `where` misreports it).
   *Test:* Create an ISO image with two directory records for the same file (extent 0 with `flags = 0x80`, extent 1 with `flags = 0x00`) or an overlapping LBA range where a dummy outer entry spans `[100, 500)` and an inner file spans `[200, 300)`; test `locate(files, starts, 400)` and confirm it returns `(outside every listed file)` because `bisect_right` selects the inner file `[200, 300)`.

9. **Silently ignores 64-bit (`ELFCLASS64`) and big-endian ELFs.**
   `ELF_MAGIC = b"\x7fELF\x01\x01\x01"` hardcodes `EI_CLASS == 1` (32-bit) and `EI_DATA == 1` (little-endian). PS2 Linux binaries, certain toolchain/host artefacts left in disc slack space, or multi-platform studio leftovers (e.g., GameCube/PS3/network-adapter firmware blobs) using 64-bit MIPS or big-endian ELF headers are completely invisible.
   *Test:* Place a 64-bit MIPS ELF (`\x7fELF\x02\x01\x01`) with a `.symtab` into a test image and run `find_symbols.py`; verify `elf_headers == 0`.

10. **Misses `.dynsym` symbols and counts stripped ELFs with empty debug sections as finds.**
    `describe_elf` only checks `ds.symtab_rows(data)` (which looks only for `.symtab`/`.strtab`) and checks `names.get(n, (0, 0))[1] > 0` for `DEBUG_SECTIONS`.
    - An ELF stripped of `.symtab` that still exports function names via `.dynsym`/`.dynstr` reports `funcs = 0, objects = 0` and is dropped from `with_symbols`.
    - An ELF whose `.mdebug` section is present in the section header with `size > 0` but contains an invalid/empty header (`mdebug_header` returns `None`), or has only `.stabstr` (size 1, single NUL byte) with 0 `.stab` entries, is still added to `with_symbols` because `e["debug"]` is non-empty.
    *Test:* (a) Scan an ELF with `.dynsym`/`.dynstr` and no `.symtab`; confirm it is missed. (b) Scan an ELF with a 1-byte `.stabstr` (`b"\0"`) and no other symbols; confirm it is falsely reported in `with_symbols`.

11. **Peak memory spike up to 256 MB per ELF and unhandled `ValueError` in `describe_elf`.**
    In `describe_elf`, `end` can be up to `256 << 20`, read into memory at once (`data = fh.read(end)`), and passed to `ds.symtab_rows(data)`. As noted in Finding 4, if a hostile or corrupted stray ELF header passes the `shnum` and `end` bounds checks (e.g. random data with `mach == 8`, `shentsize == 40`, `shnum == 2`, and a valid-looking `.symtab` / `.strtab`), `ds.symtab_rows(data)` will raise an unhandled `ValueError` when `data.index(b"\0", ...)` fails, crashing the entire 4.7 GB scan.
    *Test:* Construct a 200-byte blob with `ELF_MAGIC`, `mach = 8`, and a section table defining `.shstrtab`, `.symtab` (16 bytes, `name_off = 0`), and `.strtab` (4 bytes of `b"AAAA"`, no NUL); run `find_symbols.py` on it and verify it crashes with `ValueError: subsection not found`.
