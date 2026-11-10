# The tools, one by one

Reference for every script in `tools/`. Each has `--help`, and each one that
makes a claim has `--self-test`. The README gives the short version; this file
is the long one, in pipeline order: **identify** the compiler, **name** what
the SDK already named, **triage** the rest, **probe** the toolchain you hold,
**verify** a decomp from the outside, **match** one function at a time, and
(new) **compile with CodeWarrior**.

Conventions used throughout: nothing assumes a load address (ELF program
headers decide), nothing guesses a name (ambiguities are counted, not
resolved), and every verdict comes from bytes the tool compiled or read itself.

## Identify: `fingerprint_elf.py`, `unwrap_image.py`

```sh
tools/fingerprint_elf.py SLUS_204.69                      # ELF, MWo overlay, or an .image with its sidecar
tools/fingerprint_elf.py SLUS_204.69 --json -v
tools/unwrap_image.py SLUS_209.15 --describe              # a zlib-stub boot ELF: what is inside
tools/unwrap_image.py SLUS_209.15 --out work/ && tools/fingerprint_elf.py work/SLUS_209.15.image
tools/fingerprint_elf.py GX/AEE.BIN                        # a Metrowerks MWo3 overlay, header parsed in place
tools/fingerprint_elf.py IOP/SSD.IRX                      # an IOP module: says so and stops
```

`fingerprint_elf.py` answers "which compiler built this" with its evidence in
the order that avoids the known misreads:

1. **Vendor strings.** `.comment` survives stripping. `MW MIPS C Compiler
   (2.4.1.01)` is CodeWarrior for PS2 (a constant string of its R3.x
   compilers); GNU strings, `gcc2_compiled.` symbols and old GNU C++ mangling
   say GCC 2.x; Itanium `_Z` names say 3.x.
2. **Container tells.** An `MWo1`..`MWo3` header is the Metrowerks linker's
   overlay record; a boot ELF whose one segment starts `[u32 length][zlib
   stream]` is a decompressor stub; ELF type `0xff80` / `.iopmod` is an IOP
   module (R3000, none of the EE heuristics apply).
3. **The entry point.** A run of `padduw rd,$zero,$zero` is the register-
   zeroing startup seen on Yakuza, the habit of CodeWarrior for PlayStation's
   `_psstart.c`; Sony's `crt0` shape proves nothing (Silent Hill 2 links it
   under CodeWarrior).
4. **The prologue census.** `addiu $sp,$sp,-N` followed by callee saves. SCE
   ee-gcc saves with `sd`, at 8 bytes (2.96, 3.x) or 16 (the 1999 `2.9-991111`
   build); CodeWarrior and ProDG save with `sq` at 16. The census alone cannot
   separate those two, and a minority style is present in every binary because
   the Sony SDK libraries are ee-gcc objects whatever built the game.
5. **Idioms.** `nop;nop` before `div.s` is SCE gcc from 2.9-ee-991111-01 (June
   2000) on and never CodeWarrior; `sqrt.s` unpadded with `div.s` padded is
   3.x; float constants as `lui`+`mtc1` are 2.96, loaded from `.rodata` 3.x;
   real `div`/`div.s` everywhere.
6. **SDK banners** `PsIIlib<name><version>` date the Sony SDK; `__DATE__`
   stamps and surviving source paths sketch the translation-unit skeleton.

The verdict is a ranked hypothesis with its reasons and footnotes; the proof
is always a byte-identical compile (`verify_decomp.py`). `docs/CORPUS.md`
holds the measured rows for six games.

`unwrap_image.py` handles the images that are not a plain ELF: a zlib-stub boot
ELF is inflated and its container header described (MGS3's begins with a Unix
build timestamp); a Metrowerks `MWo` overlay is parsed with the fields the
Metrowerks manuals and runtime name (`overlay_id`, `load_addr`, text, data and
bss sizes, `static_init`/`static_init_end` on version 3); a plain ELF is
flattened. It writes `<name>.image` plus a JSON sidecar that
`fingerprint_elf.py` and `transfer_symbols.py` read for the base address.
MGS3 round-trips byte-identical to an independent unpack; 55 PS2 overlay
records agree with the layout (`docs/CODEWARRIOR.md` §2).

