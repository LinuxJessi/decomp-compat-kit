# Decomp Compatibility Kit

Tools for checking a matching decompilation against the original binary with an
**independently obtained toolchain**, and for moving symbol tables between decomp
projects.

Built for PlayStation 2 / Emotion Engine targets compiled with SCE ee-gcc 2.96,
against *Xenosaga Episode I: Der Wille zur Macht* (NTSC-U, `SLUS_204.69`). The
method generalises; the defaults do not.

No game binaries, extracted assets, or decompiled game code are distributed here.
You need your own copy of the game.

## Why

A matching decomp's gate is the strongest claim in the field: rebuild the file,
hash it, compare to the original. Nothing masked, nothing normalised. If it
passes, the instruction stream is identical.

Two things that gate cannot do, both by construction:

1. **Verify itself from the outside.** The gate runs on the author's machine with
   the author's toolchain. When that toolchain is a patched compiler — and for
   PS2 targets it usually is, because the retail compilers were SCE builds that
   no longer exist unmodified — "my source reproduces the image" and "my source
   is right" are the same sentence in two different senses. This kit separates
   them: compile the same source with a *stock, unpatched* compiler and see what
   moves.

2. **Check meaning.** A passing hash proves the instructions; it says nothing
   about whether the types, struct layouts and names above them are correct. Many
   different C sources compile to identical machine code.

## What's here

| Path | |
|---|---|
| `tools/verify_decomp.py` | compile a decomp's C with a stock toolchain, relocate against a real symbol table, byte-compare **per function**, and classify every mismatch by which toolchain difference caused it |
| `tools/symbols_to_splat.py` | symbol-table CSV → the `name = 0xVA; // type:func size:0xN` format splat ingests |
| `tools/dump_symbols.py` | ELF symtab → CSV, for all binaries of a target at once |
| `symbols/symbols.csv` | 15,132 symbols from `SLUS_204.69` and its five overlays (unstripped retail executables) |
| `symbols/splat/*.txt` | the same table in splat's format, one file per binary |

## verify_decomp.py

```sh
export DECOMP_GAME_DIR=/path/to/your/extracted/originals
./tools/verify_decomp.py --upstream ~/src/some-decomp --out report.json
./tools/verify_decomp.py --upstream ~/src/some-decomp --tu main/tu086 -v
```

Exit status is 0 when nothing is unexplained and 1 otherwise, so it works as a
CI gate. It compiles each in-scope C translation unit with `-DSKIP_ASM`, so
partly-migrated units still yield exactly their C functions and get checked
individually rather than being skipped.

Mismatch classes:

| Class | Meaning |
|---|---|
| `hazard-nop` | the original has extra `nop`s and yours matches otherwise — a patched assembler inserting R5900 hazard nops, notably between an FP compare and the branch on its condition |
| `reorder` | identical instruction multiset, different order — an alias-analysis difference letting memory references move |
| `mixed` | both of the above in one function |
| `immediate` | same opcodes, one differing immediate — suspect decimal-literal rounding |
| `unexplained` | everything else. The only class that needs a human. |

**Known limitation, stated up front:** the `mixed` test masks branch and jump
targets, because inserting one `nop` shifts every later instruction and so changes
every branch spanning it. That masking is what makes nop insertion legible, but it
means a genuinely wrong call target inside an otherwise-correct function lands in
`mixed` rather than being flagged. Read `mixed` as "placement differs", not as
"verified equivalent".

### Case study

Run against an advanced PS2 matching decomp of this binary (project anonymised;
63 translation units of its `main` unit), using a stock `cc1` that is precisely
the unpatched base archive its own patched compiler is built from, plus a stock
assembler:

```
identical      817 / 857   (95.3%)
differing               40
unverifiable           195   (relocations needing that project's data layout)
build failures           1   (stock cc1 ICEs on one TU: clear_by_pieces, expr.c:2346)
```

Every one of the 40 differences was a toolchain difference, not wrong code: 13
missing R5900 hazard nops, 12 pure reorderings, 15 combining both, **0
unexplained**. No wrong opcode, register, operand, or control flow anywhere.

