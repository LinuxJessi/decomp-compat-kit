# Xenosaga Episode III: Also sprach Zarathustra (SLUS_213.89, 2006, Monolith Soft)

The last episode: same studio and compiler as the first two, stripped in every build we
hold. This page collects what was known before its decomp started (written 2026-11-21, the
day after OpokXeno/xsg-iii-decomp went public): the binaries, the three builds, the
compile line with its per-file flags, the link layout, where the code overlaps Episodes I
and II, and the addresses the data-format work already pinned down.

Status words as in the index: **proven** (compiled and byte-identical), **measured** (a
tool printed it), **lead** (favoured, not proven), *inferred* (a reading of the evidence).

## The binaries

| unit | file | bytes | SHA-256 | what it is |
|---|---|---:|---|---|
| main | `SLUS_213.89` (Disc 2's `SLUS_214.17` is byte-identical) | 4,513,232 | `bec09b76…46d99c` | plain ELF, stripped, 63 section headers kept; entry `0x120008`, game `main` at `0x181af0`, `_gp` `0x5571f0`; embeds `ov01` as a LOAD segment at `0xa80000` (0x5570 bytes) |
| ov01 | `OV01.OVL` | 26,120 | `22a13c81…f9c18d` | title, logo and credits (`mnu\credit.bin`, `titlebg1.sfd`); the copy inside the SLUS is the same code |
| ov02 | `OV02.OVL` | 303,468 | `806e81fe…7689b3` | the battle system (script verbs, AI `think`, `robo_menu.euc.c`, a test-play CSV logger) |
| ov04 | `OV04.OVL` | 263,996 | `63091079…197788` | the HaKox minigame (`mg1\…`), with its pad cheat codes and debug pages intact |

There is no `OV03.OVL`. The three overlay files and the IOP directory are identical on both
discs. Each overlay is a small ELF with one PROGBITS section (`ov01`, `ov02`, `ov04`) at
**`0xa80000`, file offset `0x1000`**; OV02 and OV04 map the file from offset 0, so their
LOAD segment starts at `0xa7f000`, which is the base older notes quote. All three share
that bank, and the main image calls into it directly: `main` calls `0xa80000` before any
other subsystem, and four thunks plus the heap-window test code call OV02 addresses
(`0xa88608`…`0xa89400`).

Sections of the main image:

```
.text    0x00120000..0x003172f0     .vutext  0x003172f0..0x00319820  (9,520 B, ~28 VU1 programs, 29 XGKICK)
.data    0x00319880..0x00515ae4     .vudata  0x00515b00              (72,896 B)
.rodata  0x00527800..0x0054f200     .sdata   0x00550280..0x00551380
.bss     0x00551600..0x009109c8     ov01     0x00a80000..0x00a85570
```

Function counts differ by who counts. Ghidra 12.0.4 with ghidra-emotionengine-reloaded and
the sibling images mapped in finds 8,626 / 21 / 1,301 / 369 (10,317, none truncated; with
each binary analysed alone 210 functions truncate at calls into the other image). The
upstream's inferred partition has 9,930 in main, of which 5,594 are in scope (game code),
plus 21, 1,364 and 307: **7,286 in-scope functions**.

**No IOP module keeps symbols**, unlike Episode I (`SSD.IRX`) and Episode II
(`KORGIVAG.IRX`): `CRI_ADXI`, `IOPSUB` (Monolith's own driver, 2.7 KB), `LIBSD`, `MCMAN`,
`MCSERV`, `PADMAN`, `SDRDRV`, `SIO2MAN` and `SNDFI` each have the one-entry table.

## Three builds, none with symbols

| build | boot file | built | SHA-256 | note |
|---|---|---|---|---|
| Japan retail, VER 1.01 | `SLPS_256.40` (Disc 2 `SLPS_256.41`, same executable) | `Feb 21 2006` | `b82b6744…a54ec9` | 4,505,232 bytes |
| "FOR PRESS ONLY" prototype | `SLUS_213.89` (same serial as retail) | `May 22 2006 13:26:49` | `1c3d028a…bd08dd` | US localisation in progress; Disc 1 content only |
| USA retail | `SLUS_213.89` / `SLUS_214.17` | `Jul 12 2006 13:22:53` | `bec09b76…46d99c` | the target |

`find_symbols.py` over the USA Disc 1 and Japan Disc 1 images: 29 MIPS ELF headers each,
none outside a listed file, none with a named symbol, `.mdebug` or stabs. An earlier
byte-level search of the prototype image, ISO gaps and container slack included, found
the same. Names come from reconstruction and from the other episodes only.

The same toolchain built all three: the Sony banners, all 31 CRI version strings, the
`Append: GCC2096 SCE3020` tag and the IOP set are identical in the prototype and retail.
What the siblings are good for:

| US function found in | main | OV01 | OV02 | OV04 |
|---|---|---|---|---|
| prototype | 6,138 of 6,167 | 14 of 15 | 888 of 894 | 245 of 274 |
| Japan retail | 6,073 of 6,167 | 11 of 15 | 888 of 894 | 190 of 274 |

(`transfer_symbols.py --sibling`, functions of 64 bytes or more, relocated fields masked,
about 2 s per unit.) A word-level diff of the prototype against retail is finer: 8,599 of
8,626 main functions differ only in relocated fields, 28 changed (movie, intermission and
disc-swap flow; three proportional text-width routines rewritten for Latin text; a
memory-card sync drain; a clamp fix), none added or removed. No SDK or middleware function
changed, and `.vutext`, `.vudata` and every `.DVP.overlay` section are identical.

- **A pointer-or-constant oracle.** Between prototype and retail, text moves by up to
  0x240 and `.sdata` by exactly 8. A data word or `lui`/`lo` pair that moves by the build's
  shift is an address; one that stays is a constant.
- **A second compile of the same source** for the changed functions: one gained a
  parameter, visible as a signature change plus its two callers.
- **A sibling check on every match** (`ledger.py sibling`): 99.7 % of main is the same
  code, so a match that fails in the prototype either changed between builds or was luck.
  As on Episode II it cannot catch a lucky match in code the builds share.
- **OV04 moved its structs**, which the seven matched functions show: the HaKox state
  struct is at `0xaa9320` in retail and `0xaa8ee0` in the prototype, with ten field offsets
  lower by 2 to 16 and a slot count of 114 instead of 126. Same C, another header.
- The prototype has **no debug feature retail lacks**: 69 `host0:` paths, the
  `CF Debug Flags` table, the test scenes and viewers are in both and their code is
  identical. OV04's extra 1.1 KB in retail is localisation (table-driven message
  positions), not a debug strip.

## What built it: SCE ee-gcc 2.96 (proven on all four units)

Fingerprint of the boot ELF: `sd` saves in 4,876 of 4,915 prologues (4,566 at 8 bytes);
2,603 float immediates; 497 of 497 `div.s` padded with `nop;nop`; 4-byte `.eh_frame`;
`.vutext`; SDK banners libcdvd/libgraph/libkernl/libmpeg 3.0.0.0; twelve CRI build stamps
of 9 August 2005; 38 `.euc.c` source names in main and 23 in OV02. OV02: 809 of 815
prologues `sd`, 38 of 38 `div.s` padded. OV04: 181 of 181 `sd` at 8 bytes, 104 of 104
padded. OV01 is too small to tell 2.96 from 3.x alone (15 prologues) and reads with the
executable it links against.

Proof, twice over:

1. **Seven functions of OV04**, written from scratch in the earlier Episode III work
   (the 280-instruction HaKox stage-select handler with its string and two jump tables,
   and six neighbours), byte-identical with every relocation applied.
2. **The upstream decomp** (OpokXeno/xsg-iii-decomp at `3a26737`, 2026-11-21: 1,135 of
   7,286 functions recovered, about 1,100 of them carried over from Episode II). Through
   the kit's contract lane: OV01 7 of 7, OV02 91 of 91, OV04 23 of 23, main 1,008 of 1,014.
   The six that differ are one finding, below.

### The compile line, and two flags that are per file

The contract is Episode I's, unchanged: cc1 2.96 patched `realconv + lp7 + fsv3..fsv16`,
the swap-mask la29 GNU as with `-g` (no delay-slot filling), `-fno-builtin`, `-O2`.
Two flags vary by translation unit, and both were measured here:

**`-G`.** Main addresses small data through `$gp` in 6,167 instructions (4,993 loads,
1,146 stores, 28 `addiu`); the three overlays never do (0 of 2,104, 56,890 and 37,154
words), so every overlay TU is `-G0`, as in Episodes I and II. In main the upstream's
calibration assigns `-G8` to 235 C TUs and `-G0` to 142. One assignment is contradicted by
the bytes: `main/tu334` (`TiDynamicsColl.c`) is recorded `-G0`, and six of its fifteen
recovered functions come out 4 to 20 bytes long that way (an 8-byte accessor becomes 12:
the `lui` an absolute address needs). At `-G8` the TU is 15 of 15. The other nine match
under either flag, which is how a wrong `-G` survives: only a function that touches a
small global discriminates.

**`-fno-strict-aliasing`.** The HaKox handler needs it: without it one `lhu` of a struct
field is scheduled seven instructions early (at `0xa91b90` instead of `0xa91bac`) and the
function is a reorder of the original; with it, 280 of 280. The upstream contract does not
pass it, and that is right for main: with the flag added to every TU, **59 of main's 1,014
recovered functions stop matching** (`xgl_font`, `xgl_dma`, the JPEG coder, `sef`, `sv`,
`TiWatSim`, `TiWater`, `xac_eval`, the drop-shadow and `XenoActor` TUs, …), while all 121
recovered overlay functions match either way. So main's TUs, or at least those, were built
with the default strict aliasing and OV04's HaKox TU without it. Treat it like `-G`: a
per-TU property, to be recorded in the TU (`@cflags`, or the flags of the upstream's
compile config) the first time a function discriminates. No recovered OV01 or OV02
function discriminates yet.

The seven OV04 functions under the four lanes available:

| lane | result |
|---|---|
| the Episode III project's own (unpatched cc1, decomp.me `ee-as -mcpu=4000`, two text fixes) | 7 of 7 |
| upstream contract + `-fno-strict-aliasing` | 7 of 7 |
| upstream contract as configured | 6 of 7: HaKox is a reorder |
| kit stock (unpatched cc1, stock 2.10 as) | 6 of 7: HaKox is 8 bytes short (hazard nops the stock assembler does not write) |

The two text fixes of the first lane (`break 7` in the upper field, `daddu` in jump-table
loads) are side effects of `-mcpu=4000`, not retail behaviour: the probe showed the stock
assembler emits retail's encodings with no cpu flag or `-m5900`.

### What 2.96 does here (from the OV04 matches)

- Basic blocks are emitted in source order. A case body that sits after other code in the
  layout was reached by `goto` in the source; the surviving copy of a cross-jumped tail is
  the physically last one.
- A float constant with three or more uses across calls lives in a callee-saved FP
  register; fewer, and it is rematerialised. A `swc1 $f20` that disappears from the
  prologue means calls were merged or split.
- `p->x = p->y = 0.0f;` routes the zero through an FP register (`mtc1 $zero`, `swc1`);
  two separate statements store integer `$zero` (`sw`).
- Ghidra re-sorts independent stores and prints float arguments first (`$f12`). One vector
  clear matched only in x, y, z, w order; one prototype was buffer first, float second.
- A pointer set in a `for` initialiser is emitted before the loop guard. Retail loads one
  table pointer after the guard and forms the address as `i * 0x80 + base`; `&base[i]`
  allocates a different register.
- Division by a constant is a real `div` with the `beql`/`break 7` zero check.
- Ghidra's function sizes can stop short of the epilogue (the HaKox handler: 1,096 listed,
  1,120 real).

