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

## Three builds, none with symbols

| build | boot file | `.text` size | note |
|---|---|---|---|
| *Xenosaga Freaks* demo, January 2004 | `EXP\EXP.ELF` on SLPS_253.53 | 0x18df38 | a Japanese Episode II demo with its own `OV01.OVL`, `OV02.OVL` |
| Japan retail, VER 1.02 | `SLPS_253.68` (Disc 2 `SLPS_253.69`, same executable) | 0x18f3a0 | text is inline EUC-JP in the scripts; no `.sbl` banks |
| USA retail, VER 1.00 | `SLUS_208.92` / `SLUS_211.33` | 0x193740 | the target |

That is also the build order (175 main functions changed between the demo and Japan with
the USA keeping Japan's version, 89 between Japan and the USA with Japan still equal to
the demo, 3 against the order). Same compiler, SDK 2.7, CRI stamps of 23 January 2004 and
VU microcode in all three; the same 66 section headers; no `.symtab`, no `gcc2_compiled.`.
`find_symbols.py` over all five images finds named symbols only in two IOP modules (below).

- **Siblings check maps and rips, not luck.** `ledger.py sibling` judges every ledger row
  against the demo: 141 of 141 identical, about 20 s. Japan retail gives 141 of 141 too,
  and it has to: 96 % of Japan's main code equals the USA's once relocated fields are
  masked, the ledger's functions among it. A second build can prove a map or a rip wrong;
  it cannot tell a lucky match from a real one in code the builds share.
- **Where the builds differ is information.** About 185 functions changed from Japan to
  the USA (localisation, a reworked font renderer). The USA adds a 17.7 KB debug test menu
  (`0x1e9740`–`0x1edcb0`, its table at `0x2bd3e8`), asserts in the elevator natives, three
  battle natives and the `.sbl` text-bank loader (`FUN_00a9f238` in OV01 has no Japanese
  counterpart). Japan has an assert-free build of the elevator TU to match against.
- **Layout differences carry no TU signal.** Between two builds the 85 steps in the
  address delta fall no closer to TU edges projected from Episode I than random placement
  would. Line-number constants do move (one `__FILE__` family shifts by +15, +14 and +21
  across 22 functions), which is weak evidence of insertions within one file.

## Link layout

Main `.text`, in order (boundaries accurate to the function, not proven to the byte):

| region | text range | functions | bytes | contents |
|---|---|---:|---:|---|
| sdk | 0x120000–0x138300 | 509 | 94 KB | crt0, kernel, SIF, fileio, GS/VIF packets, pad, cdvd, mc, ipu, mpeg |
| korg-ee | 0x138300–0x13b890 | 90 | 13 KB | the EE client of the KORG sound driver: RPC stubs, one per IOP routine |
| cri | 0x13b890–0x194638 | 2,339 | 338 KB | Sofdec, AC3 conversion, ADX, CVFS |
| **game** | **0x194638–0x2a0d30** | **3,527** | **1,039 KB** | control entry, Episode I's xgl library, game, the `cfe` script VM, xenoActor, the Ti\* engine |
| runtime | 0x2a0d30–0x2b3740 | 212 | 76 KB | libdma, fdlibm, libgcc, newlib |

About 4,740 in-scope EE functions plus the IOP driver's 239: two thirds of Episode I.

**Every data section orders the groups differently from `.text`**: control entry, then
game proper, then xgl, then the engine tail (from TiCameraSolver on), then the libraries,
then the runtime. Episode I has the same scheme piece for piece (an ordered `.text` list in
the linker script while data follows input-file order, *inferred*), so per-section order
lists describe both. Data order cannot be assumed to follow text order when inferring TUs.

The overlay mechanism is Episode I's too: one ELF per overlay, all at one address, the load
addresses chained, and `ov02` embedded in the main ELF.

## How much of it is shared

Opcode-shape similarity (5-instruction shingles, registers and immediates dropped, a pair
counted at 0.7 or more with at least 8 instructions; about 3 % of functions "match" by
chance):

| region | functions | Episode I relative | Episode III relative | neither |
|---|---:|---:|---:|---:|
| game (main) | 3,194 | 7 % | 12 % | **84 %** |
| OV01 | 1,016 | 4 % | 5 % | **92 %** |
| OV02 | 17 | 34 % | 5 % | 61 % |
| sdk | 488 | 70 % | 47 % | 24 % |
| cri | 1,804 | 1 % | 63 % | 37 % |
| runtime | 195 | 93 % | 96 % | 0 % |

