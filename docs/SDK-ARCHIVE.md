# The 2000-2002 PS2 toolchain archive, read for the kit

Eleven archives of period PS2 development software were read on 2026-10-31,
alongside the CodeWarrior for PlayStation discs of `docs/CODEWARRIOR.md`. This
file is the inventory, what each one established, what was tested, and what
was not run. Every version number below comes from the binaries themselves
(`VS_VERSION_INFO` resources, `strings`, `.comment`), not from file names.

## 1. Inventory

| archive | contents | what it is |
|---|---|---|
| `DTL-S13010 PS2_Tool_Chain_Release_2.0` (bin/cue) | `sce/ee/gcc`: **ee-gcc 2.9-ee-991111-01**, ee-as/ee-ld 2.9-ee-991111, `cc1`, `cc1plus`, `cpp`, ee-dvp-as, gdb; `src/src.tar.gz` (37 MB, the whole Cygnus tree); `sce-patch/patch` (108 KB); `sce/iop/gcc`: iop-gcc 2.8.1 (`mipsel-scei-elfl`). Linux i386 ELF binaries, dynamically linked | **Sony's own EE toolchain of August 2000**, the compiler that built the SDK 2.0 libraries and the one CodeWarrior and ProDG projects pointed their `SCE_TC` at |
| `DTL-S12010 PS2_Runtime_Library_Release_2.0` (bin/cue) and `Runtime_Library_2.0_DTL-S12010_fix.7z` | `sce/ee/lib/*.a` (21 archives, 1,093 named functions), `crt0.s`, `app.cmd`, `sce/ee/include`, samples, `sce/iop/modules/*.irx`, `1st_read/*.txt` (change notes), docs. The `.7z` is the same tree already extracted | SDK 2.0 (Aug 2000): the libraries whose objects sit verbatim in every retail binary of the era |
| `DTL-S12000 NextGen_Playstation_Runtime_Library_Release_1.0` (bin/cue) | `sce/ee/lib/*.a` (11 archives, 657 named functions), `crt0.s`, `app.cmd`, 1st_read notes; no toolchain on the disc | SDK 1.0 (Sep 1999), the pre-release library |
| `CodeWarrior_for_PS2_3.01.zip` (InstallShield SFX) | `Command_Line_Tools/mwccps2.exe` (ccps2_mips **2.4.5.0051**, Jan 2002), `mwldps2.exe` (lnps2_mips 2.4.1.0038), `asm_r5900_elf.exe`; IDE 4.2.6.844 with the plugins; `PS2_Support/` (prefix headers, `PRAGMA_PS2.h`, LCFs, `Runtime/Sources`, MSL); `PS2_IOP_Support`; stationery with `crt0.s`; release notes | CodeWarrior for PlayStation 2 Release 3.01 |
| `CodeWarrior_for_PS2_3.04_MPTP1.zip` (InstallShield SFX) | the same layout with ccps2_mips **3.0.0 Build 22** (Sep 2002), lnps2_mips 3.0.0 Build 18, the 3.0 front end; `Examples/` | Release 3.04 |
| `Codewarrior_3.rar` (bin/cue) | the Release 3.0 CD: `bin/plugins` with ccps2_mips 2.4.5.0048 (Nov 2001), `PS2_Tools/Command_Line_Tools/mwccps2.exe`, `PS2 Support`, `Examples/mw/overlay` and `overlayTutorial` (the PS2 overlay LCFs), `Stationery`, manuals | Release 3.0 |
| `SN Systems ProDG_for_PS2_2.0.zip` (InstallShield SFX) | `PS2CC/ps2cc.exe` (the SN driver), `ps2ld.exe`; `EETools/usr/local/sce/ee/gcc`: Win32 `ee-gcc.exe` 2.9-ee-991111b and `ee-gcc295.exe` 2.95.2-EE with both `cc1.exe` sets, `README_SN2952.txt`, `Dylan-vu0-patch/`; `SN_Assemblers`: `ps2eeas.exe`, `Ps2DvpAs.exe`, `Ps2IopAs.exe` (1.8.19); `SN_Linker/ps2link.exe` with `ps2.lk`; `libsn.a`; IOP tools (iop-elf-gcc 2.8.1); debugger, Target Manager 2.2x | ProDG for PlayStation 2 version 2.0 (late 2000 / early 2001) |
| `ProDGTMforPS2v22815.zip` | `PS2TM.exe` Target Manager 2.28.15 with notes back to 2.26j (Sep 2000) | the Target Manager alone |
| `CD_DVD-rom Generator ver2.0.7z` | InstallShield package of SCEE's CD/DVD-ROM Generator 2.00 (July 2004) | the mastering tool; no toolchain content |
| `Super_Bender_Boot_Disc-2007-05-08.7z` | a 40 MB disc image: `NFSBOOT.ELF`, `SBENDER/*.IRX`, `IOPRP280.IMG` | an EA internal NFS boot loader (2005); not a toolchain |

