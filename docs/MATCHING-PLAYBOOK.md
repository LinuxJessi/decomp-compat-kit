# Matching playbook: GCC 2.96 on the Emotion Engine

Idioms and working rules collected while matching Xenosaga I (main, OV01,
OV10, OV11, OV12) and Xenosaga III (OV04) against SCE ee-gcc 2.96 at `-O2`.
Each idiom names the function that proved it under a whole-file gate; they are
observations about this compiler with these flags, not laws. A function that
resists them needs another hypothesis, never an impossibility claim.

## 1. The loop

- **The tool decides.** A verdict is compiled bytes compared with the original
  (`match_kit.py check`), never "verified manually". Every compile goes through
  the same command, so a variant nobody checked is not a form and not evidence.
- **One changed hypothesis per form.** Line 1 of each check names the compiler
  and the verdict; line 2 the number of differing instruction words. A form
  whose bytes equal an earlier form's is a wasted form: change something else.
- **Stop rules.** A ceiling of forms per tier; a no-progress stop after K
  charged forms without a lower `diff_words` or a higher TU match count
  (K = 4 worked); `UNVERIFIABLE` (a reference the verifier cannot resolve by
  name) charges nothing and two of them stop the loop, because retrying cannot
  change a tooling verdict; `REGRESSION` (another function in the TU stopped
  matching) means undo that edit.
- **Byte identity is necessary, not sufficient.** A match whose source uses
  inline `asm`, `.word`, pointers built from a base plus a byte offset,
  `#define` offsets, raw address casts, placeholder names (`argN`, `unk_`,
  `tmp`, `field_0x`, `D_xxxxxxxx`), `goto`, `do { } while (0)` barriers, or
  `volatile`/unused locals that only pin registers is reported as `CHEAT`. When
  only an offset form matches, say so: a struct is missing and belongs in the
  header work.
- **Escalate with a changed hypothesis and the previous worker's words.** The
  next tier gets the exact earlier forms and the next hypothesis; a same-tier
  retry with a changed hypothesis is valid when budget remains. The last tier
  records, it does not relaunch.
- **A fresh sandbox per relaunch**, seeded with the best form; the old one
  stays as evidence. Never edit the budget or form records.
- **Form histories are the reviewer's receipts**: what closed each function,
  which alternatives moved bytes, the first differing instruction. Without them
  every declaration change looks unproven.

## 2. Evidence before the first form

- **A card of at most 2 KB** beats reading the file: identity, size class,
  callers and callees from the symbol table and the disassembly, argument
  registers read, member offsets and widths per base register, strings, floats,
  a COP2 flag, prototypes of two or three matched siblings in the same TU, the
  declarations the names resolve to. It cut a Haiku-class worker from 2–3 M
  tokens per function to 0.3–0.8 M. Sibling source files run 15–184 KB; never
  read one whole.
- **Named disassembly**: every `jal` target and every `%hi/%lo` or `$gp`
  global named from the real symbol table, with `lui`/`addiu` pairs folded into
  addresses across every loaded segment including `.bss` (the memory size, not
  the file size, decides what is an address).
- **Triage first** (`tools/triage.py`): trivial / easy / medium / hard / cop2 /
  huge, the `-G0` or `-G8` witness, `lq`/`sq` presence, switch tables, the
  sibling-call shape. Easy functions start on the cheapest worker with a
  ceiling of 4; a cheap worker failing at its ceiling was the largest single
  cost in the first wave.
- **Nearest already-matched function**: the frame layout, literal-pool
  placement, delay-slot filling and COP2 spelling of a matched function of the
  same shape transfer; its semantics and names do not.
- **Published prototypes are leads.** A published `unsigned short` argument had
  to be `int`, and the `andi` in the original bytes proved it. The bytes decide.

## 3. Codegen idioms (ee-gcc 2.96, `-O2`)

Control flow and layout
- **Basic blocks emit in source order.** Retail block layout pins source
  structure; `default:` placement inside a switch matters; a case body placed
  after other code means the source used `goto label` (HaKox, OV04).
- **Cross-jumping** merges identical tails; the surviving copy is the one whose
  block is physically last (fall-through entry).