## Search: `find_symbols.py`

```sh
tools/find_symbols.py GAME.iso DEMO.iso --json report.json
```

Before reconstructing names for a stripped game, look once through everything on the disc.
The scan reads the image byte by byte, so it also sees what no directory lists: every
32-bit little-endian MIPS ELF header at any offset, what each carries (named symbols,
`.mdebug`, stabs, DWARF), and text only a toolchain writes (`gcc2_compiled.`, GNU ld map
headings), each with the file it lies in. The one-entry symbol table every SCE module has
is not reported as a find. It does not see inside compressed archive members, and an ELF
stored in pieces is reported unreadable rather than guessed at; the tool's help lists the
limits. A 4.7 GB image takes about 13 seconds.

## Name: `dump_symbols.py`, `transfer_symbols.py`, `sdk_donor.py`, `symbols_to_splat.py`

```sh
tools/dump_symbols.py --code-dir /path/to/extracted/code --out symbols.csv
tools/transfer_symbols.py --donor SLUS_204.69 --donor-symbols symbols.csv \
    --acceptor SLUS_213.89 -o xs1_to_xs3.tsv --splat xs3_names.txt --compare known.tsv
tools/sdk_donor.py --out-elf sdk20.elf --out-csv sdk20.csv /path/to/sce/ee/lib
tools/transfer_symbols.py --donor sdk20.elf --donor-symbols sdk20.csv --acceptor SLUS_203.12 -o ffx_sdk.tsv
tools/symbols_to_splat.py --all --locals --outdir symbols/splat
```

`dump_symbols.py` exports the ELF symbol tables of every unstripped binary in
a directory to one CSV (`binary,name,va,size,type,bind,shndx`). Xenosaga I's
six binaries give 15,132 symbols; the kit ships them under `symbols/`.

It also reads `.mdebug`, the ECOFF symbol data older GNU toolchains wrote, which can
survive in an IOP module whose game executable is stripped and which names the source
files and the file-local statics: `--recursive` takes modules in sub-directories,
`--mdebug-out FILE` writes `binary,file,kind,name,value` rows (a row with an empty `file`
is an external). Checked on Xenosaga II's `KORGIVAG.IRX`: 239 procedures agreeing with
`.symtab` by name and address, 91 statics, 29 labels, 4 source files.

`transfer_symbols.py` packages a method used twice by hand before it existed:
every function of an unstripped donor, with the relocatable fields masked
(`j`/`jal` targets, `lui`, load/store/`addiu`/`ori` immediates, branch
offsets; `--mask loose` adds compare immediates), is searched in the acceptor
through an 8-word anchor index and verified at full length. Same compiler
family and SDK era means verbatim SDK, CRT and engine objects, so the names
transfer. Against the 428 Xenosaga I → III matches found earlier by an ad-hoc
script it reproduces 424 under `strict` and 428 of 428 under `loose`, in
0.7 s; against Final Fantasy X it names 196 functions. Multiple hits
(identical thunks) and donors that share a masked body are reported, never
guessed; `--splat` emits only the clean ones unless `--all-hits`.

`--sibling` is for an acceptor that is another build of the same program (a demo,
a prototype, another region). Link order is then preserved, so the tool may use it:
unique hits outside the longest order-preserving chain are marked ambiguous
(`<order>`: a body that changed, found again elsewhere), and a multi-hit or shared
body is placed when exactly one of its hits lies between its nearest placed
neighbours (at their shift, when both moved alike). Xenosaga II retail → the
*Xenosaga Freaks* demo: unique placements 4,577 → 6,316 (main) and 774 → 1,070 (OV01),
zero order inversions.