The two RAR archives that `bsdtar` could only partly read (`DTL-S12010`,
`DTL-S12000`, `Codewarrior_3`: RAR "unsupported method" to 7-Zip too) extract
cleanly with `unar`; the disc images are raw 2352-byte sectors like the
CodeWarrior for PlayStation ones.

## 2. Sony's 2.9-ee-991111-01 toolchain

Identities (`sce/ee/gcc` of the Tool Chain 2.0 disc):

| tool | version string | sha256 |
|---|---|---|
| `lib/gcc-lib/ee/2.9-ee-991111-01/cc1` | `2.9-ee-991111-01` | `67ccdce560770320a438d3a90d3bf1c3f5af7df27820dfb4b616b24840fa6c55` |
| `lib/gcc-lib/ee/2.9-ee-991111-01/cpp` | `2.9-ee-991111-01` | `e71400405b2aa36f2dfb345d323e3bf9afab29091d567f5df5c1173dac98f822` |
| `bin/ee-as` | GNU assembler `2.9-ee-991111` | `cb909465e826106e8ad11f11bb201476a0b64a5012829cd2a440ca3eaf732a03` |
| `bin/ee-ld` | GNU ld `2.9-ee-991111` | `ebfe0c5ecfca4c555e48673939b4618b867ed091191d8958870c96cb08720b91` |
| `bin/ee-gcc` | `2.9-ee-991111-01` | `6a62eab1ed09fb36e14d2b11e3aa60e9fb659fc11789d983d6b37504a85a673e` |

