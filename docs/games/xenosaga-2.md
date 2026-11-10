# Xenosaga Episode II: Jenseits von Gut und Böse (SLUS_208.92, 2004, Monolith Soft)

The middle episode, stripped, built between the two the kit already knew. The game the
target manifest, the ledger and the cross-game port check were built on: the first title
where another game's decomp C was compiled and proven byte-identical against it.

## The binaries

| file | what it is |
|---|---|
| `SLUS_208.92` | plain ELF boot executable (Disc 2's `SLUS_211.33` is byte-identical); stripped of symbols but its section headers survive, `.DVP.ovlytab` and `.DVP.overlay.*` VU overlay sections included; embeds a copy of `ov02` at 0xa80000 |
| `OV01.OVL` | ELF overlay at 0xa80000 (0x6d088 bytes): the battle code |
| `OV02.OVL` | ELF overlay at 0xa80000 (0x3e28 bytes), byte-identical to the SLUS's embedded `ov02` |

Ghidra 12.0.4 with ghidra-emotionengine-reloaded counts 6,625 functions in the SLUS, 1,188
in OV01 and 21 in OV02, every one decompiled but one; shape transfer from Xenosaga I adds
59 boundaries Ghidra merged.

## What built it: SCE ee-gcc 2.96 (proven)

Fingerprint: `sd` saves in 3,792 of 3,829 prologues (3,477 at 8 bytes); 1,761 float
immediates; 494 of 494 `div.s` padded with `nop;nop`; 4-byte `.eh_frame`; `.vutext`; SDK
banners libcdvd 2.7.1.0, libgraph 2.7.0.0, libkernl 2.7.1.0, libmpeg 2.7.0.0, libmrpc
2.7.0.0 (between Xenosaga I's 2.4 and Xenosaga III's 3.0); CRI middleware stamped
23 January 2004; 26 `.euc.c` source names.

Proof: Xenosaga I's decomp C (OpokXeno/xsg-i-decomp), compiled with that project's contract
toolchain and nothing changed, reproduces Xenosaga II. `port_check.py` over the 28 Xenosaga I
TUs whose functions shape-transfer into SLUS_208.92 placed 159 functions: 134 identical with
only relocated fields masked, 24 differing only in I-type immediates (struct offsets and
constants Xenosaga II changed), 1 size difference (two statics of one name), 0 other. With
the functions brought into a target manifest and every relocation applied from a harvested
symbol table, 136 are **Tier A** (byte-identical, nothing masked), re-proved by
`ledger.py all`. Five more, written from scratch for functions Xenosaga II shares with
Xenosaga III and Xenosaga I does not have, matched on the first compile (141 rows).

The compile contract is Xenosaga I's own for game C: cc1 2.96 patched `realconv + lp7 +
fsv3..fsv12`, the swap-mask la29 GNU as with `-g` (no delay-slot filling), `-fno-builtin`,
`-O2`, `-G` per TU. The kit-default pair without `-g` leaves the 8 inline-asm `MMath*`
functions one word short (the assembler fills the delay slot the original kept). The stock
unpatched pair gets 104 of them.

`-G`: the SLUS addresses small data through `$gp` 5,051 times (`-G8`); four Xenosaga I TUs
built `-G0` (`xgl_task`, `xgl_flags`, `game_over`, `outer_product`) match under either flag
here, their functions touch no small data. OV01 never uses `$gp` (0 of 111,650 words),
consistent with the `-G0` overlays of Xenosaga I and III.

The fingerprint used to print "ee-gcc 3.x" for OV02 alone: 14 prologues, no float constant at
all, and 25 `lwc1` field reads counted as constant-pool loads. That rule is gone. The
float-constant test now compares immediates with all float loads, abstains below 50
prologues or 100 float loads (OV02 reads 2.96, low confidence, `float_constants: abstains`),
and the stripped Xenosaga I Reloaded, which the old rule also called 3.x, reads 2.96.

## What the kit learned here

- **A cross-game port check is cheap and strong.** One compile of another game's decomp
  separates shared source (identical) from shared source with a changed layout (immediate)
  from different code, and the relocated fields of the identical functions name the second
  game's symbols: 220 names from 158 functions, 218 with one address, 98 confirmed by more
  than one site. The two conflicts are structs whose field offsets moved between the games.
- **Relocation tables are not in ABI order.** The `-g` assembler lists a HI16 after LO16s of
  later functions; pairing `%hi` with the next `%lo` in table order paired across functions.
  Target mode pairs by instruction offset, and the harvest does too.
- **Initialised data can be proven by content.** A string, a float or a table the original
  references is accepted when the original holds the same bytes there; nothing has to be
  named by hand for `.rodata`, `.lit4`, `.data` or `.sdata`.
- **Shape transfer as a cross-reference.** Xenosaga III's functions masked-match 1,600 Xenosaga
  II functions; the five written from scratch reproduce Xenosaga III's copies too (four
  identical, one with a moved struct offset).

## Running the kit on it

```sh
export DECOMP_TARGET=/path/to/xenosaga2-decompiled/decomp-target.json EE_ROOTFS=~/ee-rootfs
tools/verify_decomp.py --target $DECOMP_TARGET --unit main --compiler contract --out report.json
tools/ledger.py all
tools/match_kit.py init FUN_00154d80
tools/port_check.py --upstream ~/src/xsg-i-decomp --map xs1_to_xs2.tsv --acceptor SLUS_208.92 \
    --compiler contract --contract-name ee-tu-c296-G8-g --harvest xs2_harvest.csv
```

## Opok's decomp (OpokXeno/xsg-ii-decomp) through the kit

The public decomp is a splat tree with estimated function boundaries and no symbol table.
`tools/upstream_target.py` turns its `config/` into a symbols CSV and a target manifest that
lives outside the checkout (`symbols/xs2-symbols.csv`, `symbols/xs2-target.json`); the MCP
registration `ps2decomp-xs2` uses them (`mcp/README.md`, "Xenosaga II"). At its commit
`ba20cba` the contract verify reproduces every recovered function the kit can judge: main
1,985 of 1,985 (2 unverifiable), OV01 655 of 655, OV02 8 of 8, fourteen `-O0` TUs among them.

```sh
tools/upstream_target.py --upstream ~/src/xsg-ii-decomp --game-dir ~/xs2/originals \
    --symbols-out symbols/xs2-symbols.csv --target-out symbols/xs2-target.json
export DECOMP_TARGET=$PWD/symbols/xs2-target.json DECOMP_UPSTREAM=~/src/xsg-ii-decomp \
       DECOMP_GAME_DIR=~/xs2/originals DECOMP_TOOLCHAINS=~/toolchains EE_ROOTFS=~/ee-rootfs
tools/verify_decomp.py --target $DECOMP_TARGET --unit main --compiler contract
tools/match_kit.py init Resigne --compiler contract
```

## Open

Two targets exist. The first, described above, lives with the Xenosaga II project (its
symbol table, manifest and ledger are not in this repository). The second is the upstream
decomp, OpokXeno/xsg-ii-decomp: `tools/upstream_target.py` writes its manifest and symbol
CSV (`symbols/xs2-target.json`, `symbols/xs2-symbols.csv`) from the checkout's own config,
and at upstream `ba20cba` the contract compiler reproduces main 1987/1987, OV01 655/655 and
OV02 8/8 functions byte for byte.

No release of the game keeps EE symbols: `tools/find_symbols.py` over the USA and Japanese
discs and the *Xenosaga Freaks* demo finds named symbols only in two IOP modules
(`KORGIVAG.IRX`: 239 functions, 91 named statics, one source file; `CRI_ADXI.IRX`: 275
functions over 23 source files), which `dump_symbols.py --recursive --mdebug-out` exports. Of the 24 immediate-only ports, the struct definitions need Xenosaga II's
layout; `FUN_00154d80` (a 9-instruction flag test) has resisted 14 forms.