`sdk_donor.py` makes a donor out of Sony's own SDK static libraries
(`sce/ee/lib/*.a`, unstripped relocatable objects): every executable section
laid into one donor ELF, every FUNC symbol into a CSV. Because the libraries
are ee-gcc objects in every PS2 game, this names the SDK minority of a stripped
executable whatever compiler built the game: SDK 2.0 names 58 functions in
FFX, 85 in Silent Hill 2 (a CodeWarrior title) and 49 in Xenosaga I, where the
retail symbol table confirms them (`docs/SDK-ARCHIVE.md` §3).

`symbols_to_splat.py` writes the `name = 0xVA; // type:func size:0xN` lines
splat ingests and refuses to guess: a splat symbol file is one flat namespace,
an ELF symtab is not, so a local name that genuinely holds several addresses
(`buffer`, `tbl`, `pi`; 361 in Xenosaga I's main alone) is dropped and counted
rather than emitted with whichever address came first. Pass `--locals` for
overlays whose functions are all `LOCAL`. Cross-checked against an independent
symbol file: 5,927 names, 0 disagreements.

## Triage: `triage.py`

```sh
tools/triage.py --elf SLUS_204.69 --symbols symbols.csv --summary
tools/triage.py --elf OV11.OVL --symbols symbols.csv --binary OV11.OVL --json ov11.json
tools/triage.py --elf SLUS_213.89 --splat xs3_names.txt --name sceGsResetGraph
```

Per function, from the bytes and a symbol table (CSV, splat, or `name,va,size`
lines): instruction and branch counts, callees named, indirect calls, switch
tables, FPU and COP2 counts, `lq`/`sq` presence, frame size and saved
registers, `$gp` use, float-literal loads, leaf and sibling-call shape; then
the score and class the Xenosaga I project's workers are routed by (credited to
its `function_context.py`): trivial / easy / medium / hard / cop2 / huge. Two
witnesses are worth the run on their own: a `lui`/`lw` pair on an object of 8
bytes or less means that TU was compiled `-G0`, `$gp`-relative access means
`-G8`; and `cop2`/`quad` functions are the ones plain C will not reproduce.
Xenosaga I main: 544 trivial, 1,642 easy, 1,540 medium, 596 hard, 239 cop2,
75 huge; OV11 shows zero `$gp` use and 37 absolute-addressed small objects, a
`-G0` overlay.

## Probe: `probe_toolchain.py`

```sh
export EE_ROOTFS=~/ee-rootfs                     # the i386 rootfs with the stock archive under opt/ee
tools/probe_toolchain.py --json stock.json
tools/probe_toolchain.py --toolchains ~/src/decomp/.work/toolchains \
    --cc1 /toolchains/ee-gcc2.96-realconv-lp7-fsv3-v1/bin/cc1 \
    --as  /toolchains/ee-legacy-binutils-la29-vsqrt-nolabel-laimm-v1/bin/ee-as \
    --as-flags "-EL -m5900 -mabi=eabi -G8" --cc1-flags "-quiet -O2 -G 8 -fno-builtin"
tools/probe_toolchain.py --toolchains ~/sce-tc20 \
    --cc1 /toolchains/ee/gcc/lib/gcc-lib/ee/2.9-ee-991111-01/cc1 --as /toolchains/ee/gcc/bin/ee-as \
    --as-flags "-EL -m5900 -mabi=eabi -G8"                       # Sony's own 2000 toolchain
```

