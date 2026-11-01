# Silent Hill 2 (SLUS_202.28, 2001, Konami KCET / Team Silent)

The first confirmed CodeWarrior title in the corpus, the one that broke the
"`sq` saves mean SN ProDG" rule, and the game whose 54 overlays taught the kit
the Metrowerks overlay record.

## The binaries

| file | what it is |
|---|---|
| `SLUS_202.28` | plain ELF boot executable, stripped, 3.0 MB; 53 program headers, of which 51 are zero-filesize BSS slots reserving the overlay area |
| `GX/*.BIN`, `GY/*.BIN`, `GZ/*.BIN` | 54 stage code overlays in Metrowerks' `MWo3` record, loaded at `0x1f0bc00` (three placeholder stages `XXX`/`YYY`/`ZZZ` at `0x1e08700`); overlay IDs 1 to 51 |

Each overlay's header gives its load address, text and data sizes, bss size
and the bounds of its static-initializer table (empty: these are C overlays);
the file ends where `.bss` begins. `unwrap_image.py` reads them with the field
names from Metrowerks' own runtime source (`../CODEWARRIOR.md` §2).

## What built it: Metrowerks CodeWarrior for PlayStation 2 (identified)

Fingerprint: `.comment` reads `MW MIPS C Compiler (2.4.1.01)` / `PlayStation2`
(a string constant across CodeWarrior for PS2 R3.0-3.04, so it dates the title
to that product, not to a build); `sq` saves at a 16-byte stride in 2,100 of
2,514 prologues; only 70 of 753 `div.s` carry SCE gcc's `nop;nop` padding, and
those are the SDK; SDK banners libcdvd 2.2.4.0, libgraph 2.2.0.0, libkernl
2.2.4.0. The entry point is Sony's `crt0` shape (bss clear, SetupThread,
SetupHeap): Konami linked Sony's startup under CodeWarrior, which is also what
CodeWarrior for PS2's own stationery does.

The 414 prologues that save with `sd` are the Sony libraries: Sony's SDK 2.0
objects name 85 of them at high confidence and 95 at medium (`sdk_donor.py`).

## What the kit learned here

- **The census is corroboration only.** By save style alone this is an SN
  ProDG game; the `.comment` says Metrowerks. Vendor strings come first.
- **The `MWo3` record.** Measured on all 54 overlays (file size always equals
  header plus text plus data; word 5 the bss size; words 6 and 7 equal and
  pointing at the end of the file), then confirmed from CodeWarrior for PS2
  R3.0's `mwUtils_PS2.c` and its LCF generator.
- **A compiler-family gate.** The MCP server refuses to open a match sandbox
  on an `sq`-prologue function here, because the kit's lanes compile with
  ee-gcc and no form could match; the `sd` minority still opens.
- The R3.0 release notes say the compiler switched the return-address save
  from `sq`/`lq` to `sd`/`ld`; this 2001 title predates that, and its
  prologues save everything with `sq`.

## Running the kit on it

```sh
tools/fingerprint_elf.py SLUS_202.28
tools/fingerprint_elf.py GX/AEE.BIN                       # one overlay, header parsed in place
tools/unwrap_image.py GX/AEE.BIN --describe
tools/transfer_symbols.py --donor sdk20.elf --donor-symbols sdk20.csv --acceptor SLUS_202.28 -o sh2_sdk.tsv
tools/cw_run.py --tools ~/cwps2/3.0 --as-of 2002-06-01 -- mwccps2 -c -O2 -prefix PREFIX_PS2.h -I <PS2_Support> -o x.o x.c
```

## Open

A judged CodeWarrior lane. `mwccps2` from R3.0, 3.01 and 3.04 now compiles on
this machine (`../SDK-ARCHIVE.md` §4) and produces the `sq`/16 prologues seen
here; what remains is a verifier that drives it and byte-compares, and the
question of which release (and which flags) Team Silent used in 2001, which a
first matched function will answer. The compiler's own release notes are the
first place to look when a diff is one instruction off.