## Link layout

The upstream's zones for main `.text` (coarse and marked provisional there; byte identity
does not depend on them):

| zone | text range | bytes | contents |
|---|---|---:|---|
| crt0 | 0x120000–0x120250 | 592 | Sony's SDK 3.0 startup |
| CRI | 0x120250–0x17ce40 | 380 KB | Sofdec (MWSFD 3.65, SFD 1.966), ADXT 9.61, ADXF 7.36 |
| sound client | 0x17ce40–0x181950 | 19 KB | the EE side of the SNDF driver |
| **game** | **0x181950–0x2e69b8** | **1.43 MB** | control entry, the xgl library, game, `cfe`, xenoActor, the Ti\* engine; an 11.6 KB public library sits inside at 0x190720 |
| SCE SDK | 0x2e69b8–0x303a50 | 119 KB | kernel stubs, libgraph, libdma, libpad, libmc, libcdvd, … |
| runtime | 0x303a50–0x3172e8 | 80 KB | libm, libgcc, newlib |

Episodes I and II link the SDK first; here it follows the game, with CRI and the sound
client ahead. The control entry is again the first game object and xgl follows it.
Whether the data sections reorder the groups as they do in Episodes I and II (control
entry, game, xgl, engine tail, libraries, runtime) has not been measured on this binary;
Episode II's page says why data order must not be assumed to follow text order.

