# Final Fantasy X (SLUS_203.12, 2001, Square)

The title that showed the kit's "ee-gcc" bucket has more than one compiler in
it, and the first candidate for a lane built on Sony's own 2000 toolchain.

## The binary

| file | what it is |
|---|---|
| `SLUS_203.12` | plain ELF boot executable, stripped, 4.6 MB, `.vutext` (VU microcode) in the ELF |

## What built it: SCE ee-gcc, the 2.9-ee-991111 family (lead)

Fingerprint: `sd` saves in 5,375 of 5,421 prologues, but at a **16-byte
stride** (5,339 `sd/16` against 28 `sd/8`); 1,473 float immediates; 565 of 567
`div.s` and 39 of 39 `sqrt.s` padded with `nop;nop`; SDK banners libcdvd
2.3.4.0, libgraph 2.3.0.0, libkernl 2.3.4.0; build stamps of 2 June, 25 and 30
October and 2 November 2001; 40 source names (`tklib.c`, `rcBBox.c`, ...).

Why that reads as the 1999 build: the compiler-identification rig only ever
saw `sd` at a 16-byte stride from `ee-gcc 2.9-991111`; 2.96 and 3.x save at 8.
The `div.s` padding then narrows it: that `nop;nop` entered SCE's compiler with
the `-01` patch of June 2000, so the compiler is 991111-01 or a sibling, not
the original November 1999 build. Sony's SDK 2.0 libraries, built with exactly
991111-01, show the same shape (`sd` at 16, padded `div.s`).

## What the kit has for it

- **Sony's own compiler, staged and probed.** The Tool Chain 2.0 disc's
  `cc1` 2.9-ee-991111-01 and `ee-as` 2.9-ee-991111 reproduce Sony's shipped
  `libvu0.o` byte for byte and have measured behaviour rows in
  `../TOOLCHAIN-DIFFERENCES.md` §F (literals round to nearest, loops padded to
  five by the assembler, hazard nop present). This is the pair to try FFX
  functions against first.
- **Names.** Xenosaga I as a donor names 196 functions (129 of them `sce*`);
  Sony's SDK 2.0 libraries name 58 more at high confidence and 34 at medium
  (`sdk_donor.py`, strict masking). The medium rows are the same objects with
  a constant changed between SDK 2.0 and the 2.3 the game shipped with.

## Running the kit on it

```sh
tools/fingerprint_elf.py SLUS_203.12
tools/sdk_donor.py --out-elf sdk20.elf --out-csv sdk20.csv /path/to/sce/ee/lib
tools/transfer_symbols.py --donor sdk20.elf --donor-symbols sdk20.csv --acceptor SLUS_203.12 -o ffx_sdk.tsv --splat ffx_names.txt
tools/transfer_symbols.py --donor SLUS_204.69 --donor-symbols symbols/symbols.csv --acceptor SLUS_203.12 -o ffx_xs1.tsv
tools/triage.py --elf SLUS_203.12 --splat ffx_names.txt --summary
```

## Open

No function has been matched yet, so the compiler is a lead. The two things
that would settle it: a verify lane that drives the 991111-01 pair (its
binaries abort inside `exit()` on a modern glibc after writing complete
output; the probe tolerates that, the verifier does not yet), and a first
byte-identical function under it. A later SDK's libraries (2.3) would raise
the named count.