`src/src.tar.gz` is the Cygnus tree the ps2-ee-toolchain repository preserves
(`src/CYGNUS`, bfd, binutils, gas, gcc, gdb, newlib...), and `sce-patch/patch`
is what turns 2.9-ee-991111 into **-01** (SCE's June 2000 revision). It
touches eleven files. The ones that change code:

- **`gas/config/tc-mips.c`: short-loop padding in the assembler.** A new
  `check_branch_or_jump_insn` and, in `append_insn`, "the Emotion Engine #2.X
  mistakes branch target address under certain condition... all `do ... while()`
  loops [must consist] of more than equal to five instructions (excludes a
  branch instruction and an instruction in BDS)". For a backward PC-relative
  branch to a label in the current frag chain the assembler counts the loop
  body and emits `5 - len` nops before the branch (`SAFE_LOOP_BODY_LEN 5`),
  moving pending labels past them, and refuses to swap the delay slot when the
  body is exactly five (`dont_swap`). This is the assembler-side ancestor of
  the padding the kit catalogues as A2 (`mips_r5900_lengthen_loops` in the
  2.96 `cc1`, stock gate 4, retail objects 6); the two are different
  mechanisms with different counts, so a retail object's padding says which
  tool padded it.
- **`gcc/config/mips/mips.md`: `nop;nop` before `div.s`, `sqrt.s` and
  `rsqrt.s`.** The three patterns become `%(nop\;nop\;div.s %0,%1,%2%)` with
  length 3. Measured across the corpus, this is the cleanest SCE-gcc tell the
  kit has:

  | binary | div.s | preceded by nop;nop | sqrt.s | padded |
  |---|---|---|---|---|
  | SDK 1.0 libraries (1999) | 3 | 0 | 0 | |
  | SDK 2.0 libraries (Aug 2000) | 28 | 28 | 0 | |
  | Final Fantasy X `SLUS_203.12` (2001) | 567 | 565 | 39 | 39 |
  | Xenosaga I `SLUS_204.69` (2.96, proven) | 487 | 487 | 100 | 100 |
  | Xenosaga III `SLUS_213.89` | 497 | 497 | 62 | 62 |
  | MGS3 (ee-gcc 3.x) | 197 | 193 | 61 | **0** |
  | Silent Hill 2 (CodeWarrior) | 753 | 70 | 176 | 4 |
  | Yakuza `MAIN.BIN` (CodeWarrior) | 881 | 163 | 14 | 4 |

  Every SCE gcc from 991111-01 on pads `div.s`; 3.x dropped the `sqrt.s`
  padding; CodeWarrior never pads, and the padded minority in a CodeWarrior
  title is the SDK. `fingerprint_elf.py` now counts both.
- **`gcc/haifa-sched.c`**: `insn_orig_block[]` initialised to
  `n_basic_blocks + 1` (an uninitialised-read fix in the scheduler; the kind of
  host-dependent behaviour the kit's A6 describes for 2.96).
- **`Makefile.in`**: `CFLAGS_FOR_TARGET`/`LDFLAGS_FOR_TARGET` get `-G0`
  (libgcc and newlib built without small data).
- `gcc/cpplib.c`: multibyte (Shift-JIS) awareness in comments and strings;
  `gcc/version.c`: the `-01` suffix; the rest is gdb's DECI2 remote.

The `1st_read/gccchg_e.txt` of SDK 2.0 lists the compiler's known bugs at the
time (`-fshort-double` varargs, a strength-reduction miscompile fixed by
`-fno-strength-reduce`, `__attribute__((aligned))` on typedefs leaking); SDK
1.0's lists the 1999 ones (an `insn 340 ... mult:SI` internal error at `-O2`,
`-Os` as the workaround).

**Probed** (`tools/probe_toolchain.py --rootfs <i386 rootfs> --toolchains
~/sce-tc20 --cc1 /toolchains/ee/gcc/lib/gcc-lib/ee/2.9-ee-991111-01/cc1 --as
/toolchains/ee/gcc/bin/ee-as --as-flags "-EL -m5900 -mabi=eabi -G8"`, report in
`docs/probes/ee-gcc-2.9-ee-991111-01.json`): literals round to nearest (every
one exact), loops padded to five by the assembler, the FP-branch hazard nop
present on the C path, `move` as `daddu`, `break` in the lower field, `la` as
two instructions, `vsqrt` bits `00`. The binaries abort inside `exit()` on a
modern glibc after writing complete output (their static crtend's
`__deregister_frame_info` fails against the libc stub); the probe treats rc 134
with a non-empty output as success and says so. The table and what it means
are in `docs/TOOLCHAIN-DIFFERENCES.md` §F, together with the proof that this
pair built the SDK 2.0 libraries: `libvu0.c` from the disc's `src/lib`,
compiled with this `cc1` and `as` at the Makefile's flags, reproduces the
shipped `libvu0.o` byte for byte.

## 3. The SDK libraries as a symbol donor

The `.a` members are unstripped relocatable ELF objects with `gcc2_compiled.`
markers (SDK 2.0 built by 991111-01: `sd` saves at a 16-byte stride, padded
`div.s`). `tools/sdk_donor.py` lays their executable sections into one donor
ELF with a symbols.csv, and `transfer_symbols.py` does the rest. Measured with
`--mask strict`, high confidence, one hit per name:

| donor | FFX `SLUS_203.12` | Silent Hill 2 `SLUS_202.28` | Xenosaga I `SLUS_204.69` |
|---|---|---|---|
| SDK 2.0 (1,093 functions) | **58** (+34 medium) | **85** (+95 medium) | 49 |
| SDK 1.0 (657) | 12 | 22 | 18 |

Xenosaga I's own symbol table agrees with its 49, which is the control. The
names are the DMA, GS, VU0, SIF RPC, CD and MPEG primitives (`sceDmaSend`,
`sceGsPutDrawEnv`, `_sceSifSendCmd`, `cd_read_intr`, the `_ri0_*`/`_rix_*`
MPEG kernels in SH2); the medium rows are the same objects with a constant
or a table bound changed between SDK 2.0 and the SDK the game shipped with
(2.2.4 for SH2, 2.3.4 for FFX). Later SDK discs would raise the counts; the
method is independent of the game's compiler.

## 4. CodeWarrior for PlayStation 2, Release 3.0 to 3.04

What the three releases settle beyond `docs/CODEWARRIOR.md` (written from the
PS1 product):

- **The compiler is a command-line program here**: `mwccps2.exe` and
  `mwldps2.exe` (Win32), with `--gcc-compat` for GCC-style options, the
  `MWCPS2MIPSIncludes` / `MWPS2MIPSLibraries` environment, `-c`, `-S`,
  `-disassemble`. A CodeWarrior lane for the kit is therefore possible (Wine
  in an amd64 container, as decomp.me runs the GameCube `mwcceppc`), and was
  not attempted in this session for the same policy reason as the Sony `cc1`.
- **Running them.** `tools/cw_run.py` hosts the tools two ways: a native Wine
  (`$CW_WINE`, `wine` on PATH, or the Wine Crossover bundle from the
  `gcenx/wine` Homebrew tap, the only Apple Silicon Wine not disabled by
  Gatekeeper in Homebrew as of 2026-10; 32-bit tools run through Rosetta in
  seconds), or the docker image `cw-wine:amd64-booted`. The image is a
  `debian:bookworm-slim` run with `--platform linux/amd64`, `dpkg
  --add-architecture i386; apt-get install wine wine32:i386`, then `docker
  commit` (a `docker build --platform` on this colima produced an arm64 image
  that could not load `kernel32.dll`), booted once and committed again. On an
  arm64 host the image runs Wine as qemu-i386 inside an emulated amd64 box:
  every prefix boot took about ten minutes, so `cw_run.py` keeps one
  `wineserver -p` per invocation and the image carries a booted prefix.
  The tools expect `license.dat` beside them; the R3.04 installer's file
  licenses the PS2 plugins only until 18-nov-2002 (FLEXlm, `lmgr326b.dll`).
- **The `.comment` string `MW MIPS C Compiler (2.4.1.01)` is constant** across
  ccps2_mips 2.4.5.0048 (R3.0, Nov 2001), 2.4.5.0051 (R3.01, Jan 2002) and
  3.0.0 Build 22 (R3.04, Sep 2002): it dates a title to CodeWarrior for PS2,
  not to a build. The front-end version is in `__MWERKS__` (0x2405, 0x3000).
- **The `MWo3` record is Metrowerks' own and complete.** `PS2_Support/Runtime/
  Sources/CPP_Support/mwUtils_PS2.c` declares `mwOverlayHeader { identifier[3];
  version; id; address; sz_text; sz_data; sz_bss; _static_init;
  _static_init_end; name[32] }`, and the R3.0 example LCF writes exactly that
  (`WRITEB 0x33 # '3', ver.3`, then `OVERLAYID`, `ADDR`, text size, data size,
  bss size, `_static_init`, `_static_init_end`, the name). `mwOverlayInit`
  flushes the i-cache, clears `sz_bss` bytes after the file image and runs the
  initializers between the two pointers; `mwLoadOverlay` wraps `mwBload`
  (`sceOpen`/`sceLseek`/`sceRead`) and `mwOverlayInit`. The PS2 address table
  is `_overlay_group_addresses` (index 0 = `.main`), reached through
  `mwGetGroupAddress(index)`. Yakuza's 42-word table and every SH2 overlay's
  empty one are what this predicts.
