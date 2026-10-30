# Corpus: what built six PS2 games, as measured

Every row below is the output of `tools/fingerprint_elf.py` (and, where the
code is not a plain ELF, `tools/unwrap_image.py` first) run on the retail
executables of discs the author owns. The numbers are reproducible with those
two tools; nothing here is copied from another project's write-up.

## The fingerprinting order, and why

1. **Vendor strings first.** Metrowerks' `MW MIPS C Compiler (2.4.1.01)` sits in
   `.comment` and survives stripping; GNU strings, `gcc2_compiled.` symbols and
   old GNU C++ mangling settle GCC 2.x; Itanium `_Z` names settle 3.x.
2. **Container tells.** An `MWo3` header is CodeWarrior's linker overlay. A boot
   ELF whose single segment begins `[u32 length][zlib stream]` is a decompressor
   stub and the real image is the payload. ELF type `0xff80` / `.iopmod` is an
   IOP module: R3000 code, none of the EE heuristics apply.
3. **Prologue census.** `addiu $sp,$sp,-N` followed by callee saves. SCE ee-gcc
   saves with `sd`; ProDG and CodeWarrior save with `sq` at a 16-byte stride.
4. **Idioms.** Real `div`/`div.s` (every compiler met so far), float constants as
   `lui`+`mtc1` immediates (ee-gcc 2.96) versus `.rodata` loads (3.x), and the
   `sd` stride: 8 bytes for 2.96 and 3.x, 16 bytes for the 1999 `2.9-991111`
   build (the compiler-id idiom rig established that stride table).
5. **SDK banners** `PsIIlib<name><vers>` date the Sony SDK; `__DATE__` stamps
   and surviving source paths sketch the translation-unit skeleton.

Two corrections this corpus forced on the earlier heuristics:

- **`sq`/16-byte saves do not mean SN ProDG.** Metrowerks CodeWarrior saves the
  same way. Silent Hill 2 would have been filed as a Western ProDG title by the
  census alone; its `.comment` says CodeWarrior. The census corroborates a
  vendor string, it never replaces one.
- **The toolchain is chosen per studio, not per region or publisher.** Konami's
  KCET (Silent Hill 2) used CodeWarrior while Kojima Productions (MGS3) used
  SCE's GCC; Sega's Yakuza team used CodeWarrior. "Japanese = ee-gcc" holds as a
  majority, not a rule.

A minority save style is present in every binary (the Sony SDK static
libraries are ee-gcc objects whatever built the game), so the dominant style
decides and the minority is reported, not explained away.

## The table

