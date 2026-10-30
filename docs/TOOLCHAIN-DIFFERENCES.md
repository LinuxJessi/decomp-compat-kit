# Stock toolchain vs the toolchain that built the game

What the Xenosaga I matching decomp learned, patch by patch, about how the
archived SCE ee-gcc 2.96 differs from whatever built the retail binaries. Every
entry below was found the same way: a function whose C was right under one
toolchain and whose bytes still differed, traced to a specific compiler or
assembler behaviour, then confirmed across the whole image. The project is
[OpokXeno/xsg-i-decomp](https://github.com/OpokXeno/xsg-i-decomp); the full
reports live in its `reports/` directory under the names given here. The
measurements are reproducible with `tools/probe_toolchain.py`.

Why this matters outside Xenosaga: the same archived compiler
(`decompme/compilers` `ee-gcc2.96.tar.xz`) is the starting point of most PS2
matching decomps of Japanese titles, and the retail builds of other games will
hit the same differences. Knowing the catalogue turns "40 functions differ"
into "two known assembler behaviours and one compiler rounding bug".

## What "stock" is

| tool | identity | sha256 |
|---|---|---|
| `cc1` | GNU C 2.96-ee-001003-1 (ee), the archive member, unpatched | `59ec92b3f9f3513e0633331af304733e3094de30884e662a8cb584a51c1c42b5` |
| `cpp0` / `ee-gcc` | same archive, used unmodified by everyone | `f6f6b598…`, `b8c284d1…` |
| `ee-as` | GNU assembler 2.10-ee-001003-1, unpatched | `b8cfdb6ecb6931642914020f92a62e653378f57230eed4895f69afa050d47b5e` |
| archive | `ee-gcc2.96.tar.xz` from decompme/compilers | `0590d2ca9da8f5903889d66761220d14b47a8d14ba987ca53db84a1650a1fd0a` |

The `cc1` is an **authentic SCE build**, not a later rebuild: it is unstripped,
its stabs name the build directory `/usr/local/sce/ee/gcc/build/gcc/`, the host
compiler was Cygnus GNUPro 99r1 on glibc 2.1.3 (Red Hat 6.x), and it carries
SCE-only options (`-fgenobscured`, `-attr-info`) and source files
(`cppobscure.c`, `range.c`). No public source exists for the 2.96-ee line; every
preserved SCE EE GCC/binutils source stops at 2.9-ee-991111
([SSXModding/ps2-ee-toolchain](https://github.com/SSXModding/ps2-ee-toolchain)
commit `b595ded6`). So the differences below were established on the binary and
fixed with byte patches, each recorded with old/new bytes and a hash of the
result (`reports/gcc296-source-hunt.md`).

## How a difference shows up in this kit

`verify_decomp.py` and `match_kit.py` classify every mismatch. Read the class,
then look the cause up here:

| class | first suspects |
|---|---|
| `hazard-nop` | B1 hazard nops; B9 delay-slot filling; A2 short-loop padding |
| `reorder` | A3–A8 alias analysis; B9 delay-slot swapping; source statement order (see the playbook) |
| `mixed` | B1 + A3 together, the common case in the first 63-TU run (15 of 40) |
| `immediate` | A1 literal rounding; B5 `la` expansion; B7 `break` encoding |
| `delay-slot` | B9: the stock assembler filled a delay slot the retail one left as a nop (the object is one word shorter per slot) |
| `size-diff` | A2 padding; B10 trailing alignment; a real structural difference |
| `unexplained` | B6 `move` encoding when assembled with modern GAS; B8 jump-table macro; otherwise the source |

## A. Compiler (`cc1`)

**A1. Decimal literals: stock truncates, retail rounded** (`reports/double-literal-rounding.md`, `realconv`).
GCC converts a decimal literal with its own e-type software emulator
(`REAL_ARITHMETIC` is unconditional on MIPS), never the host's `strtod`. In the
stock 2.96 `cc1` the final rounding in `asctoeg` and the narrowing in `etoe24`/
`etoe53` are gated on the R5900 target bit (`target_flags & 0x02000000`, tested
at 114 sites, 11 of them in `real.c`) and the gate selects *no rounding*:
GCC's `ROUND_TOWARDS_ZERO` path, meant for FPUs that only truncate. Every
constant whose correctly rounded value lies above the exact decimal comes out
one ulp toward zero (`0.1` → `0x3fb9999999999999` instead of `…9a`,
`0.1f` → `0x3dcccccc`), every other constant is right. The retail binary agrees
with Python's `float()` on all of them. Six single-byte patches turn the six
*conversion* gates into unconditional jumps; the five arithmetic-folding gates
are left alone. A `float` table written without `f` suffixes
(`static const float PIo2[] = { 1.5703125000e+00, … }`) needs the `etoe24` gate
too, not just `asctoeg`. No command-line switch clears the bit.

**A2. Short-loop padding** (`reports/cc1-lengthen-loops-lp7.md`, `lp7`).
`mips_r5900_lengthen_loops` pads short loops; the stock gate and loop bound are
4 instructions, ten original objects need 6. Two single-byte patches.

**A3. `LO_SUM` scalar addresses in alias analysis** (`reports/cc1-fsv-clause1-losum.md`, `fsv1`; `reports/cc1-fsv3-alias.md`, `fsv3`).
`fixed_scalar_and_varying_struct_p` decides that a scalar memory reference and
a struct reference with a varying address cannot alias. Stock `rtx_varies_p`
calls a split `lui`/`%lo` global address (`lo_sum (reg) (symbol_ref)`)
varying because its register operand is a pseudo, so a store to a split global
scalar blocks every later struct-field load in `sched1`. Retail schedules such
loads above such stores (`CardMainProc`, OV10). The patch treats a `LO_SUM`
address as non-varying in both clauses, which is upstream GCC's later rule.
`fsv3` additionally gives the `MEM` of a non-function, non-small-data
declaration its type alias set in `make_decl_rtl`.

**A4. gcse load-PRE transparency** (`fsv4`): `compute_transp` passes
`rtx_varies_p` instead of `rtx_addr_varies_p`, so an in-struct store through a
varying pointer no longer kills a fixed scalar load (`MenuShopCore`).

**A5. `char` sign-extended to 64 bits** (`fsv5`, "qisi"): expanded as `lb` plus
a separate in-place `extendsidi2`, never one `extendqidi2_insn`. The extra
(invisible) insn is counted by the short-loop pass, so it changes padding too.

**A6. Host heap garbage reached codegen** (`fsv6`): `mips_legitimate_address_p`
evaluated `INTVAL (xplus1) > 0` on non-`CONST_INT` operands and
`gen_mem_addressof` read an uninitialised alias-set word, so the output depended
on the lengths of line-marker paths and on ASLR. Symptom worth knowing anywhere:
**a diff that changes when you move the source tree** is this class. The patch
makes `sym(reg)` addresses always legitimate and gives an address-taken variable
alias set 0.

**A7. Struct store through a pointer loaded from global S kills a later load of
S** (`fsv7`, "SameG"): an empirical rule fitted on 27 sites; no GCC source
mechanism is known. **A8.** A small-data global with a non-zero initializer gets
its type alias set like a non-small-data one (`fsv8`).

**A9. Sibling calls.** A function whose original keeps a real `jal` plus
epilogue where 2.96 emits a tail `j` was built with
`-fno-optimize-sibling-calls` (the project's `nosibcall` profile). The triage
tool flags the shape as `nosibcall_candidate`.

**A10. Flags are per translation unit.** Xenosaga I: `-O2`, `-G8` for most of
the main executable and `-G0` for a minority of TUs and for the overlays, plus
`-fno-builtin` from the driver. Xenosaga III: `-O2 -G0 -fno-strict-aliasing`
proven for OV04 by two globals of 8 and 4 bytes addressed absolutely in retail;
`-fno-strict-aliasing` is load-bearing (without it one load per
aliasing-sensitive region schedules early). The `-G` witness is cheap: a
`lui`/`lw` pair on an object of 8 bytes or less means `-G0`; `$gp`-relative
access means `-G8` (`tools/triage.py` reports both).

**A11. Doubles are soft-float.** The EE FPU is single precision; a function
calling `__adddf3`/`__muldf3` has `double` in its source, model it so or it
never matches.

## B. Assembler (`ee-as`)

**B1. R5900 hazard nops** (`reports/B01-historical-assembler.md`,
`reports/as-la29-vsqrt-nolabel.md`). GNU as 2.9-ee-991111 (`append_insn`)
inserts hazard nops between an FP compare and the branch on its condition and
between `mtc1` and a dependent `cvt`; the archived 2.10 omits them; the retail
objects have them. So the retail assembler was a 2.9-era `ee-as`, and the
project builds one from the ps2-ee-toolchain source. On the archived 2.10
assembler, `-mcpu=4000` restores the `mtc1`→`cvt` nop (the Xenosaga III kit's
route). Thirteen of the first forty stock differences were exactly this.

**B2. Labels and hazard nops** (`nolabel`): LA29 moved pending labels to after
the inserted nops; retail has them before. One loop removed.

**B3. `memcpy` → `memmove` in relaxation** (LA29): an overlap bug in the old
gas, a crash and corruption fix, not codegen.

**B4. `vsqrt` encoding** (`vsqrt`): the 2.9 opcode table had bits 21–22 of
`vsqrt` as `00`; every retail word, as 2.10 and ps2dev binutils have `01`.

**B5. `la $t,CONST($b)`** (`reports/as-la-imm16.md`, `laimm`): 2.9 expands it
as `li tmp,CONST; addu $t,tmp,$b`; 2.10 and retail assemble one `addiu`
(upstream binutils `0c11417f`, 2002). Backported. Still open in the same
family: two `li` pseudo-ops that retail has as `ori` where the pinned assembler
emits `addiu` (`fadeObjDraw`).

**B6. `move`** expands to `daddu` in both historical assemblers (2.9 and 2.10
prefer `daddu` in their opcode table) and to `or` in modern GAS. A decomp that
assembles compiler output with ps2dev binutils gets a different word for every
register move. Measured on four register pairs, eleven substitutions across the
nine calibration cases.

**B7. `break 7`** (the division-by-zero guard GCC emits after `div`): the
archived 2.10 encodes the code in the upper 10-bit field (`0x0007000d`), retail
in the lower field (`0x000001cd`); `break 0,7` produces the retail encoding.

**B8. The jump-table load macro `lw $d,$Lnn($idx)`**: the archived assembler
expands the internal address add as `daddu` (64-bit address mode), retail used
`addu`. The Xenosaga III kit expands the macro explicitly
(`lui/addu/lw`, `fix_asm.py`); the Xenosaga I project assembles with
`-EL -m5900 -mabi=eabi`.

**B9. Delay-slot filling.** The game's C objects reassemble byte-identically
only with the per-object flag `-g` on a swap-mask-corrected 2.9 `ee-as`: `-g`
sets `mips_optimize = 1`, which stops the assembler from swapping an
instruction into a branch delay slot, and keeps the local `$L` labels in the
object symbol table (user decision 2026-10-27, two TUs excepted). In other
words the retail assembler did not fill delay slots; what you see in the delay
slots is the compiler's own scheduling. A `reorder` at a branch is this before
it is anything in the source.

**B10. Trailing section padding.** Historical gas returns the unrounded section
size for `elf` targets and emits no trailing alignment padding; modern gas needs
`-no-pad-sections` for the same object size. Matters for whole-TU and section
comparisons, not for per-function ones.

**B11. Local labels.** Pass `-L` when the `.rodata` objects (strings, jump
tables, float pools) must keep their `$L` labels for a per-object comparison.

## C. The rest of the pipeline

The retail link order and TU boundaries are not in the compiler at all: the
`.euc.c` names in assertion strings (EUC-JP sources) give the TU skeleton, and
GCC 2.96 emits basic blocks in source order, so block layout pins source
structure (see `MATCHING-PLAYBOOK.md`). The project links with ps2dev binutils
2.45.1 `ld`/`objcopy` and assembles data objects and retail blobs with modern
GAS; only the game's C and the runtime C go through the historical assembler.
VU microprograms are a separate unit kind (DVP assembler, pair-by-pair audit),
and IOP modules (SSD.IRX, RSSD.IRX: ELF32 MIPS-I, type `0xff80`, `.iopmod`)
have their own compiler and must never be judged with the EE profile.

## D. Identities the project pins

| payload | sha256 | what it is |
|---|---|---|
| `cc1` fsv3 variant | `790f9f8ca43d2de4cb3621730d287f779f18102f36e2925768d25245b36f5625` | realconv + lp7 + fsv1 + fsv3 |
| `cc1` fsv8 variant | `575db2bbef59132765136aa2525722501d717abe8381bf706645962af6c6188f` | the above + fsv4…fsv8 (current contract) |
| `ee-as` LA29-vsqrt | `33dae0133f60ee05179464f9defa83b1e391e4e6e4d3f8f3b71b43de10ec274d` | 2.9-ee-991111 + memmove + vsqrt |
| `ee-as` 2.9 plain, rebuilt | `8cf3508116e85cacf62e1958c9a1592df4e7761363c3ea5efe47ca5f7fab1f20` | pristine ps2-ee-toolchain `b595ded6` build (x86-64 host) |

A rebuild of the same source on another host hashes differently and assembles
identically; pin behaviour (`probe_toolchain.py`) as well as bytes.
