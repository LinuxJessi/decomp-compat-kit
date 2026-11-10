# x-ai/grok-4.3

1. ECOFF EXTR layout bug in dump_symbols.py: `mdebug_rows` does `unpack_from("<iII", ex0 + k*16 + 4)` assuming iss/value/bits start at byte 4, but self_test packs `"<hhiII"` (first 8 bytes header) and the real GNU as EXTR on IOP puts the SYMR at offset 0 or 8; test: feed a real CRI_ADXI.IRX .mdebug and compare produced externals vs `readelf -s` or `objdump -t`.

2. FDR `iss_base` + `rss` string offset uses `cbSsOffset` inside the loop but `cbSsExtOffset` only for EXTR; if GNU as emitted per-FDR ss, strings for file/proc names will be wrong or raise in `_cstr`; test: run on KORGIVAG.IRX and assert every yielded name decodes without ValueError and matches known symbols.

3. `has_symbols` accepts `.mdebug` with `size >= 8`; `mdebug_header` then requires `size >= HDRR.size` and magic, so discover_binaries silently drops valid small .mdebug modules; test: create 8-byte .mdebug ELF and check it is listed.

4. Fingerprint rule still overconfident on MGS3: the single 3.x sample has 5 immediates / 2654 floats (0.19 %) but its label depends on the same idiom plus absence of old mangling; no independent 3.x binary exists, so `pool and not s["gcc2_markers"]` can flip on any future binary with <5 % immediates; test: add a confirmed ee-gcc 3.2+ binary and re-run verdict.

5. `find_symbols.py` never matches big-endian or 64-bit ELFs: `ELF_MAGIC = b"\x7fELF\x01\x01\x01"` only matches LE 32-bit; any BE IOP or 64-bit stub is invisible; test: plant `\x7fELF\x01\x02\x01` and `\x7fELF\x02\x01\x01` at arbitrary offsets and assert they are reported.

6. ISO multi-extent and non-ISO (UDF, raw) files: `iso_files` only walks single-extent directory records (no XA or continuation flag handling) and returns [] for non-CD001; a symbol table in a later extent or UDF file extent is never located even if the raw scan finds the ELF; test: build image with multi-extent file containing an ELF and check `where` field.

7. Chunk-boundary ELF header: `OVERLAP=64` > magic size so `data.find` catches magic that straddles CHUNK, but `describe_elf` then does `fh.seek(off); hdr=fh.read(52)` which succeeds; no crash, but section table read may still be truncated if the ELF header itself straddles a 2048-byte sector in a way that `end` calc overflows; test: place full ELF so its e_shoff crosses a CHUNK boundary.

8. Hostile-data crash in `describe_elf`: `shnum` is taken from header with only `0 < shnum < 4096` check; an attacker can set `shnum=4095`, `shoff` near end of image so `table = fh.read(163800)` succeeds but later `secs` indexing or `end = max(...)` produces huge allocation or `off + end > size` path; test: 52-byte header with `shnum=3000, shoff=size-100`.
