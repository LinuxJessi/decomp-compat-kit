# Yakuza (SLUS_213.48, 2006, Sega / Amusement Vision)

The second CodeWarrior title, packaged unlike anything else in the corpus: a
tiny loader executable and the whole game as one Metrowerks overlay record.

## The binaries

| file | what it is |
|---|---|
| `SLUS_213.48` | a 354 KB loader ELF, stripped, `.comment` `MW MIPS C Compiler (2.4.1.01)`; Sony SDK 3.0.0.0 banners |
| `MAIN.BIN` | the game: a 4.4 MB `MWo3` record with overlay ID 0 (the root program), loaded at `0x100000`; text 0x2e0d20, data 0x147420, bss 0x9b500, a static-initializer table of 42 entries (a C++ title); 131 source names (`Map.cpp`, `Item.cpp`, `YAct.cpp`, `Root.cpp`, `Media.cpp`); build stamps of 28 February and 2 August 2005 |

The loader's entry zeroes every general register with `padduw`, then the hi/lo
pairs, the shift amount and all 32 FPU registers, before the usual SetupThread
and SetupHeap sequence. That is the register-zeroing habit of CodeWarrior for
PlayStation's `_psstart.c`; CodeWarrior for PS2 R3.0x stationery links Sony's
`crt0.s` instead, so this startup is a later runtime's or Sega's own.

## What built it: Metrowerks CodeWarrior for PlayStation 2 (identified)

The loader carries the `.comment`; `MAIN.BIN` is the `MWo3` container. Census
of `MAIN.BIN`: `sq` saves in 3,640 of 5,523 prologues, `sd` in 1,844 (1,771 at
8 bytes): a larger ee-gcc minority than Silent Hill 2's because the game links
CRI middleware as well as Sony's libraries. 163 of 881 `div.s` are padded,
again the minority. The loader itself is `sd`-dominant with a CodeWarrior
`.comment`: Metrowerks emits `sd` for small frames too.

## What the kit learned here

- The container and the vendor string decide, not the census ratio.
- The `MWo3` record's overlay ID 0 is the root program, exactly as the
  Metrowerks manuals reserve it; the two trailing addresses in the header are
  the static-initializer table bounds (0xa8 bytes apart here, 42 words), which
  the kit first read from Metrowerks' generator and then from its runtime
  source.
- The register-zeroing entry is a startup tell reported with its caveat, not a
  compiler tell.

## Running the kit on it

```sh
tools/fingerprint_elf.py SLUS_213.48                      # the loader
tools/fingerprint_elf.py MAIN.BIN                         # the MWo3 record, header parsed in place
tools/unwrap_image.py MAIN.BIN --describe
tools/transfer_symbols.py --donor sdk20.elf --donor-symbols sdk20.csv \
    --acceptor MAIN.BIN --acceptor-base 0x100000 -o yakuza_sdk.tsv
```

## Open

The same CodeWarrior lane Silent Hill 2 needs, plus the question of which
CodeWarrior release Sega used in 2005 (the compiler's R3.0 change to `sd`/`ld`
for the return address is a visible tell), and the SDK 3.0 libraries as a
donor for the Sony minority. The game's own data formats (ROFS, AVLZ, TLFD,
TXBP) are documented in the author's separate extractor, not here.