## The three episodes side by side

| | Episode I (SLUS_204.69) | Episode II (SLUS_208.92) | **Episode III (SLUS_213.89)** |
|---|---|---|---|
| SCE libraries | 2.4.x | 2.7.0/2.7.1 (adds libdma, libmrpc, libnet) | **3.0.x (adds libsdr)** |
| `.text` base, size | 0x200000, 1.28 MB | 0x120000, 1.65 MB | **0x120000, 2.06 MB** |
| `.vutext`, `.vudata` | 0, 67 KB | 9.7 KB, 78 KB | **9.5 KB, 73 KB** |
| `_gp` | 0x4dfb70 | 0x6a2df0 | **0x5571f0** |
| overlays (one shared address) | OV01 battle, OV02 boot, OV10–12 minigames (0xa00000) | OV01 battle, OV02 boot (0xa80000) | **OV01 title, OV02 battle, OV04 minigame (0xa80000)** |
| overlay embedded in main | `ov02` | `ov02` | **`ov01`** |
| EE symbols | every unit unstripped | none | **none** |
| `*.euc.c` file names | 1 | 27 | **38 main + 23 OV02** |
| IOP sound | SSD + RSSD, unstripped | KORGIVAG, unstripped with `.mdebug` | **SNDFI, SDRDRV, IOPSUB, stripped** |
| events | Java classes on a JVM in the EE | `cfe` VM, `.sb`, 16-bit opcodes | **`.sb`, byte opcodes** |
| movies | PSS (SCE libmpeg) | CRI Sofdec, MPEG-1 + ADX + AC3 | **CRI Sofdec, MPEG-1 512×320 + ADX** |
| entry point | bss clear first | bss clear first | **29 registers zeroed first (SDK 3.0 crt0)** |

