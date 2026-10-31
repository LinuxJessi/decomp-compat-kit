# CodeWarrior for PlayStation, read for the PS2 titles it explains

Two of the six games in `docs/CORPUS.md` were built with Metrowerks CodeWarrior
(Silent Hill 2, Yakuza), and the kit knew that only from a `.comment` string and
an `MWo3` header it could not fully read. No CodeWarrior for PS2 release is
preserved anywhere this project can reach. What is preserved is its direct
ancestor: **CodeWarrior for PlayStation Release 4 (1998) and Release 5 (1999)**,
the PS1 product, on the Internet Archive item `CodeWarriorForPlayStation` (two
redump images, raw 2352-byte sectors). The same Metrowerks MIPS back end,
linker, overlay linker, runtime sources, linker-command-file language and
manuals carried over to the PS2 product with the version number continuing, so
these discs document most of what a PS2 CodeWarrior binary contains. This file
is what they taught, with the documented parts and the measured parts kept
apart. The tools that use it: `tools/unwrap_image.py` (the overlay record),
`tools/fingerprint_elf.py` (the startup tell, the version lineage), and the MCP
server's compiler-family check.

## 1. What the discs are

| | Release 4 | Release 5 |
|---|---|---|
| CD label, date | `CW_PSX_R4`, April 1998 (Apple HFS hybrid) | `CW_PSX_R5`, Welcome dated 29 March 1999 |
| IDE | CodeWarrior IDE 3.0 | IDE 3.3 |
| MIPS compiler plugin | `cc_mips.dll` 2.44.14.0 (15 Apr 1998), "MIPS Compiler for PlayStation" | `cc_mips.dll` 2.1.87.0 (6 May 1999) |
| MIPS linker plugin | `ln_mips.dll` 2.44.16, "MIPS Overlay Linker for PlayStation" | `ln_mips.dll` 2.1.93.0 |
| Compiler release notes | "CW PRO MIPS Compiler 2.39, 19-Feb-1998" (author Pascal Clève) | documentation release notes 99.4.30 |
| Assembler plugin | `asm_playstation.dll` (release notes 0.1, July 1997: PsyQ-compatible syntax) | 1.5 build 0002 |
| Also on the disc | SN plug-in adapter (`SNPSXCompiler.dll`, `SNPSXLinker.dll`, `ccpsx`), ECOFF/PsyQ library importer, `PSComUtil`, `MWDebug` | plus the LCF generator prelinker `PSLCFPrelinker.DLL`, `SpriteWarrior`, MWDB control libraries |
| Runtime sources | `MIPS Support/` (`__start.c`, `_psstart.cpp`, `__rts_info_t__.h`, exceptions, MSL C), `PlayStation Pro Support/` (`_psstart.c`, `MWUtils`, `MWDebugIO`) | the same plus `MIPS Support/Linker Command Files/`, `StartUp/` |
| Manuals (PDF) | Targeting PlayStation OS (210 pp), C Compilers Reference MIPS (162 pp), Plugin API, Debugger, IDE | Targeting PlayStation (300 pp), C Compilers Reference (214 pp), PlayStation Assembler Reference (90 pp), MSL C / C++ |
| Examples | `OverlayDemo - solution` with built `Overlay1.bin`, `Overlay2.bin` (`MWo1` records) and a link map `.xMAP` | `OverlayDemo`, `Clear - bss` (reads `bssSize` from the header), `Coredemo` |

The compiler is an IDE plugin, not a command-line program: there is no
`mwccmips.exe` on either disc, and the plugin needs the Win32 IDE (`IDE.exe`)
to run. So the discs give knowledge, not a compiler lane: **this kit still
compiles only with SCE ee-gcc** (see §8).

### The version lineage

The Metrowerks MIPS compiler numbers restart between R4 and R5 and then run
into the PS2 product:

| version string | product |
|---|---|
| `2.44.14` (DLL resource), "2.39" (release notes) | CodeWarrior for PlayStation R4, 1998 |
| `2.1.87` compiler, `2.1.93` linker | CodeWarrior for PlayStation R5, 1999 |
| `MW MIPS C Compiler (2.4.1.01)` in `.comment` | CodeWarrior for PlayStation 2: Silent Hill 2 (2001) and Yakuza (2006) carry the same string |