- **The PS2 LCF dialect** adds `AFTER(seg, ...)`, `KEEP_SECTION`,
  `ALIGNALL(n)`, `LITERAL`, `WRITES("...")` with `DATE`/`TIME`, the `U`
  (not loadable) memory flag, `.comment` sections via a `.comment_section`
  block, and the `GROUP(ROOT)` / `GROUP(name.bin)` selectors; the default
  layout is `main (RWX) : ORIGIN = 0x100000`, `crt0 .text` first, two
  `WRITEW 0x0` "text section patch for EE pipeline" words after `.text`,
  `_gp = ALIGN(128) + 0x7FF0`, `.sdata` after the literals, `_fbss` before
  `.sbss`. The stationery links **Sony's `crt0.s`** unchanged, so a Metrowerks
  title of this era starts exactly like an SCE-gcc one (Silent Hill 2 does);
  Yakuza's register-zeroing entry is from a later runtime or Sega's own.
- **Codegen notes that bear on matching** (release notes): R3.0 "save and
  restore return address with `sd`/`ld` instead of the 128-bit `sq`/`lq`
  sequence" and "replaced `qmove` 128-bit with 64-bit equivalents", so a
  pre-R3.0 CodeWarrior title (Silent Hill 2, 2001) keeps `ra` with `sq`/`lq`
  where an R3.0+ one uses `sd`/`ld`; `#pragma fastmath` turns `sqrtf()` into
  `sqrt.s` (R3.01); `#pragma vu0_register_number n`; the COBRA peephole; the
  2.4.5 and 3.0 front ends; `#pragma divbyzerocheck`; prefix defines
  `__R5900__`, `__ee__`, `__mips_single_float`; `PRAGMA_PS2.h` forces
  `ANSI_strict off` and defines an `.indata` section.