(shares of each region's bytes). Reused Episode I C is a bootstrap, not the bulk.

- Episode I's **xgl library is intact and in Episode I's order** at 0x194950–0x1ab134
  (timer, font, dma, pad, task, menu, cd, mc, flags, jpeg, hdd). 32 Episode I TUs have runs
  of two or more matched functions in main, 12 in OV01, and OV02 holds Episode I's `logo`
  and `title_hdd_install`.
- A first count said 1,600 functions were shared with Episode III. Most of those rows are
  libraries, which no control game without CRI can subtract: 893 CRI, 157 SDK, 174
  runtime, **350 game**. The game code shared with Episode III sits at the end of the game
  region, from 0x28b570 on (129 of 261 functions there score 0.7 or more).
- VU microcode is Episode III's lineage: 80 % of `.vutext` is in Episode III's, none in
  Episode I's. Of 46 `.DVP.overlay` sections only 14 hold data; the "identical" rest are
  zero-filled. The section names (`.DVP.overlay..<vma>.<file id>.<line>.<n>`) group VU
  programs by source file.

## Finding TUs in a stripped binary

Calibrated on Episode I, whose exact map is known, to see which signals survive stripping:

- **Layout carries none.** Gaps between functions are 0 or 4 bytes inside TUs (2,115 /
  2,007) and at TU boundaries (199 / 199); TU starts are 8-aligned like any function.
- **References are precise and sparse.** A shared data address or an only-caller relation
  within 1 KB is same-TU 99 to 100 % of the time, but fusing all such pairs leaves 2,437
  candidate cuts for 183 real ones (recall 0.96, precision 0.07). By section, references
  that stay inside the function's own TU: `.lit4` 902 of 903, `.rodata` 80 %, `.bss` 71 %,
  `.data` 33 %.

So a stripped game's TU map is assembled from anchors and proven by matching: 27
`__FILE__` strings (every function that references one belongs to that TU: 155 functions,
`cfePlugIn` 24, `TiMRPackage` 21, `xenoActor` 19), the Episode I projection, and the TUs
already byte-identical.

## Names the discs do carry

- **`IOP/KORGIVAG.IRX` is unstripped with `.mdebug`**: one source file `korgivag.c`, 239
  functions (174 static), 91 named static variables, built without `-g`. Each RPC stub of
  the 90-function EE client pairs with a server routine in it. `CRI_ADXI.IRX` keeps 275
  functions over 23 source files. `dump_symbols.py --recursive --mdebug-out` exports both.
- **Script natives are registered by name.** The VM lives in OV01 (handler table
  `0xab5fc0`, instruction lengths `0xab5f90`); every call of its registrar `FUN_00a9cdb0`
  passes a class name and a `{name, handler}` table: 17 classes, 268 natives (`runtime` at
  `0x3e6e40`, `talk` `0x3ec868`, `camera` `0x3e7050`, `math` `0x3e71e8`, `bat` in OV01 at
  `0xab60a0`). That gives each handler's address, the game's own spelling of what it does
  and, from the scripts, its argument types. It does not give C names: the handlers stay
  `FUN_` addresses with the strings kept as evidence, and 26 of them were function starts
  Ghidra had missed.
- A call site inside matched code outranks shape transfer: `sceCdDiskReady` is at
  `0x12e638`, not the lookalike the transfer chose at `0x12e440`.
- The font lookup is not Episode I's: character code to cell is `FUN_001979e0`, with an
  extra-kanji list read by `FUN_00197988` (87 entries in Japan, empty in the USA).

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
`ba20cba` (2026-11-10) the contract verify reproduced every recovered function: main 1,987
of 1,987, OV01 655 of 655, OV02 8 of 8, fourteen `-O0` TUs among them. At `33b232f`
(2026-11-16): main 3,370 of 3,372 judged, two differing by two words each and four
unverifiable (a HI16 the object leaves without its LO16), OV01 655 of 655, OV02 8 of 8.
At `30d3405` (2026-11-21): main 3,769 of 3,769 with seven unverifiable, OV01 1,257 of
1,257 with two, OV02 22 of 22.
The upstream's names are the names of record; the first target above is a local side
target now.

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
with the measured totals in the section above.

No release of the game keeps EE symbols (see "Three builds"). Of the 24 immediate-only
ports, the struct definitions need Xenosaga II's layout; `FUN_00154d80` (a 9-instruction
flag test) has resisted 14 forms. The function partition still has gaps (Ghidra covers
94.4 % of main `.text`: 153 missed prologues, 34 overlaps). The CRI region has no donor
(Episode I has no CRI), `KORGIVAG.IRX`'s compiler is unprobed, and OV01's `-G0` awaits a
function that discriminates. Episode III's page has the three games side by side.