One docker run, one row per behaviour: observed, what the retail Xenosaga I
build did, and a verdict. Run it on whatever `cc1`/`ee-as` you hold before
matching anything: it tells you up front whether your literals will be one ulp
low, whether your `c.lt.s`/`bc1t` pairs will be missing a nop, how short loops
are padded, and how `move`, `break`, `la`, jump tables and `vsqrt` encode.
A 2000-era SCE binary that aborts inside `exit()` after writing complete
output (Sony's Tool Chain 2.0 does, on a modern glibc) is accepted and noted.
Results: `docs/TOOLCHAIN-DIFFERENCES.md`, with the measured JSON under
`docs/probes/`.

## Verify: `verify_decomp.py`

```sh
export DECOMP_GAME_DIR=/path/to/your/extracted/originals EE_ROOTFS=~/ee-rootfs
tools/verify_decomp.py --upstream ~/src/some-decomp --out report.json
tools/verify_decomp.py --upstream ~/src/some-decomp --tu main/tu086 -v
tools/verify_decomp.py --upstream ~/src/some-decomp --compiler contract --toolchains DIR
tools/verify_decomp.py --upstream ~/src/some-decomp --baseline report.json      # regression gate
tools/verify_decomp.py --src src/*.c -I include -G 0 --binary OV11.OVL --symbols symbols.csv
tools/verify_decomp.py --self-test
```

**Any target.** `--binary` is any 32-bit little-endian ELF: its program headers
give the text placement, and its basename selects the rows of `--symbols`, so
an overlay ELF or another game's boot executable works the same way. Trees
that do not have the xsg-i-decomp config layout (`config/tu-build.json` + per-TU
ledgers, or the public `config/objects/overlays.compile.json`) name their C
files with `--src`, include roots with `-I`, one `-G` for all, extra defines
with `-D` (`SKIP_ASM` is always defined).

**Two compilers, one box** (`debian:bookworm-slim --platform linux/386`):

| `--compiler` | cc1 | assembler | what it proves |
|---|---|---|---|
| `stock` | `EE_ROOTFS/opt/ee/.../cc1`, unpatched 2.96-ee-001003-1, `-G<n> -quiet -O2` | rootfs `ee-as` 2.10, `-G<n>` | the independent, outside view: 95 % of the project's functions agree with it |
| `contract` | the decomp's own pinned cc1, driven as its driver drives it | the decomp's own pinned `ee-as` and argv | the project's own compile contract, per function, without a link |

The contract layout is read from the upstream's `config/toolchain-identity.json`
(schema `toolchain-identity/1`), overridden by `--contract-*` options or the
`DECOMP_CONTRACT_*` environment, with this file's constants as the last
fallback; the first layout whose two tools exist under `--toolchains` (else
`DECOMP_TOOLCHAINS`, else `<upstream>/.work/toolchains`) wins and is printed.
Every contract report carries the upstream's pinned cc1 hash and whether the
pair used matches it. `rootfs.lock.json` records the measured SHA-256 of every
tool of both lanes.

**Section-relative references** are resolved, not skipped: file-local
functions, file-local statics in `.data`/`.bss`/`.sbss` (including the
`STT_NOTYPE` symbols `ee-as` emits for `.lcomm` data), `$gp`-relative statics
(the object's `.reginfo` bias removed first), and `switch` jump tables in
`.rodata`, anchored on the original's table address only after every entry is
shown to point at the same code. Skips are itemized by function and reason.

**Fail-closed.** Every run builds in its own `--work/run-<pid>-<ms>/` tree; a
docker failure is exit 2 with the stderr tail (never "0 matches"); a TU that
fails to build is exit 1; `--baseline REPORT.json` is exit 1 when any function
that report matched no longer matches. Under `stock` only `unexplained` and
`size-diff` fail, because the other classes are known toolchain differences;
under `contract` every class fails, because the same compiler must reproduce
the bytes.

Preprocessing happens on the host with `clang -E -P -DSKIP_ASM`: cc1 only
sees the token stream, and at `-O2` without `-g` codegen does not depend on
line markers (the documented exception is catalogue entry A6).

Mismatch classes, each mapped onto `docs/TOOLCHAIN-DIFFERENCES.md`:

| Class | Meaning |
|---|---|
| `hazard-nop` | the original has extra `nop`s and yours matches otherwise: a 2.9-era assembler inserting R5900 hazard nops |
| `reorder` | identical instruction multiset, different order: alias analysis or delay-slot filling |
| `mixed` | both of the above in one function |
| `immediate` | same opcodes, one differing immediate: suspect decimal-literal rounding |
| `delay-slot` | the object is shorter by exactly the nops the original keeps in branch delay slots |
| `size-diff` | the function is longer or shorter than the original (both sizes shown) |
| `unexplained` | everything else. The only class that needs a human. |

**Known limitation:** the `mixed` test masks branch and jump targets, because
one inserted `nop` shifts every later instruction. Read `mixed` as "placement
differs", not as "verified equivalent".

### Case study

Run against an advanced PS2 matching decomp of Xenosaga I (63 translation
units of its `main` unit, September 2026), with a stock `cc1` that is the
unpatched base archive its own patched compiler is built from:

```
identical      817 / 857   (95.3%)
differing               40    13 hazard-nop, 12 reorder, 15 mixed, 0 unexplained
unverifiable           195    (relocations needing that project's data layout; now resolved)
build failures           1    (stock cc1 ICEs on one TU)
```

Every difference was a toolchain difference, not wrong code. A compiler
patched to force matches would produce *arbitrary* differences under a stock
one, not two clean classes. Re-run on 2026-10-30 against the whole `main`
unit (158 TUs, about 90 % recovered): 2,037 of 2,459 identical, 392 of the
422 differences in the five known classes, 30 left as the reading list a
stock lane exists to produce.

## Match: `match_kit.py`

A per-function loop on top of `verify_decomp.py`, so the verdict always comes
from compiled bytes and never from whoever (or whatever model) wrote the C:

```sh
export DECOMP_GAME_DIR=/path/to/originals EE_ROOTFS=~/ee-rootfs   # colima mounts $HOME only
uv venv -p 3.14 .venv && VIRTUAL_ENV=$PWD/.venv uv pip install rabbitizer==1.16.2   # disassembly
K=.venv/bin/python\ tools/match_kit.py
$K init    someFunction --forms 30 --author stock-sonnet   # + SANDBOX/card.md
$K disasm  someFunction --range 0x10:0x40 --brief
$K card    someFunction [--json]
$K check   ~/.cache/decomp-match/someFunction "reorder the argument read"
$K verdict ~/.cache/decomp-match/someFunction [--full]
$K best    ~/.cache/decomp-match/someFunction
$K patch   ~/.cache/decomp-match/someFunction [--against newer/file.c]
```

Environment: `DECOMP_UPSTREAM` (the decomp checkout), `DECOMP_GAME_DIR` and
`DECOMP_BINARY` (the original's folder and file name; the name also selects the
`symbols.csv` rows), `DECOMP_UNIT`, `EE_ROOTFS`, `DECOMP_TOOLCHAINS`,
`DECOMP_SANDBOXES` (default `~/.cache/decomp-match`), `DECOMP_IMAGE` (the build
container, default `debian:bookworm-slim`; a contract assembler built against a newer
glibc needs a newer image).

- `init` locates the function by its definition (the `INCLUDE_ASM` stub or a C
  body), copies the sources into a sandbox, records compiler, author, commits,
  source hash and the no-progress stop in `budget.json`, writes the evidence
  card, and refuses (exit 3, JSON reason) when the original refers to an
  address the verifier could never resolve by name: UNVERIFIABLE before any
  work, not after twenty forms.
- `card` is at most 2 KB: identity, size class, callers and callees, argument
  registers read, member offsets per base register, strings, floats, a COP2
  flag, prototypes of matched siblings, declarations found.
- `check` prints the verdict line, `diff_words=N`, and only the differing rows;
  it saves `forms/NN.c` and `forms/NN.diff`. Exit 0 judged, 2 ceiling or
  `NO PROGRESS`, 3 UNVERIFIABLE, 4 REGRESSION. A byte `MATCH` whose source
  breaks a rule (inline asm, `.word`, base-plus-offset pointers, raw address
  casts, placeholder names, `goto`, `do {} while (0)` barriers,
  register-pinning locals) is reported as `CHEAT`.
- `verdict` adds compiler, regression, `diff_words`, `best_form` and `scope`;
  `best` restores the lowest-`diff_words` form; `patch` diffs the function
  block against the copy `init` saved.

The rules came out of running language models against it
(`docs/MATCHING-PLAYBOOK.md` §1): the tool decides exactness; a worker's
"verified manually" is not evidence; byte identity is necessary, not
sufficient.

## Targets: a decomp that is not xsg-i-decomp

`verify_decomp.py`, `match_kit.py`, `ledger.py`, `port_check.py` and the MCP server
were written against the Xenosaga I upstream's config layout. A **target manifest**
(`decomp-target/1`, JSON, next to the sources) describes any other tree instead; the
xsg-i-decomp layout stays the other adapter (`--upstream`), unchanged.

```json
{"schema": "decomp-target/1", "name": "Xenosaga Episode II (USA) SLUS_208.92",
 "game_dir": "ELF", "symbols": "symbols/xs2.csv", "resident": "SLUS_208.92",
 "units": {"main": {"binary": "SLUS_208.92"}, "ov01": {"binary": "OV01.OVL"}},
 "include": ["src/include", "src/main"], "defines": [],
 "toolchains": "~/.cache/xs2-toolchains",
 "contracts": {"c296-g": {"cc1": "ee-gcc2.96-realconv-lp7-fsv12-v1/bin/cc1",
                          "as": "ee-legacy-binutils-la29-vsqrt-nolabel-laimm-swap-v1/bin/ee-as",
                          "cc1_flags": "-fno-builtin", "as_flags": "-EL -m5900 -mabi=eabi -g -I ."}},
 "default_contract": "c296-g", "ledger": "src/matched.tsv",
 "tus": [{"id": "main/xgl_menu", "unit": "main", "path": "src/main/xgl_menu.c", "flags": ["-O2", "-G8"],
          "contract": "c296-g"}]}
```

Paths are relative to the manifest (`~` and `$VARS` expand). Each TU names its
contract, so one tree can mix assemblers or cc1 flags per TU (Xenosaga I's m_math TU is
`-fno-optimize-sibling-calls`; its game C needs the swap-mask GAS with `-g`, which keeps
delay slots unfilled). `verify_decomp.py --target M` (or `DECOMP_TARGET=M`) builds one
docker run per contract group. `match_kit.py` and the MCP server read `DECOMP_TARGET`
and take the upstream root, originals, the unit's binary, the resident image, the symbol
table and the toolchains from it (each still overridable by its own variable);
`DECOMP_SYMBOLS` overrides the symbol table in either mode. A match sandbox is a copy of
the target's sources with a manifest pointing back at the originals.

A manifest may live outside the tree it describes: `"root"` names the source tree (TU
paths and include roots are then relative to it, everything else to the manifest), so a
read-only checkout needs nothing written into it. `tools/upstream_target.py --upstream TREE
--game-dir ORIGINALS --symbols-out S.csv --target-out T.json` writes such a manifest and
its symbol table from a splat-based checkout that has no ELF symbols (OpokXeno/xsg-ii-decomp:
`config/symbols/<unit>.txt`, `estimated-functions.json`, `data-names.json`, the per-TU
flags and assembler options of `config/objects/*.compile.json`, the tool paths of
`config/toolchain-identity.json`); `root`, `game_dir` and `toolchains` are written as
`$DECOMP_UPSTREAM`, `$DECOMP_GAME_DIR` and `$DECOMP_TOOLCHAINS`. Rerun it after a rename.

In target mode only (the upstream path is byte-for-byte the old behaviour):

- **Annotations in a TU's comments** (the Xenosaga III ledger convention): `@addr NAME 0xVA`
  names a symbol for this TU only (file-local statics, a name the table lacks),
  `@cflags -G0 -fno-strict-aliasing` overrides the manifest's flags, `@rodata 0xA 0xB ...`
  gives the TU's `.rodata` objects in emission order, `@data/@sdata/@bss/@sbss 0xVA` a
  section base. Ghidra-style names (`FUN_00186448`, `DAT_00645820`) and splat's
  (`func_00A9CC70`, `D_0061B2D0`, `jtbl_0068DCB0`) resolve to the address they spell.
- **Content-verified data references.** A reference into the original's initialised data
  (`.rodata` strings and floats, `.lit4`, `.data`, `.sdata`) needs no name: the address the
  original encodes at that site is accepted only when the original holds the object's bytes
  there (relocated words masked). A different object there forces a difference, never a
  match. Uninitialised data (`.bss`, `.sbss`) needs a name. `R_MIPS_LITERAL` is resolved
  the same way.
- **HI16/LO16 pairing by instruction offset.** The `-g` assembler does not keep a HI16 next
  to its LO16 in `.rel.text` (it can list it after a later function's LO16); pairing in
  table order paired `%hi` with the wrong `%lo` on Xenosaga II. A `%lo` that shares another's
  `%hi` resolves through its governing HI16.

## Ledger: `ledger.py`

The Xenosaga III paradigm as a kit feature: a TSV of functions that are **Tier A**
(byte-identical with every relocation applied from the table and the TU's annotations,
nothing masked, same length), and a command that re-proves every row.

```sh
export DECOMP_TARGET=path/to/decomp-target.json
tools/ledger.py check src/main/xgl_menu.c [--add]   # one TU: every function's verdict
tools/ledger.py promote report.json                  # the Tier-A functions of a target verify --out
tools/ledger.py all                                  # re-verify every row; exit 0 only if all hold
tools/ledger.py list
tools/ledger.py sibling NAME [--remap] [--harvest DIR]  # every row against another build
```

`sibling` reads the manifest's `siblings` block, `{NAME: {note, units: {UNIT: {binary,
map}}}}` (binary under `game_dir`, map a `transfer_symbols.py --sibling` TSV, rebuilt with
`--remap`), compiles the TUs holding ledger rows and runs `port_check.py` against that build:
each row must be `identical` there with relocated fields masked. A row that is not either
changed between the builds or matched by luck. Measured on Xenosaga II against the
*Xenosaga Freaks* demo (2026-11-05): 141 of 141 identical, ~20 s.

Columns: `unit addr function bytes source lane verified` (the Xenosaga III ledger's five
plus the compile lane and the date). Measured on Xenosaga II (2026-11-05): 141 rows, `all`
re-proves 141 of 141.

## Cross-game: `port_check.py`, `harvest_relocs.py`

Two games of one studio share engine code. `transfer_symbols.py` finds where game A's
function sits in game B by shape; `port_check.py` asks whether A's *source* reproduces B:

```sh
tools/port_check.py --upstream ~/src/xsg-i-decomp --map xs1_to_xs2.tsv --acceptor SLUS_208.92 \
    --compiler contract --toolchains DIR --contract-name ee-tu-c296-G8-g \
    --harvest xs2_harvest.csv --harvest-accept immediate --out port.json -v