That last row corrected the kit. A run of `padduw rd,$zero,$zero` at the entry was first
met on Yakuza, a CodeWarrior title, and read as a Metrowerks startup. Episode III starts
with the same words (29 registers, `$k0`/`$k1` skipped, then hi/lo, the shift amount and
every FPR) and holds no Metrowerks code; both link SDK 3.0.0. `fingerprint_elf.py` now
reports it as `sce-crt0-regclear` and draws no vendor from it.

## Shared code

- **From Episode I** (unstripped): 428 functions found verbatim with relocated fields
  masked, 424 under `--mask strict` and all 428 under `loose` (four differ in one `slti`
  bound each), in 0.7 s. Lowering the size floor to 24 bytes gives about 500 unique names,
  223 of them beyond the SDK: 71 `xgl*` engine functions (matrix, vector, camera, DMA,
  font, CD archive), JPEG helpers, culling, `sv*` image-list code, nine `sceVif1Pk*`
  helpers. Fifteen land in OV04 (`Get_Distance`, `CalcCrossPoint`, `Get_Rnd`, …). About
  390 more rows are multi-hit twins that need a caller check.
- **From the SDK**: PS2Recomp's SCE symbol database named 613 functions here; the same
  scanner scored 529 of 530 on Episode I with its symbol table hidden.
