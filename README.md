# Decomp Compatibility Kit

[![Featured by Awesome REA](https://raw.githubusercontent.com/reacher-z/Awesome-REA/477658fa73f6860e86cd1fc526657c2efdc25194/assets/featured-by-awesome-rea-inline.svg)](https://awesomerea.org/resources/decomp-compat-kit/)

Tools for the parts of a PlayStation 2 matching decompilation that sit *around*
the compiler: finding out what built a binary, moving symbol tables between
binaries and projects, triaging functions, checking a decomp's C against the
original bytes with an **independently obtained toolchain**, and running the
one-function matching loop in a way that cannot be talked into a false match.

It grew out of the *Xenosaga Episode I* decompilation (NTSC-U, `SLUS_204.69`),
where it was the "Xenosaga I Decomp Compatibility Kit", and the matching tools
still default to that target. Since 2026-10-30 the identification, symbol and
triage tools are measured on six games (`docs/CORPUS.md`), the verifier takes
any ELF layout and any compile-contract layout, and two documents record what
the Xenosaga I project learned about the stock ee-gcc 2.96 versus the retail
toolchain (`docs/TOOLCHAIN-DIFFERENCES.md`) and about matching GCC 2.96 output
on the Emotion Engine (`docs/MATCHING-PLAYBOOK.md`).

No game binaries, extracted assets, or decompiled game code are distributed
here. You need your own copy of the game. Everything is Python 3.9+, standard
library only (`rabbitizer` is optional, for named disassembly in `match_kit.py`).

## Why

A matching decomp's gate is the strongest claim in the field: rebuild the file,
hash it, compare to the original. Nothing masked, nothing normalised. If it
passes, the instruction stream is identical.

Two things that gate cannot do, both by construction:

1. **Verify itself from the outside.** The gate runs on the author's machine
   with the author's toolchain. When that toolchain is a patched compiler, and
   for PS2 targets it usually is, because the retail compilers were SCE builds
   that no longer exist unmodified, "my source reproduces the image" and "my
   source is right" are the same sentence in two different senses. This kit
   separates them: compile the same source with a *stock, unpatched* compiler
   and see what moves.

2. **Check meaning.** A passing hash proves the instructions; it says nothing
   about whether the types, struct layouts and names above them are correct.
   Many different C sources compile to identical machine code.

And one thing the field keeps relearning: most of the work before the first
matched function is *identification*: which compiler, which flags per
translation unit, which functions are SDK code that already has a name, which
functions are cheap. The tools in the first half of the table are for that.

## What's here

| Path | | Measured on |
|---|---|---|
| `tools/fingerprint_elf.py` | which compiler built a PS2 code image, with evidence in the order that avoids the known misreads: vendor strings, container tells, prologue census, idioms, SDK banners | 9 binaries from 6 games, `docs/CORPUS.md` |
| `tools/unwrap_image.py` | code images that are not a plain ELF: a zlib-stub boot ELF (MGS3) inflated and its container header described, a Metrowerks `MWo3` overlay (Silent Hill 2, Yakuza) parsed, a plain ELF flattened; emits `.image` + JSON sidecar | MGS3 round-trips byte-identical to an independent unpack |
| `tools/dump_symbols.py` | ELF symtab → CSV, for all binaries of a target at once | Xenosaga I: 15,132 symbols from six unstripped binaries |
| `tools/transfer_symbols.py` | name a stripped binary's functions from an unstripped donor of the same compiler era by masked body matching | Xenosaga I → III: reproduces all 428 known matches; Xenosaga I → FFX: 196 SDK/CRT functions |
| `tools/symbols_to_splat.py` | symbol-table CSV → the `name = 0xVA; // type:func size:0xN` format splat ingests | cross-checked against an independent symbol file: 5,927 names, 0 disagreements |
| `tools/triage.py` | every function's shape and difficulty from the bytes: trivial / easy / medium / hard / cop2 / huge, the `-G0`/`-G8` witness, `lq`/`sq`, switch tables, sibling-call shape | Xenosaga I main 4,636 functions, OV11 139 |
| `tools/probe_toolchain.py` | what a given `cc1` + `ee-as` pair does on the behaviours known to separate stock from retail: literal rounding, loop padding, hazard nops, `move`/`break`/`la`/jump-table encodings, `vsqrt`, small data | stock archive vs the Xenosaga I project's patched pair, `docs/TOOLCHAIN-DIFFERENCES.md` §E |
| `tools/verify_decomp.py` | compile a decomp's C with a stock (or its own pinned) toolchain, relocate against a real symbol table, byte-compare **per function**, classify every mismatch by which toolchain difference caused it | 857 functions of an advanced decomp: 95.3 % identical under stock, 0 unexplained |
| `tools/match_kit.py` | the one-function matching loop for people and agents: sandbox, evidence card, named disassembly, a verdict per attempt from compiled bytes, no-progress stop, anti-cheat lint, patch output | the Xenosaga I stock lane, 2026-09-27 onward |
| `rootfs.lock.json` | measured SHA-256 of the stock rootfs tools and of the project's contract `cc1` / `ee-as`; roots named by their environment variable, never a machine path | |
| `symbols/symbols.csv`, `symbols/splat/*.txt` | the Xenosaga I symbol table (unstripped retail executables), as CSV and in splat's format | |
| `docs/CORPUS.md` | the six-game fingerprint table and the two heuristic corrections it forced | |
| `docs/TOOLCHAIN-DIFFERENCES.md` | the catalogue: every compiler and assembler behaviour the Xenosaga I project found between the archived ee-gcc 2.96 and the retail build, how each shows up in a diff, and what the probe measured | |
| `docs/MATCHING-PLAYBOOK.md` | the loop discipline, the evidence card, forty-odd GCC 2.96 codegen idioms each tied to the function that proved it, review and cost lessons | |

## Identify: `fingerprint_elf.py`, `unwrap_image.py`

```sh
tools/fingerprint_elf.py SLUS_204.69                      # ELF, MWo3 file, or an .image with its sidecar
tools/fingerprint_elf.py SLUS_204.69 --json -v
tools/unwrap_image.py SLUS_209.15 --describe              # a zlib-stub boot ELF: what is inside
tools/unwrap_image.py SLUS_209.15 --out work/ && tools/fingerprint_elf.py work/SLUS_209.15.image
tools/fingerprint_elf.py IOP/SSD.IRX                      # an IOP module: says so and stops
```

The order matters and was learned the hard way: **vendor strings first**
(`.comment` survives stripping and names Metrowerks outright), then container
tells (`MWo3`, a zlib stub, ELF type `0xff80`), then the prologue census, then
idioms, then `PsIIlib` SDK banners. The census alone misfiles CodeWarrior as SN
ProDG (both save with `sq` at a 16-byte stride), and a minority save style is
present in every binary because the Sony SDK libraries are ee-gcc objects
whatever built the game. Two findings came out of running it across the shelf:
MGS3 fingerprints as ee-gcc **3.x** (float constants loaded from `.rodata`),
not the 2.9x its first pass assumed, and FFX saves `sd` at a 16-byte stride,
the signature of the 1999 `2.9-991111` build. Both are leads for a matching
rig, not verdicts; the table in `docs/CORPUS.md` says which rows are proven by
a byte-identical compile and which are fingerprints.

## Name: `dump_symbols.py`, `transfer_symbols.py`, `symbols_to_splat.py`

```sh
tools/dump_symbols.py --code-dir /path/to/extracted/code --out symbols.csv
tools/transfer_symbols.py --donor SLUS_204.69 --donor-symbols symbols.csv \
    --acceptor SLUS_213.89 -o xs1_to_xs3.tsv --splat xs3_names.txt --compare known.tsv
tools/symbols_to_splat.py --all --locals --outdir symbols/splat
```

`transfer_symbols.py` packages a method this workspace used twice by hand:
every function of an unstripped donor, with the relocatable fields masked
(`j`/`jal` targets, `lui`, load/store/`addiu`/`ori` immediates, branch
offsets; `--mask loose` adds compare immediates), is searched in the acceptor
through an 8-word anchor index and verified at full length. Same compiler
family and SDK era means verbatim SDK, CRT and engine objects, so the names
transfer. Against the 428 Xenosaga I → III matches found in 2026 by an ad-hoc
script it reproduces 424 under `strict` and **428 of 428 under `loose`** (the
four differ in one `slti` bound each), in 0.7 s; against Final Fantasy X it
names 196 functions, 129 of them `sce*`. Multiple hits (identical thunks) and
donors that share a masked body are reported, never guessed; `--splat` emits
only the clean ones unless `--all-hits`. The same idea is what gave PS2Recomp's
embedded `sce-symbol-scanner` 529 of 530 correct SDK names on this binary.

`symbols_to_splat.py` refuses to guess as well: a splat symbol file is one flat
namespace, an ELF symtab is not, so a local name that genuinely holds several
addresses (`buffer`, `tbl`, `pi`; 361 in Xenosaga I's main alone) is dropped
and counted rather than emitted with whichever address came first. Pass
`--locals` for overlays whose functions are all `LOCAL`.

## Triage: `triage.py`

```sh
tools/triage.py --elf SLUS_204.69 --symbols symbols.csv --summary
tools/triage.py --elf OV11.OVL --symbols symbols.csv --binary OV11.OVL --json ov11.json
tools/triage.py --elf SLUS_213.89 --splat xs3_names.txt --name sceGsResetGraph
```

Per function, from the bytes and a symbol table (CSV, splat, or `name,va,size`
lines): instruction and branch counts, callees named, indirect calls, switch
tables, FPU and COP2 counts, `lq`/`sq` presence, frame size and saved
registers, `$gp` use, float-literal loads, leaf and sibling-call shape; then the
score and class the Xenosaga I project's workers are routed by (credited to its
`function_context.py`). Two witnesses are worth the run on their own: a
`lui`/`lw` pair on an object of 8 bytes or less means that TU was compiled
`-G0`, `$gp`-relative access means `-G8`; and `cop2`/`quad` functions are the
ones plain C will not reproduce. Xenosaga I main: 544 trivial, 1,642 easy,
1,540 medium, 596 hard, 239 cop2, 75 huge; OV11 shows zero `$gp` use and 37
absolute-addressed small objects, a `-G0` overlay.

## Probe: `probe_toolchain.py`

```sh
export EE_ROOTFS=~/ee-rootfs                     # the i386 rootfs with the stock archive under opt/ee
tools/probe_toolchain.py --json stock.json
tools/probe_toolchain.py --toolchains ~/src/decomp/.work/toolchains \
    --cc1 /toolchains/ee-gcc2.96-realconv-lp7-fsv3-v1/bin/cc1 \
    --as  /toolchains/ee-legacy-binutils-la29-vsqrt-nolabel-laimm-v1/bin/ee-as \
    --as-flags "-EL -m5900 -mabi=eabi -G8" --cc1-flags "-quiet -O2 -G 8 -fno-builtin"
```

One docker run, one row per behaviour: observed, what the retail Xenosaga I
build did, and a verdict. Run it on whatever `cc1`/`ee-as` you hold before
matching anything: it tells you up front whether your literals will be one ulp
low, whether your `c.lt.s`/`bc1t` pairs will be missing a nop, and (a thing the
run itself taught) that the archived `ee-as` only mis-encodes `break 7` and
uses `daddu` for jump-table adds under `-mcpu=4000`, the flag one reaches for to
get hazard nops back. Results for the stock archive and the project's patched
pair are tabulated in `docs/TOOLCHAIN-DIFFERENCES.md` §E.

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
give the text placement (nothing assumes `0x200000`/`0x1000` any more), and its
basename selects the rows of `--symbols`, so an overlay ELF or another game's
boot executable works the same way. Trees that do not have the xsg-i-decomp
config layout (`config/tu-build.json` + per-TU ledgers, or the older compile
manifest) name their C files with `--src`, include roots with `-I`, one `-G`
for all, extra defines with `-D` (`SKIP_ASM` is always defined).

**Two compilers, one box** (`debian:bookworm-slim --platform linux/386`):

| `--compiler` | cc1 | assembler | what it proves |
|---|---|---|---|
| `stock` | `EE_ROOTFS/opt/ee/.../cc1`, unpatched 2.96-ee-001003-1, `-G<n> -quiet -O2` | rootfs `ee-as` 2.10, `-G<n>` | the independent, outside view: 95 % of the project's functions agree with it |
| `contract` | the decomp's own pinned cc1, driven as its driver drives it | the decomp's own pinned `ee-as` and argv | the project's own compile contract, per function, without a link |

The contract layout is **read from the upstream's `config/toolchain-identity.json`**
(schema `toolchain-identity/1`: tool `dir`/`path`, contract `driver_flags` and
`assembler_argv`; `--contract-name` picks a contract, the default is the first
GCC 2.96-family one), overridden by `--contract-cc1`/`--contract-as`/
`--contract-cc1-flags`/`--contract-as-flags` or the `DECOMP_CONTRACT_*`
environment variables, with this file's constants as the last fallback; the
first layout whose two tools exist under `--toolchains` (else
`DECOMP_TOOLCHAINS`, else `<upstream>/.work/toolchains`) wins and is printed.
`contract` is the default whenever one resolves. Both tools are i386 ELFs
mounted read-only at `/toolchains`; cc1 runs through the rootfs loader like the
stock one, a 2.9-era `ee-as` rebuilt against a modern glibc runs on the box's
own libc. The per-TU `-G` comes from the ledgers. `rootfs.lock.json` records the
measured SHA-256 of every tool of both lanes next to the project's pins.

**Section-relative references** are resolved, not skipped: the address of a
file-local function (`.text`+offset, by the object's own symbol), file-local
statics in `.data`/`.bss`/`.sbss` (by name, including the `STT_NOTYPE` symbols
`ee-as` emits for `.lcomm` data and references into the middle of a static
struct or array), `$gp`-relative statics (the object's `.reginfo` `ri_gp_value`
bias is removed first), and `switch` jump tables in `.rodata`, which are
anchored on the original's table address only after every entry is shown to
point at the same code. When the Xenosaga I project moved its data definitions
into the C files in October 2026, every such TU became unverifiable from the
outside overnight; after the `NOTYPE` and `.reginfo` fixes `xgl_thread.c` is
4 of 4 identical again under both compilers.

**Fail-closed.** Every run builds in its own `--work/run-<pid>-<ms>/` tree
(the report records it); only trees whose pid is dead or older than
`--docker-timeout` plus a minute are removed, so concurrent runs sharing one
`--work` never delete each other. A docker failure is exit 2 with the stderr
tail (never "0 matches"); a TU that fails to build or a run with no jobs is
exit 1; `--baseline REPORT.json` is exit 1 when any function that report
matched no longer matches. Exit 0 means nothing unbuilt, nothing regressed and
no failing mismatch class: under `stock` only `unexplained` and `size-diff`
fail (`hazard-nop`, `reorder`, `mixed`, `immediate` and `delay-slot` are the
known toolchain differences), under `contract`
every class fails, because the same compiler must reproduce the bytes. The
assembler's stderr is read: `ASWARN` is a build failure under `contract` and a
note under `stock`. The JSON report carries `compiler`, `contract`, `tools`,
`binary`, `segments`, `matched` (per TU), `regressions` and `warnings`.

Preprocessing happens on the host with `clang -E -P -DSKIP_ASM`, not with the
toolchain's `cpp0`: cc1 only sees the token stream, and at `-O2` without `-g`
codegen does not depend on line markers (the one documented exception, a
heap-garbage dependence on path lengths in the stock cc1, is catalogue entry
A6). A construct the two preprocessors expand differently would show up as a
build failure or an `unexplained` diff, never as a false match.

Mismatch classes:

| Class | Meaning |
|---|---|
| `hazard-nop` | the original has extra `nop`s and yours matches otherwise: a 2.9-era assembler inserting R5900 hazard nops, notably between an FP compare and the branch on its condition |
| `reorder` | identical instruction multiset, different order: an alias-analysis difference letting memory references move, or delay-slot filling |
| `mixed` | both of the above in one function |
| `immediate` | same opcodes, one differing immediate: suspect decimal-literal rounding |
| `delay-slot` | the object is shorter by exactly the nops the original keeps in branch delay slots: GNU as 2.10 fills them, the retail-era assembler did not |
| `size-diff` | the object's function is longer or shorter than the original (both sizes shown); the original-sized window is never judged alone, so a body whose first N bytes match cannot pass |
| `unexplained` | everything else. The only class that needs a human. |

Each class maps onto catalogue entries in `docs/TOOLCHAIN-DIFFERENCES.md`.

**Known limitation, stated up front:** the `mixed` test masks branch and jump
targets, because inserting one `nop` shifts every later instruction and so
changes every branch spanning it. That masking is what makes nop insertion
legible, but it means a genuinely wrong call target inside an otherwise-correct
function lands in `mixed` rather than being flagged. Read `mixed` as
"placement differs", not as "verified equivalent".

### Case study

Run against an advanced PS2 matching decomp of this binary (63 translation
units of its `main` unit, September 2026), using a stock `cc1` that is
precisely the unpatched base archive its own patched compiler is built from,
plus a stock assembler:

```
identical      817 / 857   (95.3%)
differing               40
unverifiable           195   (relocations needing that project's data layout)
build failures           1   (stock cc1 ICEs on one TU: clear_by_pieces, expr.c:2346)
```

Every one of the 40 differences was a toolchain difference, not wrong code: 13
missing R5900 hazard nops, 12 pure reorderings, 15 combining both, **0
unexplained**. No wrong opcode, register, operand, or control flow anywhere.

That is the useful shape of a result from this tool. It did not just say "95 %
matches": it said the residual 5 % lands entirely in two classes that the
project's own published patch notes predict. A compiler patched to force
matches would produce *arbitrary* differences under a stock one, not two clean
classes. Reproduction by a third party is what makes that distinction visible.
The 195 "unverifiable" of that run were the data-layout references the
verifier now resolves.

Re-run on 2026-10-30 against the same project's whole `main` unit, now 158 C
translation units and about 90 % recovered, still with the stock pair:

```
identical     2037 / 2459   (82.8%)
differing              422   hazard-nop 33, reorder 4, mixed 281, immediate 6,
                             delay-slot 68, size-diff 20, unexplained 10
unverifiable          1033   (overlay calls, data this table does not name)
```

392 of the 422 differences fall in the five known classes. The 30 that do not
are the reading list a stock lane exists to produce: three `xglDma*` setup
functions two words apart, and a dozen large functions (`JNT_*`,
`virtualMachine`, the menu updaters) whose size differs by a few words, which is
the shape the project's later alias-analysis patches (catalogue A4 to A8)
predict for code the stock `cc1` schedules differently. Nothing here says the
project's C is wrong; it says exactly where the patched compiler is doing work
the stock one does not, which is what an outside check is for.

## Match: `match_kit.py`

A per-function loop on top of `verify_decomp.py`, so the verdict always comes
from compiled bytes and never from whoever (or whatever model) wrote the C:

```sh
export DECOMP_GAME_DIR=/path/to/originals EE_ROOTFS=~/ee-rootfs   # colima mounts $HOME only
uv venv -p 3.14 .venv && VIRTUAL_ENV=$PWD/.venv uv pip install rabbitizer==1.16.2   # disassembly
K=.venv/bin/python\ tools/match_kit.py
$K init    Java_xeno_util_Runtime_jumpEvent__I --forms 30 --author stock-sonnet   # + SANDBOX/card.md
$K disasm  Java_xeno_util_Runtime_jumpEvent__I --range 0x10:0x40 --brief
$K card    Java_xeno_util_Runtime_jumpEvent__I [--json]
$K check   ~/.cache/decomp-match/Java_xeno_util_Runtime_jumpEvent__I "reorder the argument read"
$K verdict ~/.cache/decomp-match/Java_xeno_util_Runtime_jumpEvent__I [--full]
$K best    ~/.cache/decomp-match/Java_xeno_util_Runtime_jumpEvent__I
$K patch   ~/.cache/decomp-match/Java_xeno_util_Runtime_jumpEvent__I [--against newer/runtime.c]
$K self-test
```

Environment: `DECOMP_UPSTREAM` (the decomp checkout), `DECOMP_GAME_DIR` and
`DECOMP_BINARY` (the original's folder and file name, default `SLUS_204.69`;
the name also selects the `symbols.csv` rows), `DECOMP_UNIT` (default `main`),
`EE_ROOTFS`, `DECOMP_TOOLCHAINS` (the contract layout inside is resolved as
`verify_decomp.py` does), `DECOMP_SANDBOXES` (default `~/.cache/decomp-match`).

- `init` locates the function by its definition only (the `INCLUDE_ASM` stub or
  a C body), copies `src/`, `include/` and `config/`, saves untouched copies of
  the source and the TU-local header under `SANDBOX/base/`, records the
  compiler, author, kit and upstream commits (`-dirty` when the files on disk
  are not the commit's), the source hash and the no-progress stop in
  `budget.json`, writes the evidence card, and refuses (exit 3, JSON reason)
  when the original refers to an address the verifier could never resolve by
  name: UNVERIFIABLE before any work, not after twenty forms. The probe folds
  `lui`/`addiu` pairs into addresses across every loaded segment including
  `.bss`; an `addiu`/`ori` into the same register consumes the `lui` half, any
  other write to a register drops its half. A function whose original copies
  16 bytes with `lq`/`sq` is outside plain C's reach unless the TU already
  carries the native 128-bit type (`docs/MATCHING-PLAYBOOK.md`).
- `card` is at most 2 KB: identity, size class, callers and callees from the
  symbol table and the disassembly, argument registers read, member offsets and
  widths per base register, strings, floats, a COP2 flag, the prototypes of
  two or three matched siblings of the same TU, the declarations of the
  referenced names found under `include/` and `src/`.
- `check` prints the verdict line, `diff_words=N` and `diff identical to form
  N` when the bytes did not change, then only the differing rows with one row
  of context (`--full` for all); it saves `forms/NN.c` and `forms/NN.diff`.
  Exit 0 judged, 2 form ceiling or `NO PROGRESS (K forms)` (K charged forms in
  a row with neither a lower `diff_words` nor a higher TU match count; default
  4), 3 UNVERIFIABLE stop, 4 REGRESSION. `SIZE_DIFF` compares the object's
  symbol size with the original's; `UNDECLARED` names what the C refers to
  that the symbol table never held (an invented or misspelled name) and costs
  a form.
- A byte `MATCH` whose source breaks a rule is reported as `CHEAT`: inline asm
  or `.word`, pointers built from a base plus a byte offset (or a `#define`
  offset), raw address casts, placeholder names, `goto`, `do { } while (0)`
  barriers, `volatile` or unused locals that only pin registers.
- `UNVERIFIABLE` costs no attempt; the second one from the same verifier
  version stops the loop (exit 3), because retrying cannot change a tooling
  verdict, and a verifier fix lifts a stale stop.
- `verdict` (exit 0 only on MATCH) adds `compiler`, `regression`,
  `diff_words`, `best_form`, `forms_without_progress`, the init records and
  `scope` (every changed line outside the target function other than
  declarations). `best` restores the lowest-`diff_words` form; CHEAT and
  REGRESSION forms, and forms judged with another compiler, are never `best`
  and never count as progress. `patch` diffs the function block (and the
  TU-local header, when a form split an `unmodeled_` span into named members)
  against the copy `init` saved, refuses when the published file moved since
  init, and `--against FILE` splices the block into a newer copy so waves on
  one file apply in sequence.

The rules came out of running models against it (`docs/MATCHING-PLAYBOOK.md`
§1): the tool decides exactness; a worker's "verified manually" is not
evidence; byte identity is necessary, not sufficient.

## Data-format knowledge is a decomp input

The second gap above deserves more than a footnote, because it is the one that
determines whether a finished decomp is readable or merely correct.

Large stretches of a game binary exist only to parse its own data: archive
decompression, texture uploads into GS memory, script-container loading, the
sound driver's RPC surface. Holding the format specification lets those
functions be typed and named with confidence. Not holding it means inferring
from register traffic, and the byte gate will happily pass either way. Two
people can produce byte-identical C for an archive decompressor and only one of
them can tell you it is a word-dictionary coder whose table is the top 30 words
by frequency with ties broken by first occurrence.

The same asymmetry governs testing. A matching decomp emits an ELF; it cannot
run anything by itself. Checking that a decompiled loader actually loads needs
real files and a known-good reference implementation.

So format reversing is not a parallel hobby next to a decomp. It is an input
to it, and the input that the gate cannot supply.

## Running an i386 ee-gcc on an arm64 Mac

The SCE ee-gcc 2.96 binaries are 32-bit Linux ELFs. There is no native path on
Apple silicon; this is the one that works.

```sh
colima start --cpu 4 --memory 6
docker run --privileged --rm tonistiigi/binfmt --install i386,amd64
```

The `binfmt` step is **required**: without `qemu-i386` registered, a
`--platform linux/386` container cannot exec anything. Then drive `cc1` directly
through the dynamic loader:

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
  trick. Drive `cc1`/`cc1plus` directly and preprocess separately. `clang -E -P`
  on the host is fine: only the token stream matters.
- Mounts must be under `$HOME`; colima does not mount `/private/tmp`.
- Colima may come up `aarch64` even with `--arch x86_64`. That is fine; the
  binfmt handler does the work.
- `cc1 --version` prints only the cpplib line; `cc1 -version` prints the real
  identity (`GNU C version 2.96-ee-001003-1 (ee) compiled by GNU C version
  2.9-gnupro-99r1`).
- Bare Qiling does not import here (`qiling` 1.4.6's keystone binding fails to
  load, and upgrading `keystone-engine` does not fix it); stubbing the
  `keystone` module before the import makes it run cc1/ee-as about a second per
  call, and the two routes produce identical objects. The docker route is the
  one these tools use.

The compiler itself comes from [decompme/compilers](https://github.com/decompme/compilers)
(`ee-gcc2.96.tar.xz`). Nothing in this kit vendors it.

## Acknowledgments

[OpokXeno/xsg-i-decomp](https://github.com/OpokXeno/xsg-i-decomp), the Xenosaga I
matching decomp whose toolchain investigations fill `docs/TOOLCHAIN-DIFFERENCES.md`
and whose `function_context.py` classifier `triage.py` reproduces;
[splat](https://github.com/ethteck/splat),
[spimdisasm](https://github.com/Decompollaborate/spimdisasm) and
[rabbitizer](https://github.com/Decompollaborate/rabbitizer) for the symbol-file
format and the disassembler; [decompme/compilers](https://github.com/decompme/compilers)
for archiving the historical toolchains that make any of this checkable;
[SSXModding/ps2-ee-toolchain](https://github.com/SSXModding/ps2-ee-toolchain) for
the only public SCE EE GCC/binutils source (2.9-ee-991111);
[ran-j/PS2Recomp](https://github.com/ran-j/PS2Recomp) for the SDK symbol
database whose precision set the bar for `transfer_symbols.py`.

## License

MIT, see [LICENSE](LICENSE). Symbol addresses are facts about a binary you own;
the game itself is not included and remains the property of its rights holders.