`fingerprint_elf.py` prints the row a vendor string belongs to. Two titles
five years apart with the same `2.4.1.01` says the PS2 compiler stopped
changing early; it does not say the two games used the same linker or SDK
(their `PsIIlib` banners differ: 2.2.x against 3.0.0).

## 2. The overlay record (`MWo` + version)

Both manuals print the record (R4 "Targeting PlayStation OS" listing 7.1, R5
"Targeting PlayStation" listing 8.2); the R5 documentation release note revises
one field:

```c
typedef struct OverlayHeader {          /* R4 and R5 manuals; R5 note renames the last three */
    char          flag[3];              /* 'MWo' */
    char          version;              /* '1' on the R4 demo overlays */
    unsigned long overlayID;            /* same ID found in DWARF (AT_overlay_id) */
    unsigned long loadAddress;          /* where to load the overlay */
    unsigned long TextSize;             /* size of the executable part */
    unsigned long DataSize;             /* size of the data part */
    unsigned long StaticInit;           /* void (*__static_init)(void); call first if non-zero (C++) */
    unsigned long unused2;              /* R5: bssSize, "a previously-unused field has been activated" */
    unsigned long unused1;              /* R5: entryPoint, unused */
    char          overlayName[32];
} OverlayHeader;                        /* 0x40 bytes; the R5 note calls it MWOverlayHeader */
```

The R5 LCF generator writes it by hand at the start of every overlay segment,
which settles what the sizes measure:

```
WRITEB 0x4D; WRITEB 0x57; WRITEB 0x6F; WRITEB 0x31;      # "MWo1"
WRITEW OVERLAYID(..Overlay1);                            # overlay ID
WRITEW ADDR(..Overlay1);                                 # load address
WRITEW _Overlay1SegmentTextEnd - _Overlay1SegmentStart;  # text (from the segment start, i.e. the header included)
WRITEW _Overlay1SegmentBssEnd - _Overlay1SegmentDataStart; # data (bss included, R5)
WRITEW _Overlay1_SINIT;                                  # static init pointer: set to '.' right after .data/.rodata/.vtables
WRITEW 0x0;  WRITEW 0x0;                                 # unused ; entry point - unused
WRITEB 0x4F; ...                                         # the name, 32 bytes
GROUP(Overlay1.bin) (.text) ... (.data) (.rodata) (.vtables) ... (.bss) (COMMON)
```