That is the useful shape of a result from this tool. It did not just say "95%
matches" — it said the residual 5% lands entirely in two classes that the
project's own published patch notes predict. A compiler patched to force matches
would produce *arbitrary* differences under a stock one, not two clean classes.
Reproduction by a third party is what makes that distinction visible.

## symbols_to_splat.py

```sh
export DECOMP_GAME_DIR=./symbols
./tools/symbols_to_splat.py --all --locals --outdir symbols/splat
./tools/symbols_to_splat.py --binary OV11.OVL --locals -o ov11.txt
```

Pass `--locals` for overlay binaries — OV11 has only 6 `GLOBAL` symbols; its
functions are all `LOCAL`.

The converter refuses to guess. A splat symbol file is one flat namespace; an ELF
symtab is not. Two translation units can each define `static int tbl`, and this
particular SLUS embeds a copy of its `ov02` section at `0xa00000`, so generic
local names (`buffer`, `copyframe`, `pi`, `tbl`, `force_to_data`) genuinely hold
several addresses. Those are dropped and counted rather than emitted with
whichever address came first — 361 such names in `main` alone. It also drops `$L*`
assembler labels and anything below the EE load floor, since the `Vu0*`/`Vu1Mem*`
family are offsets into VU memory, a different address space.

Cross-checked against an independent decomp's own symbol file: **5,927 names in
common, zero address disagreements.**

## Data-format knowledge is a decomp input

The second gap above deserves more than a footnote, because it is the one that
determines whether a finished decomp is readable or merely correct.

Large stretches of a game binary exist only to parse its own data — archive
decompression, texture uploads into GS memory, script-container loading, the
sound driver's RPC surface. Holding the format specification lets those functions
be typed and named with confidence. Not holding it means inferring from register
traffic, and the byte gate will happily pass either way. Two people can produce
byte-identical C for an archive decompressor and only one of them can tell you it
is a word-dictionary coder whose table is the top 30 words by frequency with ties
broken by first occurrence.

The same asymmetry governs testing. A matching decomp emits an ELF; it cannot run
anything by itself. Checking that a decompiled loader actually loads needs real
files and a known-good reference implementation.

So format reversing is not a parallel hobby next to a decomp — it is an input to
it, and the input that the gate cannot supply.

## Running an i386 ee-gcc on an arm64 Mac

The SCE ee-gcc 2.96 binaries are 32-bit Linux ELFs. There is no native path on
Apple silicon; this is the one that works.

```sh
colima start --cpu 4 --memory 6
docker run --privileged --rm tonistiigi/binfmt --install i386,amd64
```

The `binfmt` step is **required** — without `qemu-i386` registered, a
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

- The `ee-gcc` driver cannot exec its own sub-programs through the loader trick.
  Drive `cc1`/`cc1plus` directly and preprocess separately. `clang -E -P` on the
  host is fine — only the token stream matters, and codegen does not depend on
  line markers at `-O2` without `-g`.
- Mounts must be under `$HOME`; colima does not mount `/private/tmp`.
- Colima may come up `aarch64` even with `--arch x86_64`. That is fine; the binfmt
  handler does the work.
- **Qiling is a dead end here.** `qiling` 1.4.6 installs but its keystone binding
  fails to load, and upgrading `keystone-engine` does not fix it.

The compiler itself comes from [decompme/compilers](https://github.com/decompme/compilers)
(`ee-gcc2.96.tar.xz`). Nothing in this kit vendors it.

## Acknowledgments

[splat](https://github.com/ethteck/splat) and
[spimdisasm](https://github.com/Decompollaborate/spimdisasm) for the symbol-file
format this interoperates with, and [decompme/compilers](https://github.com/decompme/compilers)
for archiving the historical toolchains that make any of this checkable.

## License

MIT — see [LICENSE](LICENSE). Symbol addresses are facts about a binary you own;
the game itself is not included and remains the property of its rights holders.
