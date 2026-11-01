# Xenosaga Episode III: Also sprach Zarathustra (SLUS_213.89, 2006, Monolith Soft)

Same studio and compiler as Xenosaga I, four years later, stripped. The game
where symbol transfer between titles was first done by hand, and the one the
transfer tool is calibrated against.

## The binaries

| file | what it is |
|---|---|
| `SLUS_213.89` | plain ELF boot executable, stripped |
| `OV01.OVL`, `OV04.OVL`, ... | three ELF overlays at `0xa7f000`, stripped |

## What built it: SCE ee-gcc 2.96 (proven on OV04)

Fingerprint of the boot ELF: `sd` saves in 4,876 of 4,915 prologues (4,566 at
8 bytes); 2,603 float immediates; 497 of 497 `div.s` padded with `nop;nop`;
4-byte `.eh_frame`; `.vutext`; SDK banners libcdvd/libgraph/libkernl/libmpeg
3.0.0.0; twelve build stamps of 9 August 2005; 38 `.euc.c` source names.
OV04 alone: 181 prologues, all `sd` at 8 bytes, 613 float immediates.

Proof: a function of OV04 was byte-matched under 2.96 in the earlier Xenosaga
III work, which also settled that overlay's flags, `-O2 -G0
-fno-strict-aliasing`, from two globals of 8 and 4 bytes addressed absolutely
in retail (`-G0`) and from one load per aliasing-sensitive region that
schedules early without `-fno-strict-aliasing`.

## What the kit learned here

- **Symbol transfer works across titles of one compiler era.** Xenosaga I's
  unstripped functions, with relocatable fields masked, are found verbatim in
  this binary: the 428 matches first found by an ad-hoc script are reproduced
  424 under `--mask strict` and 428 of 428 under `loose` (four differ in one
  `slti` bound each), in 0.7 s. The same idea, applied with an SDK symbol
  database, gave PS2Recomp's scanner 529 of 530 correct SDK names here.
- The `-G0` witness in `triage.py` (a `lui`/`lw` pair on an object of 8 bytes
  or less) came from this game's overlays.
- The `-mcpu=4000` side effects an earlier Xenosaga III kit worked around in
  its assembler output (`break 7` in the upper field, `daddu` in jump-table
  loads) are flags, not retail behaviour: the probe showed the stock 2.10
  assembler emits retail's encodings with no cpu flag or `-m5900`.

## Running the kit on it

```sh
tools/fingerprint_elf.py SLUS_213.89
tools/transfer_symbols.py --donor SLUS_204.69 --donor-symbols symbols/symbols.csv \
    --acceptor SLUS_213.89 --mask loose -o xs1_to_xs3.tsv --splat xs3_names.txt
tools/triage.py --elf SLUS_213.89 --splat xs3_names.txt --summary
tools/verify_decomp.py --src src/ov04/*.c -I include -G 0 --binary OV04.OVL --symbols xs3.csv
```

## Open

Naming beyond the SDK and shared-engine functions needs the game's own work;
the kit's part (transfer, triage, verify against any ELF with a symbol table)
is in place. The matching work itself lives in the separate Xenosaga III
project.