- **One `jal` reached from two branches is a `goto` to a shared call site**
  (`dataFileLoadWepSp`).
- **Signed range check `0 <= x < 8` as nested ifs** gives two branches and two
  loads (`lb` then `lbu`); the `&&` form folds to one unsigned compare
  (`unitCmdAtkSub`).
- **A trailing `printf` becomes a sibling call unless something follows it**;
  a `do { printf(...); } while (0)` macro keeps it a plain call.
- **An equality-chain `switch` needs an unsigned selector**; signed gives an
  `slti` tree (`MCamMove`).
- **A sign flip with one store is a ternary** `x = (x < 0.0f) ? a : -a;`;
  if/else or a temporary gives two stores (`MCamShakeProc`).
- **Structured in-place update beats goto mirrors**: `*value += step; if
  (*value > max) *value = min; return *value != old;` (`valUD`, `valLR`).
- **Push/pop stacks**: `if (ok) { s[depth++] = v; } else { printf(err); return
  1; } … return 0;`, a `short` passed as `value & 0xFFFF` (`cmdRpush`).
- **Loop peeling and register order**: `short len = 0` declared before
  `char *text`, then `while (text[len++] != 0) {}` puts `len` in `$16` and
  `text` in `$17` (`cmdPrint`); `for (; cond;)` with the int initialised
  outside reproduces peeling where `while`+flag, `do`-`while`+`if` and a
  comma-`for` all change the branch layout.
- **Guard-then-load, and take the decompiler's address arithmetic literally**:
  a pointer set in a `for` initialiser is emitted before the loop guard; retail
  loaded the table pointer after the guard and formed it as
  `i * 0x80 + (int)base`, where `&base[i]` allocates a different register
  (`FUN_00a986d0`, OV04).

Scheduling and statement order
- **Statement order is the scheduler's input.** Stuck at ±1 instruction:
  permute statement order, declaration order, expression operand order and
  sweep; a build is one or two seconds, a 24-permutation sweep is cheap.
- **Argument order in the call is the RTL emission order** for argument setup;
  on scheduling ties this decides instruction order. Ghidra renders float
  arguments first because of `$f12`; the C prototype may differ.
- **Stores: the compiler schedules first the stores whose register is used for
  the last time, and keeps source order within such a group** (two T3
  functions closed on this; write the `0x28` store last, the `0x14` store of
  `1` before the `0x00` store). Try store order before a structural rewrite
  when the diff is a store permutation.
- **Reading an argument into a named local before a global store** closed two
  of the first matches.
- **Ghidra's statement order is not source order** for independent stores; it
  re-sorts them. Vectors match only as x, y, z, w (`FUN_00a9a1d8`).
- **Callee-saved swaps that survive every statement order** (`calcBp`,
  `calcSkillInit`, `calcStatClr`, `unitCmdItem`) are the signature of a
  declaration or type difference on an argument or a field, not of statement
  order; the next hypothesis changes a type, never the order again.

Types, widths and addressing
- **Signed vs unsigned field declarations** change which load (`lh`/`lhu`)
  anchors where; both can exist for one field if the source mixes casts.
- **Parameter width receipts**: `short tecId` emits `sll`/`sra` where the
  original has `daddu`; `int` matches (`calcTakeEpGet`). A changed published
  prototype needs that receipt in the form history.
- **Subscript vs pointer arithmetic changes hoisting**: `*(unit->wpnActor +
  (i & 3))` through a shared local keeps the load at +12; `wpnActor[i & 3]`
  hoists `unit+12` and loads at +16 (`unitRemoveActor`).
- **Packed colour as a union gives the aligned `lw`/`sw` copy** (`union
  MenuColor { unsigned int word; MenuRGBA rgba; }`) and removes a `$16` spill;
  the RGB variant keeps `lwl`/`lwr` (`menuStatSprite`).
- **A pointer parameter, not by value**: `daddu $18,$4,$0` then `ldl`/`ldr`
  from `$18` means the callee reads the caller's struct in place; the by-value
  form copies it onto the stack (`MMvPlay2`).