tools/port_check.py --target xs2/decomp-target.json --map xs2_to_xs3.tsv --acceptor SLUS_213.89
```

It compiles A's TUs (the upstream's TU list or a target's), places each mapped function at
its B address (a `transfer_symbols.py` TSV, single-address rows), and compares word by
word masking only the relocated **field** (j/jal: 26 bits; HI16, LO16, GPREL16, LITERAL:
16 bits; anything else the whole word, reported). Classes: `identical`, `immediate` (same
size, every differing word an I-type whose opcode and registers agree: a moved struct
field, a changed constant), `size-diff`, `other`.

`harvest_relocs.py` (run by `--harvest`, or standalone on objects) reads every relocated
field of the identical functions back out of B: the address B's linker gave each symbol.
Globals by name, statics by name and TU, section bases per TU; a symbol referenced from
several sites is cross-checked and a disagreement is reported, never resolved.
`--harvest-accept immediate` also reads the immediate-only functions, whose relocated
fields are unaffected. Measured, Xenosaga I's C against Xenosaga II: 159 functions placed;
134 identical, 24 immediate, 1 size-diff (a static name collision), 0 other under the
`-g` swap-assembler contract (126 / 24 / 9 under the kit-default pair, the 8 extra being
m_math's inline-asm functions whose delay slots only `-g` leaves unfilled); the harvest
read 220 names from 158 functions, 218 with one address, 98 confirmed by more than one site.

## Compile with CodeWarrior: `cw_run.py`

```sh
tools/cw_run.py --which                                  # native wine or the docker image?
tools/cw_run.py --tools ~/cwps2/3.04 --as-of 2002-06-01 -- mwccps2 -version
tools/cw_run.py --tools ~/cwps2/3.04 --as-of 2002-06-01 -- \
    mwccps2 -c -O2 -prefix PREFIX_PS2.h -I ~/cwps2/3.04/PS2_Support -o out.o in.c