## 5. ProDG for PS2 2.0

- `ps2cc` is a driver over GCC: the package carries SN's Win32 builds of
  `2.9-ee-991111b` (`ee-gcc.exe`) and of **`2.95.2-EE`** (`ee-gcc295.exe`,
  "based on the gcc 2.95.2 compiler source", with Dylan Cuthbert's VU0 inline
  patches and `-fdefault-single-float` / `-Wpromote-doubles`), selected by
  `compiler_path` in `sn.ini`; defaults `-m5900 -mabi=eabi -mips3`.
- The assembler and linker are SN's own: `ps2eeas` 1.8.19.316 (`ee/bin`,
  replacing `ee-as`), `Ps2DvpAs`, `ps2link` with the `ps2.lk` script
  (`org 0x00100000`, `text group`, `sectalign`, `inclib libc.a libkernl.a
  libgcc.a libm.a`), `ps2ld` 1.0.0.292 and `PS2DllLk` for SN's relocatable
  DLL scheme (`libsn.a`: `add_relmod`, `define_symbol*`, `check_dll`).
- So an SN-built game is GCC code (2.9-ee or 2.95.2) assembled and linked by
  SN tools: its `.comment`, if kept, is GNU's, and the kit's "sq/16 saves =
  ProDG" census rule (from the compiler-id rig) is about SN's `cc1` build,
  not a different compiler family. Untested here: no ProDG title is in the
  corpus.

## 6. Where things are

The archives stay in `~/Downloads`; the extracted trees were session
scratch. Sony's toolchain was staged at `~/sce-tc20/ee/gcc` (binaries, libs,
patch; no `src`) for the probe. The SDK donors (`sdk20.elf`/`.csv`,
`sdk10.elf`/`.csv`) are reproduced in seconds with `tools/sdk_donor.py` from
`sce/ee/lib` of the respective disc.