- **Pointer-typed buffer addresses** (`void *stream_base`) fix hoisted
  constants, and a reused local (`display_buffer += 0xC000`) gives the
  `lui`/`ori`/`sw` pairs in one register.
- **Chained float assignment is visible in the bytes**: `p->x = p->y = 0.0f;`
  routes the zero through an FP register (`mtc1 $zero,$f0` hoisted, then
  `swc1`); separate statements store integer `$zero` (`sw`) (`FUN_00a98450`).
- **Callee-saved float registers** (`$f20`+) are used when a float constant
  has three or more uses spanning calls; fewer uses are rematerialised with
  `li.s`. Losing a `swc1 $f20` in the prologue means you merged or split calls
  the original did not.
- **Division by a constant emits a real `div` plus `beql`/`break 0,7`**; no
  magic multiply. That is a 2.96-on-EE signature; `div.s` for float division
  rules out fast-math flags.
- **Fixed addresses are named constants** `#define NAME ((void *)0x...)` with
  the `lui`/`ori` citation; a bare cast in code is rejected.
- **Doubles are soft-float** (`__adddf3` family): model `double` or it never
  matches.

128-bit and COP2
- **`lq`/`sq` in the original is one of two things**, told apart by the bytes:
  both addresses forced into registers with `addiu` and the quadword moved
  through a hard-coded scratch GPR is the SDK `sceVu0CopyVector` inline-asm
  shape (`exact_c_with_asm`); displacements folded into one base with an
  allocator-chosen register is compiler output of a 128-bit native type
  (`typedef int v4 __attribute__((mode(TI)))`, `exact_c`). A copy that compiles
  to `ldl`/`ldr`/`sdl`/`sdr` where the original has `lq`/`sq` is a missing
  alignment, not a reason for assembly.
- **128-bit literal tables**: a `static` array of the TI type copied whole
  places the `lq`/`sq` where retail has them; a struct of four ints or a plain
  union is BLKmode and gives no `lq` (`fnSOLB_DP000`).
- **Two inline-asm copies that must keep their own addresses but share a
  merged tail come from one `static __inline__` helper called twice**; two
  separate `__asm__` statements never merge. `sqc2 $vf0` is the clear idiom,
  not `sq $0`; `"m"` constraints become offset addressing where the originals
  used `"r"`.
- MMI and VU-macro instructions appear only via inline asm or builtins in this
  compiler, so they cluster in graphics and sound layers, not game logic. A
  function containing them is routed as `cop2` from triage.

Stack and frame
- **Declare locals in the original's stack-slot order** (one `int pass` for
  three passes declared after `int count` lined up every slot).
- **A frame that saves `s0–s4` only, with the struct read through a pointer**,
  says the parameter is a pointer.

Large functions
- The permuter is of no use at 2,500–5,000 instructions. What worked: a
  compile-and-compare probe loop (under a second per form, relocated fields and
  branch offsets masked on both sides, rows aligned with `difflib`, variants
  ranked by matched rows) and the compiler's own allocator and scheduler dumps
  (`.09.loop`, `.10.cse2`) to see which pass moves the copy that will not sit
  still (`CardMainProc` 4,852 instructions, `_JobEnemy` 2,570).

## 4. Review

Review is a model of another family, or a person, who sees the function, the
TU's published idioms, the published lines naming the function, the scope
hunks outside the function and the form history. What it rejects, in the order
it came up: a declaration or comment left for a function still `INCLUDE_ASM`;
a changed published declaration without a byte receipt; an absolute address in
a comment the evidence does not establish; a byte literal in a comment without
its `.rodata` citation; a parameter the body never reads; names that contradict
the demonstrated role; byte-offset pointer arithmetic and integer-cast bases
even with receipts. A split two-family panel is a rework, not a third opinion.
A rename-only adjustment must pass the byte check again.

## 5. Costs measured

| worker | function | forms | tokens |
|---|---|---|---|
| Haiku-class | 32-byte Java native | 2 | ~54 k |
| Sonnet-class | 96-byte function | 9 | ~184 k |
| Haiku-class at its 4-form ceiling, no match | | 4 | the largest cost in the wave |

Exploration (reading source files whole) was 21 % of all tokens in the first
test; the evidence card removed most of it.