Measured on the R4 demo: `Overlay1.bin` is `MWo1`, id 1, load `0x80030000`,
text `0x24` (exactly `foo1`'s size in the `.xMAP`), data 0, the rest 0, so the
R4 linker counted text without the header. A `(RWXO)` memory segment with
`> filename` makes the linker emit the segment as this record plus bytes
instead of an ELF program header; the `O` is the only attribute the linker
reads, the others go to `p_flags`.

**Version 3, the PS2 record** (unwrap_image.py reads it; measured on all 54
Silent Hill 2 stage overlays `GX/GY/GZ/*.BIN` and on Yakuza's `MAIN.BIN`):

| field | SH2 `gx_aee` | SH2 `gx_toi` | Yakuza `main.bin` | all 55 samples |
|---|---|---|---|---|
| version | 3 | 3 | 3 | 3 |
| overlayID | 8 | 1 | **0** | SH2 ids 1..51 (the `XXX/YYY/ZZZ` placeholder stages share 13); 0 is the root program, the index the manuals reserve for it |
| loadAddress | `0x1f0bc00` | `0x1f0bc00` | `0x100000` | the header is loaded at this address: file offset 0 maps to it |
| text, data | `0xcc0`, `0xa80` | `0xdc0`, `0x380` | `0x2e0d20`, `0x147420` | **file size == 0x40 + text + data** in 55 of 55 |
| word 5 | `0xe00` | `0x200` | `0x9b500` | the bss size (Yakuza's known bss; SH2's values are the memsz the ELF program headers reserve) |
| words 6, 7 | `0x1f0d380`, same | `0x1f0cd80`, same | `0x58fd00`, `0x58fda8` | both equal `load + file size` (the start of .bss) on every SH2 overlay (C); on Yakuza (C++) two addresses `0xa8` apart beyond the image |

So version 3 moved the bss size up one slot and kept two address words. By the
R5 generator's construction the static-init pointer is the address right after
the data part, which is exactly `load + file size` for a C overlay, and a C++
title would have a table there; `0xa8` is 42 words. That reading of words 6-7
is a hypothesis from the construction, not a proof: the tool reports them raw
with a note. `Clear - bss` in R5 shows the intended loader: `bload` the file,
`MWNotifyOverlayLoaded(addr)` for the debugger, then
`memset(addr + fileSize, 0, header->bssSize)`, because from R5 on the `.bss`
is no longer stored in the file ("fast-loading overlays"). Every PS2 sample
agrees with that: the file ends where `.bss` begins.

## 3. What the Metrowerks linker leaves in a binary

**Linker-generated symbols.** `__rts_info_t__.h` (R4 and R5) declares them as
objects in `.data` ("otherwise we would not be able to load GP"), and the R4
`.xMAP` shows them laid out contiguously as 4-byte `.rodata` objects in this
order: `__static_init, _gp, _sbss_size, _bss_size, _sdata_size, _data_size,
_rdata_size, _fsbss, _fbss, _fsdata, _fdata, _frdata, __exception_table_start__,
__exception_table_end__, _ftext, _stack_addr, _nbtvectors, _stack_size,
_heap_size, _etext, _edata, _end, _fsdata_ROM, _fdata_ROM, _sdata_ROM_size,
_data_ROM_size, _scratchpad_size, _overlay_section_addreses[]` (the typo is
Metrowerks'). The R5 generated LCF instead writes each as `WRITEW(value);
WRITEW(0);` pairs inside `..data`, followed by `__static_init = .;
STATICINIT(ROOT)` and then `_overlay_section_addreses = .; WRITEW <root addr>;
WRITEW <overlay addr>; ...` (one word per overlay, index = overlayID, index 0 =
root). The manual says to read them as immediates (`lw t2,_end` fetches the
value, not the address) and that `_heap_size` / `_stack_size` are in kilobytes.
`_gp = _fsdata + 0x8000` and `.sbss` must follow `.sdata`: "`_gp` is the only
symbol known to the linker. Do not change the name."

Whether a PS2 CodeWarrior binary keeps this block was not settled: Silent Hill
2's `SLUS_202.28` has no run of overlay load addresses that could be the table
(the 50 copies of `0x1f0bc00` at a 40-byte stride are a stage table of the
game's own), and Yakuza's loader has a plausible but unproven cluster
(`0x100000, 0x167c80, 0x62b300` = the three segment starts) at `0x153490`.
Open; a decomp of either title should look for it with its own symbols.

**The LCF language** (R5 manual chapter 6, and the two sample files under
`MIPS Support/Linker Command Files/`): `MEMORY { name (RWX|RWXO) : ORIGIN =
addr, LENGTH = n [> file] }`, `SECTIONS { ..name : { ... } > region }`,
`GROUP(ROOT|overlay) (.section)`, `STATICINIT(ROOT|overlay)`, `EXCEPTION`,
`WRITEB`/`WRITEW`, `ADDR()`, `SIZEOF()`, `ALIGN()`, `AFTER(seg)`, `OVERLAYID(..seg)`,
assignments, `.` as the location counter. The PS1 scratchpad is a segment
`scratchpad (RWX) : ORIGIN = 0x1F800000, LENGTH = 0x400` holding `.spad_bss`
(`__declspec(scratchpad)` objects, never initialised at load). A segment whose
origin overlaps another's is how overlay groups share one load address.

**Other linker facts** (release notes and manual): it deadstrips unused code
and data only from CodeWarrior-compiled objects, never from assembler or
foreign objects; source files are linked before `.o`/`.a`, so a source
definition wins over a library's; data and text are aligned to 4 bytes for
ISA 1/2 and 8 for ISA 3+; whole objects get GNU-compatible alignment (structs
at least 4); R4's linker put an Adler-32 checksum of each loadable section in
its first 4 bytes (R4 2.21 notes; `adler32.c` is linked into `ln_mips.dll`),
a behaviour not seen in the PS2 records; DWARF 1 output carries Metrowerks'
`AT_overlay_id`, `AT_overlay_name` and `TAG_overlay_branch`; the link map is
the `.xMAP` "Performance Analyzer Map File" (`address size section symbol
(object)` rows under `### group` / `#-- overlay` headings); `overlaylist.txt`
lists the overlay files a link produced.

## 4. The startup code, and the tell it gives

`_psstart.c` (PlayStation Pro Support, R4 and R5) is the PS1 startup: `bgezal
zero` to find the text base (PC-relative mode), then **every register is set to
zero** (`move t0,zero; move v0,t0; ... move k1,t0`), then `lw sp,_stack_addr`,
align to 16, `lw gp,_gp`, clear `.sbss` and `.bss` word by word from
`_fsbss/_sbss_size` and `_fbss/_bss_size`, `InitHeap` only for C++ ("according
to Sony"), call the `__static_init` table, then `main`.

The PS2 descendant keeps the habit. Yakuza's loader `SLUS_213.48` begins at its
entry with `padduw $1..$31, $zero, $zero` (29 of them: `$k0`/`$k1` skipped),
`mthi/mtlo/mthi1/mtlo1 $zero`, `mtsah`, `mtc1 $zero` into all 32 FPRs,
`adda.s`, `sync.p`, `ctc1 $zero,$31`, and only then the sequence Sony's `crt0.s`
has: clear `.bss` with `sq`, `SetupThread` (syscall 0x3c with `gp`, stack,
stack size, args, root), `SetupHeap` (0x3d), two init calls, `ei`, load `argc`,
`main`, `j Exit`. Silent Hill 2's `SLUS_202.28` begins straight at the `.bss`
clear: Konami linked Sony's `crt0`, under CodeWarrior. Hence the rule the
fingerprint tool now applies:

- a run of `padduw rd,$zero,$zero` at the entry point is the **Metrowerks PS2
  startup**, a vendor tell as good as a `.comment` when the `.comment` is gone
  (`startup: metrowerks-psstart`);
- the Sony `crt0` shape at the entry is a startup tell only, and never argues
  against CodeWarrior (`startup: sce-crt0`).

## 5. The compiler's vocabulary (what a matcher may write)

From the two C Compilers References, the Targeting manuals, the release notes
and the strings of `cc_mips.dll`; PS1-era, so each is a lead about the PS2
compiler, not a proof.

- **Pragmas** the MIPS plugin knows: `optimization_level 0..4`,
  `global_optimizer`, `peephole`, `scheduling`, `optimize_for_size`,
  `inline`/`auto_inline`/`dont_inline`/`always_inline`/`inline_depth`,
  `pack(n)` (n in 0,1,2,4,8,16), `align`/`align_array_members`/`packed`,
  `readonly_strings`, `pool_strings`, `longlong`, `longlong_enums`,
  `enumsalwaysint`, `ANSI_strict`, `require_prototypes`, `mpwc_relax` (R5:
  `char*` and `unsigned char*` interchangeable in C), `MIPS16 on|off|automatic|reset`,
  `define_section`, `section`, `push`/`pop`, `once`, `mark`, `unused`,
  `warn_*`, `warning_errors`, `profile`. Optimization levels: 0 none; 1 and 2
  "simple" and "aggressive", safe for debugging; 3 and 4 the same kinds over a
  larger area of code (the manual's table 4.1). `__option(name)` tests a
  setting; `__option(fp_library)` says whether floats go through the software
  library.
- **Storage classes**: `__declspec(sbss | sdata | bss | data)` forces an object
  into or out of the small-data sections regardless of size (within one
  declaration `sbss`/`sdata` and `bss`/`data` are equivalent);
  `__declspec(scratchpad)` for PS1; `#pragma define_section sname ".istr"
  [".ustr"] [standard|near_absolute|far_absolute|near_code|far_code|near_data|far_data] [R|RW|RX|RWX]`
  with the predefined MIPS sections `text .text far_absolute RX`, `data .data
  .bss far_absolute RW`, `sdata .sdata .sbss near_data RW`, `const .rodata
  far_absolute R`, and for C++ `exception .exception`, `exceptlist .exceptix`,
  `vtables .vtables`. The small-data threshold is a **linker** setting ("Small
  Data", bytes) here, where ee-gcc's is the compiler's `-G`; the linker refuses a
  `.sdata`/`.sbss` over 64 KB and says to lower the threshold.
- **Inline assembly**: whole `asm void f(void) { ... }` functions in PsyQ-like
  syntax with `.set reorder`, and the `__I_<mnemonic>` intrinsics for every
  instruction the back end knows (`__I_mfc2`, `__I_cfc2`, `__I_cache`, the
  COP1 compares, the 64-bit `__I_d*` forms), plus `__evaluate`, `__alloca`,
  `__asm_start`/`__asm_end`; "intrinsics return the register they define".
- **Predefined macros**: `__MIPS_ISA2__`/`__MIPS_ISA3__`/`__MIPS_ISA4__`
  (replacing the older `__R3000__`/`__R4000__`), `__fpu__`, `__ptr64__`,
  `__IEEEdoubles__`, `__CASM__`, `__A5__`.
- **Codegen notes from the 1997-98 release notes** worth checking against a
  PS2 diff: vararg floats are passed in integer register pairs ("always
  even:odd" on ISA 1); the compiler never puts a floating-point operation in a
  branch delay slot (since 2.37); multiplication by a constant is strength-
  reduced; there is a call/return optimization, a global register allocator and
  scheduling "around jumps and calls"; `char` loads are explicit about
  sign/zero extension in assignments; `%` with a negative left side was fixed
  in 2.33; long-long comparisons against immediates had a dedicated bug. The
  Targeting manual's chapter "MIPS ABI: Calling Conventions, Variable
  Allocation, and Data Storage" is the ABI the back end implements.
- **Settings that change bytes without changing source**: "Make Strings
  Read-only" (off for PlayStation), pooled strings, "Insert NOP in Branch delay
  slots" (an assembler option, off when code fills its own slots), the
  profiler calls (`-profile`), MIPS16, big/little endian, and the SN plug-in
  adapter: a CodeWarrior *project* could be compiled by SN's `ccpsx`, so the
  IDE on a studio's machines proves nothing; the `.comment` names the compiler.

## 6. How it was read

The images are raw CD sectors (`mod 2352 == 0`, `CD001` at sector 16 offset
24); `bsdtar` and `hdiutil` refuse them until the user data is lifted out
(`[16:2064]` of each Mode 1 sector). R4 then extracts with `bsdtar`; R5 is an
XA bridge disc that `bsdtar` lists empty and `hdiutil attach` mounts. The text
files are CP1252 with CR line ends; the PDFs read with `pypdf`. Version
numbers come from the Win32 `VS_VERSION_INFO` resources (UTF-16 strings), not
from the release notes, which lag them.

## 7. What changed in the kit

- `unwrap_image.py` reads every `MWo` version: `overlay_id`, `version`, the
  bss size from the right slot per version, `static_init` on versions 1-2,
  words 6-7 raw with the construction note; its self-test carries the R4 demo
  header and Yakuza's values.
- `fingerprint_elf.py` reports `startup` (`metrowerks-psstart` / `sce-crt0`),
  names Metrowerks on the startup tell when no vendor string survives, prints
  the lineage row of a `MW MIPS C Compiler (x.y.z)` string, and accepts
  `mwo1`..`mwo3` inputs.
- `mcp/server.py` serves this file as `kit://docs/codewarrior`, reports the
  configured original's compiler family in `doctor`, and refuses `match_init`
  on a function whose prologue saves with `sq` when the original is a
  CodeWarrior (or ProDG) binary: no form compiled by ee-gcc can match it, and
  the loop would charge a budget for a verdict it cannot reach. Functions with
  `sd` prologues in such a binary (the Sony SDK and middleware) still open.

## 8. What the discs do not give

A PS2 compiler. The kit has no CodeWarrior lane, so for Silent Hill 2 and
Yakuza the matching tools cover the ee-gcc minority only (414 of 2,514
prologues in SH2's main executable, 1,844 of 5,523 in Yakuza's `MAIN.BIN`, the
Sony and CRI libraries). Running the PS1 plugin itself would need the Win32 IDE
under an emulator and would still compile for the R3000, which is useful for
PS1 matching and for learning the back end's habits, not for a PS2 byte match.
If a CodeWarrior for PS2 release ever surfaces, the record above says what to
look for first: the `cc_mips`/`ln_mips` plugin versions, the LCF generator's
version-3 writer, and the `_psstart` source.