```

CodeWarrior for PlayStation 2 R3.0-3.04 ships Win32 command-line tools
(`mwccps2`, `mwldps2`, `asm_r5900_elf`). `cw_run.py` runs them through a
native Wine when one exists (`$CW_WINE`, `wine` on PATH, or the Game Porting
Toolkit bundle, which on Apple Silicon runs the 32-bit tools through Rosetta
in seconds) and otherwise through the kit's amd64 docker image. Host paths are
translated for both; `-I dir` is joined to `-Idir` (the only form the tool
accepts); the tools directory goes on Wine's PATH so `-S` finds the linker.
`--as-of YYYY-MM-DD` runs the tool under a shifted clock (`tools/timeshift.c`,
a dyld interposer; `libfaketime` in the image): the shipped R3.04 evaluation
license's PS2 features expired 18-nov-2002. No key is forged, no binary
patched. Objects come out stamped `MW MIPS C Compiler (2.4.1.01)` with the
`sq`/16 prologues the corpus shows (`docs/SDK-ARCHIVE.md` §4). A judged
CodeWarrior lane (compile with it and byte-compare) is the next step.

## Running an i386 ee-gcc on an arm64 Mac

The SCE ee-gcc binaries are 32-bit Linux ELFs. There is no native path on
Apple silicon; this is the one that works.

```sh
colima start --cpu 4 --memory 6
docker run --privileged --rm tonistiigi/binfmt --install i386,amd64
```

The `binfmt` step is **required**: without `qemu-i386` registered, a
`--platform linux/386` container cannot exec anything. Then drive `cc1`
directly through the dynamic loader:

```sh
docker run --rm --platform linux/386 \
  -v "$HOME/ee-rootfs:/rootfs:ro" -v "$HOME/work:/work" \
  debian:bookworm-slim \
  /rootfs/lib/ld-linux.so.2 --library-path /rootfs/lib/i386-linux-gnu \
  /rootfs/opt/ee/lib/gcc-lib/ee/2.96-ee-001003-1/cc1 \
  /work/x.i -quiet -O2 -G 8 -o /work/x.s
```

Notes that cost time to learn:

- The `ee-gcc` driver cannot exec its own sub-programs through the loader
  trick. Drive `cc1`/`cc1plus` directly and preprocess separately (`clang -E
  -P` on the host is fine; Sony's 2.9-ee cc1 preprocesses by itself).
- Mounts must be under `$HOME`; colima does not mount `/private/tmp`, and a
  separate APFS volume under `$HOME` is not shared either.
- Colima may come up `aarch64` even with `--arch x86_64`. That is fine; the
  binfmt handler does the work.
- `cc1 --version` prints only the cpplib line; `cc1 -version` prints the real
  identity.
- Sony's own 2000 binaries abort inside `exit()` on a modern glibc after
  writing complete output; the probe tolerates it, a verify lane would need to.
- `docker build --platform linux/amd64` on this setup produced an arm64 image;
  a runtime `docker run --platform linux/amd64` plus `docker commit` did not.

The 2.96 compiler comes from [decompme/compilers](https://github.com/decompme/compilers)
(`ee-gcc2.96.tar.xz`). Nothing in this kit vendors a compiler.
