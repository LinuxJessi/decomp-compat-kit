# Xenosaga Episode I: Der Wille zur Macht (SLUS_204.69, 2002, Monolith Soft)

The kit's reference target: the game the verify and match lanes were written
for, the one whose compiler is proven rather than fingerprinted, and the only
one whose symbol table ships in this repository.

## The binaries

| file | what it is |
|---|---|
| `SLUS_204.69` | plain ELF boot executable, **unstripped**: 4,636 FUNC symbols in `main` |
| `OV01.OVL`, `OV10.OVL`, `OV11.OVL`, `OV12.OVL`, ... | five ELF overlays loaded at `0xa00000` (ELFs despite the extension), unstripped; OV11 has 139 FUNC symbols |
| `IOP/SSD.IRX`, `IOP/RSSD.IRX` | IOP sound modules: ELF type `0xff80`, `.iopmod`, R3000 code; 442 FUNC symbols in SSD. None of the EE heuristics apply |

Six binaries together carry 15,132 symbols, exported with `dump_symbols.py`
into `symbols/symbols.csv` and, in splat's form, `symbols/splat/*.txt`.

## What built it: SCE ee-gcc 2.96 (proven)

Fingerprint: 830 `gcc2_compiled.` markers, 46 old-GNU mangled names and zero
Itanium ones; 1,487 float constants formed as `lui`+`mtc1` immediates (the
2.96 idiom); `sd` saves in 2,373 of 2,435 prologues (2,118 at an 8-byte
stride); every `div.s` (487 of 487) preceded by the `nop;nop` SCE's 2000 patch
introduced; a 4-byte `.eh_frame` (`-fno-exceptions`); `.vutext` in the ELF;
SDK banners libcdvd 2.4.2.4, libgraph 2.4.0.0, libkernl 2.4.2.0; a build stamp
of 5 September 2002; source names such as `../Undulate.euc.c` (EUC-JP sources).

Proof: the upstream matching decomp's C, compiled with the stock, unpatched
2.96 `cc1` from the decompme archive, is byte-identical for 817 of 857
functions (first run, September 2026) and 2,037 of 2,459 (whole `main`, 30
October 2026), with every difference but a handful in the five classes the
project's documented toolchain patches predict. Flags: `-O2`, `-G8` for most
of the main executable, `-G0` for a minority of TUs and for the overlays,
`-fno-builtin` from the driver (`../TOOLCHAIN-DIFFERENCES.md` A10).

## What the kit learned here

- The whole catalogue of stock-versus-retail toolchain differences
  (`../TOOLCHAIN-DIFFERENCES.md` A1-A11, B1-B11): literal rounding, short-loop
  padding, hazard nops, alias analysis, `la` and `vsqrt` encodings, delay-slot
  filling.
- The matching discipline and forty-odd codegen idioms, each tied to the
  function that proved it (`../MATCHING-PLAYBOOK.md`).
- The verifier's resolution of section-relative references (file-local
  statics, `.lcomm` NOTYPE symbols, `$gp`-relative data, jump tables): when the
  project moved its data into the C files, every such TU became unverifiable
  overnight until those were handled.
- Triage of the main unit: 544 trivial, 1,642 easy, 1,540 medium, 596 hard,
  239 cop2, 75 huge; OV11 shows zero `$gp` use and 37 absolute-addressed small
  objects, a `-G0` overlay.
- As a donor: its unstripped functions name 428 in Xenosaga III and 196 in FFX
  (`transfer_symbols.py`); as an acceptor, Sony's SDK 2.0 libraries name 49 of
  its functions, all of them agreeing with its own symbol table, which is the
  control for `sdk_donor.py`.

## Running the kit on it

```sh
tools/fingerprint_elf.py SLUS_204.69
tools/triage.py --elf SLUS_204.69 --symbols symbols/symbols.csv --summary
tools/verify_decomp.py --upstream ~/src/xsg-i-decomp --out report.json          # stock lane
tools/verify_decomp.py --upstream ~/src/xsg-i-decomp --compiler contract --toolchains DIR
tools/match_kit.py init <function> --forms 30                                   # one-function loop
```

The MCP server defaults (`DECOMP_BINARY=SLUS_204.69`, `DECOMP_UNIT=main`) are
this game's; set `DECOMP_UNIT=ov01 DECOMP_BINARY=OV01.OVL` for an overlay.

## Open

The 30 functions of the October run outside the known classes (three
`xglDma*` setup functions and a dozen large ones a few words apart) are where
the stock compiler schedules differently from the project's patched one; they
are the reading list, not evidence of wrong C. The upstream project is
[OpokXeno/xsg-i-decomp](https://github.com/OpokXeno/xsg-i-decomp).