- **With Episode II**: 1,603 of 5,769 Episode III functions of 64 bytes or more are in
  Episode II under the strict mask, against about 7 % from Episode I. Most are libraries
  (in Episode II's terms 893 CRI, 157 SDK, 174 runtime); 350 are game code, concentrated in
  the Ti\* modules and what precedes them. Episode II C written for those reproduced
  Episode III's copies (four of five identical, one moved struct offset), and the
  upstream's first Episode III commit is that port at scale.
- **VU microcode**: 80 % of Episode II's `.vutext` and 58 % of its `.vudata` are in
  Episode III's; none of Episode I's `.vutext` is.

## What the game's own strings name

- `Ti*`, Monolith's animation and physics toolkit, fifteen modules by `__FILE__`:
  TiCameraSolver, TiMotionSolver, TiDynamicsColl, TiGlobalColl, TiDrawCollision,
  TiDrawJoint, TiMRPackage, TiMtnBlend, TiPartsAnim, TiVu0, TiXacAnimCurve, TiWatSim,
  TiWater, TiWindAnim, TiWindBase. Every function that references a `__FILE__` string
  belongs to that TU.
- `XAct*`, the actor system (`XAct_Rsrc_*` names the resource kinds: `.txy` textures,
  `.pxy` models, `.xhr` skeletons, `.xap` motion).
- CRI's `"<code>: <function>: <message>"` error strings name about 150 middleware
  functions; `nj*` (Sega's Ninja library), `LSC_*` and `SRD_*` (disc I/O with a run-time
  `DVD | HOST` device switch) appear in strings too.
- Test code shipped in retail: `test.euc.c` (a heap printer), `Gtest`, `BlendTest`,
  `CFLLightTest`, `CullingTest`, `ScisTest`, `XstAnimTest`, `EventViewer`, `MotionViewer`.
- The misspelling "Illigal" occurs 22 times, in CRI's errors and Monolith's alike: a good
  grep anchor for error paths.

## Addresses the data-format work pinned down

Format reversing reads the same functions a decomp has to name (`../HISTORY.md`, "Data-
format knowledge is a decomp input"). These are USA retail addresses, each found by
reading the code that parses a format the file kit (LinuxJessi/Xenosaga3PythonExtractor)
now reads and writes. Function addresses stay addresses: none of these has a found name.

| what | where |
|---|---|
| event loop | dispatcher at `0xa81718`; an event word is `[8-bit channel][24-bit function pointer]`, channel 1 and the dispatcher's own address mean "nothing" |
| disc index `X3.00`/`.10`/`.20` (directory records: 3-byte sector offset, 4-byte size) | readers `FUN_00187e28`, `FUN_00187bd8`, `FUN_00187b18`; lookup `FUN_00188578` |
| `Xc` package compression (stored, three LZSS variants) | dispatch `FUN_001b7330`; decoders `FUN_001b7438`, `FUN_001b7480`, `FUN_001b7558`, `FUN_001b7620` |
| movie player | loop `FUN_001b3858`; `FUN_0018bd40(0x200, 0x140)` fixes the 512×320 ceiling; open `FUN_0018b9f8` formats `"%08x.%08x"` for `mwPlyStartFname`; GS upload `FUN_0018b818`; subtitle timing `FUN_001b32f8` |
| text | locale table `FUN_00181c18` (`jpjpusendefriteszhko`); pair gate `FUN_00181e48` (USA: a two-argument whitelist; Japan: lead byte ≥ 0xA0); the text-command interpreter `FUN_00182708` with 18 `$` commands (`ax ay az rx ry init save load col bg shadow alpha zoom xx ruby prop han mark`); message fetch `FUN_00213700`; width `FUN_002d26d0`, `FUN_002d2920`, `FUN_002d2ab8` |
| save and flags | story-flag bank `0x3e48b0` (0x9e0 bytes), flag word `0x3e46b0`, save image `0x4b3740` (0x130b0 bytes), serialisers `FUN_00224a80` and `FUN_001b9638`, pad word `0x4b3498` |
| debug | `CF Debug Flags` table `0x418248` (PKTOVER, NOENCOUNT, DEBUGPAUSE, 60FPS, FASTMOVE, DISPPERF, …); build stamp string `0x5312a0` |
| OV04 | HaKox state struct `0xaa9320` (mode +0x16, page +0x8e, cursor +0x9e, 64 floats at +0x3b70 and +0x3c70) |

## Running the kit on it

Identify, name, triage (no compiler needed):

```sh
tools/fingerprint_elf.py SLUS_213.89
tools/find_symbols.py disc1.iso
tools/transfer_symbols.py --donor SLUS_204.69 --donor-symbols symbols/symbols.csv \
    --acceptor SLUS_213.89 --mask loose -o xs1_to_xs3.tsv --splat xs3_names.txt
tools/transfer_symbols.py --donor SLUS_213.89 --donor-symbols symbols/xs3-symbols.csv \
    --donor-binary SLUS_213.89 --acceptor PROTO/SLUS_213.89 --sibling -o proto_main.tsv
```

The upstream decomp as a target (`symbols/xs3-*` are generated from the checkout and stay
local; `mcp/README.md` has the `ps2decomp-xs3` registration):

```sh
tools/upstream_target.py --upstream ~/src/xsg-iii-decomp --game-dir ~/xs3/originals \
    --symbols-out symbols/xs3-symbols.csv --target-out symbols/xs3-target.json
export DECOMP_TARGET=$PWD/symbols/xs3-target.json DECOMP_UPSTREAM=~/src/xsg-iii-decomp \
       DECOMP_GAME_DIR=~/xs3/originals DECOMP_TOOLCHAINS=~/toolchains EE_ROOTFS=~/ee-rootfs
tools/verify_decomp.py --target $DECOMP_TARGET --unit ov04 --compiler contract
tools/match_kit.py init func_00A919C8 --compiler contract
```

A tree written against Ghidra's names with the ledger's annotations (the earlier Episode
III work) is judged through a manifest of its own; a TU says `@cflags -O2 -G0
-fno-strict-aliasing` where it needs to, and an `@addr` may sit in the header the TU
includes.

Three things this game changed in the tools:

- `upstream_target.py` keeps a name the main image gives an address in the overlay bank
  (the test-window functions main calls in OV02). Three overlays share the bank, so the
  address alone names no unit; it is written as an absolute of the main image. Eight
  functions were unverifiable without it.
- `verify_decomp.py` judges a function defined under an auto name (`FUN_00a8ded8`) when
  the table spells that address another way (`func_00A8DED8`). It used to find no size for
  it and drop it without a word: a whole TU could report 0 of 0.
- `verify_decomp.py` reads annotations from the headers a TU includes, not only from the
  TU.

## Open

- `main/tu334`'s `-G` and the per-TU aliasing flag are findings for the upstream, measured
  at `3a26737`; neither is in its configuration.
- The seven OV04 functions are not in the upstream yet (its symbols still call them
  `func_00A8DED8` … `func_00A9A1D8`); they match under its contract with the flag above.
- Which TUs of main, OV01 and OV02 want `-fno-strict-aliasing` is unknown beyond the 59
  that refuse it.
- The upstream's IOP configuration still describes Episode II's `KORGIVAG.IRX`, which is
  not on this disc; Episode III's own modules are stripped.
- The Japanese executable has not been diffed function by function; the table above is
  shape transfer only.
- Data-section order against text order, and TU boundaries: Episode II's calibration
  (layout gives no boundary signal; anchors and matching do) is expected to hold and has
  not been re-run here.