| Game | Serial | Year | Studio | Packaging | Verdict (confidence) | Key evidence | Stripped | Prologues: dominant / minority |
|---|---|---|---|---|---|---|---|---|
| Xenosaga Episode I | SLUS_204.69 | 2002 | Monolith Soft | plain ELF + 5 ELF overlays at 0xa00000 | **SCE ee-gcc 2.96** (high; proven by byte-matching upstream C with the stock compiler) | 830 `gcc2_compiled.` markers, 46 old-GNU mangled names, 0 Itanium; float immediates 1,487; 4-byte `.eh_frame`; `.vutext`; banners libcdvd 2.4.2.4, libgraph 2.4.0.0, libkernl 2.4.2.0, libmpeg 2.4.2.0; stamp Sep 5 2002; `../Undulate.euc.c` | no (4,636 FUNC) | sd 2,373 of 2,435 (sd/8 2,118, sd/16 254) / sw 61, sq 1 |
| Xenosaga Episode I, OV11.OVL | — | 2002 | Monolith Soft | ELF overlay (section `ov11`) | SCE ee-gcc 2.96 (high) | 6 gcc2 markers; stamp Nov 24 2002 | no (139 FUNC) | sd 83 of 83 (all sd/8) |
| Xenosaga Episode I, SSD.IRX | — | 2002 | Monolith Soft | IOP module (ELF 0xff80, `.iopmod`) | IOP module, R3000: EE heuristics skipped | 442 FUNC symbols; `sw`-only prologues (MIPS-I) | no | sw 214 of 214 |
| Xenosaga Episode III | SLUS_213.89 | 2006 | Monolith Soft | plain ELF + 3 ELF overlays at 0xa7f000 | **SCE ee-gcc 2.96** (medium here; proven elsewhere by the OV04 HaKox byte-match) | float immediates 2,603; 4-byte `.eh_frame`; `.vutext`; banners libcdvd/libgraph/libkernl/libmpeg 3.0.0.0; 12 stamps Aug 9 2005; 38 `.euc.c` source names | yes | sd 4,876 of 4,915 (sd/8 4,566, sd/16 310) / sw 36, sq 3 |
| Xenosaga Episode III, OV04.OVL | — | 2006 | Monolith Soft | ELF overlay | SCE ee-gcc 2.96 (medium; this is the overlay the byte-match proved) | float immediates 613 | yes | sd 181 of 181 (all sd/8) |
| Final Fantasy X | SLUS_203.12 | 2001 | Square | plain ELF, `.vutext` in the ELF | **SCE ee-gcc 2.9x, 1999 build** (`2.9-991111`-style `sd` at 16-byte stride; medium, new hypothesis) | sd/16 5,339 vs sd/8 28; float immediates 1,473; banners libcdvd 2.3.4.0, libgraph 2.3.0.0, libkernl 2.3.4.0; stamps Jun 2, Oct 25, Oct 30, Nov 2 2001; 40 source names (`tklib.c`, `rcBBox.c`, …) | yes | sd 5,375 of 5,421 / sw 42, sq 4 |
| Metal Gear Solid 3 | SLUS_209.15 | 2004 | Kojima Productions | **zlib-stub ELF** (PT_LOAD 0x800000) → 1,069,700-byte headerless image with a Konami header (word 0 = 2004-10-29 11:43:09 UTC) | **SCE ee-gcc 3.x** (medium; earlier notes said "2.9x-consistent" from the stride alone) | sd/8 1,035, sd/16 295; float constants from `.rodata` 2,649 vs 5 immediates; banners libcdvd/libgraph/libkernl 3.0.0.0, libpad2 3.0.2.0 | yes | sd 1,330 of 1,361 / sw 26, sq 5 |
| Silent Hill 2 | SLUS_202.28 | 2001 | Konami KCET (Team Silent) | plain ELF; 54 `MWo3` stage overlays at 0x1f0bc00 (`GX/*.BIN`, e.g. `gx_aee`: text 0xcc0, data 0xa80, bss 0xe00) | **Metrowerks CodeWarrior** MW MIPS C 2.4.1.01 (high) | `.comment` = `MW MIPS C Compiler (2.4.1.01)` / `PlayStation2`; sq(sp) 8,831 vs sd(sp) 2,639; banners libcdvd 2.2.4.0, libgraph 2.2.0.0, libkernl 2.2.4.0 | yes | sq 2,100 of 2,514 (sq/16) / sd 393 (mostly sd/16), sw 21 |
| Yakuza | SLUS_213.48 | 2006 | Sega (Amusement Vision) | tiny loader ELF + the whole game as an **`MWo3` overlay** `MAIN.BIN` at 0x100000 (text 0x2e0d20, data 0x147420, bss 0x9b500) | **Metrowerks CodeWarrior** MW MIPS C 2.4.1.01 (high; the loader carries the `.comment`, MAIN.BIN is the MWo3 container) | loader `.comment` = `MW MIPS C Compiler (2.4.1.01)`; MAIN.BIN sq/16 3,639; 131 source names (`Map.cpp`, `YAct.cpp`, `Media.cpp`); stamps Feb 28 2005 and Aug 2 2005; loader banners libcdvd/libgraph/libkernl 3.0.0.0, libpad2 3.0.0.0 | yes | MAIN.BIN: sq 3,640 of 5,523 / sd 1,844 (sd/8 1,771), sw 39 |

Confidence is the tool's own grading of its evidence, not a statement about
the games: for Xenosaga I and III the compiler is additionally *proven* by
byte-identical compiles (see the README's case study and the XS3 HaKox match),
which no fingerprint can replace.

## What the table changed

- **MGS3 looks like ee-gcc 3.x, not 2.9x.** The stride census alone
  (`sd`/8-byte) is compatible with both 2.96 and 3.x, which is why the first
  pass called it "2.9x-consistent". The float-constant idiom is not: 2,649
  constants loaded from `.rodata` against 5 formed as immediates is the 3.x
  shape the compiler-id probe rig measured, and SDK 3.0 banners plus a
  2004-10-29 build date fit the `3.2-ee-040921` era. Unproven until a function
  matches under that compiler; worth a probe before anyone matches MGS3 with
  2.96.
- **FFX saves at a 16-byte `sd` stride**, which the compiler-id rig only ever
  saw from the 1999 `ee-gcc 2.9-991111` build (2.96 and 3.x save at 8). With a
  2001 release and SDK 2.3 banners that is the natural reading. Again a lead
  for the matching rig, not a verdict.
- **Yakuza's CodeWarrior census is 66 % `sq`, not 80 %+ as SH2's,** because
  MAIN.BIN links a large `sd`/8 population (1,771 prologues): CRI middleware
  and Sony libraries built with ee-gcc, exactly the "mixed census" footnote.
  The container (`MWo3`) and the loader's `.comment` decide, not the ratio.
- **The Yakuza loader itself is `sd`/16-dominant** with a CodeWarrior
  `.comment`: Metrowerks emits `sd` for small frames too. Another reason the
  census is corroboration only.

## Reproducing a row

```sh
tools/fingerprint_elf.py SLUS_204.69                       # ELF or MWo3 file
tools/unwrap_image.py SLUS_209.15 --out work/ && \
tools/fingerprint_elf.py work/SLUS_209.15.image            # stub: unwrap first; the sidecar is read automatically
tools/fingerprint_elf.py GX/AEE.BIN                        # MWo3 overlay, header parsed in place
tools/fingerprint_elf.py IOP/SSD.IRX                       # reports an IOP module and stops
```

`unwrap_image.py` round-trips MGS3: the inflated image hashes
`1752aef1…931c`, identical to the independently produced reference image, and
the declared length (0x105284) equals the inflated length.
