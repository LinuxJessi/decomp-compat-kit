# PS2-Decomp Compatibility Kit

[![Featured by Awesome REA](https://raw.githubusercontent.com/reacher-z/Awesome-REA/477658fa73f6860e86cd1fc526657c2efdc25194/assets/featured-by-awesome-rea-inline.svg)](https://awesomerea.org/resources/decomp-compat-kit/)

Tools and notes for the parts of a PlayStation 2 matching decompilation that
sit *around* the compiler: finding out what built a binary, naming what the
SDK already named, deciding which functions are cheap, checking a decomp's C
against the original bytes with an independently obtained toolchain, and
running the one-function matching loop in a way that cannot be talked into a
false match. Everything is Python 3.9+ with the standard library; `rabbitizer`
is optional, for named disassembly.

No game binaries, assets or decompiled code are distributed here. You need
your own copy of the game.

## Who this is for

- **Starting a PS2 decomp** and wanting to know, before the first function,
  which compiler and flags built the game and which functions already have
  names.
- **Running a PS2 decomp** and wanting an outside check: does the C reproduce
  the bytes under a stock compiler, and when it does not, is the difference a
  known toolchain quirk or a mistake?
- **Pointing a language model at a decomp** and wanting a loop that judges the
  model's C by compiled bytes, refuses the usual cheats, and stops when it
  should. That loop is also available as an MCP server.

## Quick start

```sh
git clone https://github.com/LinuxJessi/decomp-compat-kit && cd decomp-compat-kit
tools/fingerprint_elf.py /path/to/SLUS_xxx.xx          # which compiler built this?
tools/unwrap_image.py   /path/to/SLUS_xxx.xx --describe # not a plain ELF? what is inside
tools/triage.py --elf SLUS_xxx.xx --symbols names.csv --summary
```

The identify and name tools need nothing but Python. Verify, match and probe
compile with a real ee-gcc, which is a 32-bit Linux binary: on an arm64 Mac
that means colima, docker and the `binfmt` handler, set up as described in
[`docs/TOOLS.md`](docs/TOOLS.md#running-an-i386-ee-gcc-on-an-arm64-mac). The
2.96 compiler itself comes from [decompme/compilers](https://github.com/decompme/compilers).

## The pipeline

A new PS2 decomp walks these stages before its first matched function, and
the kit has a tool for each. The long reference is [`docs/TOOLS.md`](docs/TOOLS.md).

| stage | tool | what it answers |
|---|---|---|
| **identify** | `fingerprint_elf.py`, `unwrap_image.py` | Which compiler built this image? Is this boot ELF a container? What does an overlay record say? |
| **name** | `find_symbols.py`, `dump_symbols.py`, `transfer_symbols.py`, `sdk_donor.py`, `symbols_to_splat.py` | Which functions already have names, from an unstripped sibling or from Sony's SDK libraries? |
| **triage** | `triage.py` | How hard is each function, what does it call, was its TU built `-G0` or `-G8`? |
| **probe** | `probe_toolchain.py` | What does the `cc1` + `ee-as` pair I hold do on the behaviours known to differ from retail? |
| **verify** | `verify_decomp.py` | Does this decomp's C reproduce the bytes under a stock compiler, function by function, and what class is each miss? |
| **match** | `match_kit.py` | One function at a time: evidence card, named disassembly, a verdict per attempt, no-progress stop, anti-cheat lint |
| **compile** | `cw_run.py` | Run CodeWarrior for PS2's `mwccps2` on this machine, natively through Wine or in a container |
| **ledger** | `ledger.py` | Which functions are byte-identical with every relocation applied, re-proved on demand (a target's `matched.tsv`) |
| **port** | `port_check.py`, `harvest_relocs.py` | Does game A's decomp C reproduce game B's bytes, and where did B's linker put every symbol it names? |

Measured results for each tool are in `docs/TOOLS.md`; the shape of a good
result is the Xenosaga I case study there: 817 of 857 functions identical
under a stock compiler and every difference in a class the project's own
patch notes predict.

## The games

Each title the kit has been run on has its own page under
[`docs/games/`](docs/games/README.md): what the binary is, what built it, what
was measured, which tools apply, what is open.

| game | compiler | the kit's standing |
|---|---|---|
| [Xenosaga Episode I](docs/games/xenosaga-1.md) (2002) | SCE ee-gcc 2.96 | reference target, proven; symbols shipped |
| [Xenosaga Episode II](docs/games/xenosaga-2.md) (2004) | SCE ee-gcc 2.96 | proven: 141 functions Tier A through a target manifest, 136 of them Episode I's C |
| [Xenosaga Episode III](docs/games/xenosaga-3.md) (2006) | SCE ee-gcc 2.96 | proven on one overlay; names transferred from Episode I |
| [Final Fantasy X](docs/games/final-fantasy-x.md) (2001) | SCE ee-gcc 2.9-ee-991111 family | lead; Sony's own 2000 toolchain staged and probed |
| [Metal Gear Solid 3](docs/games/metal-gear-solid-3.md) (2004) | SCE ee-gcc 3.x | lead; zlib-stub boot ELF unwrapped byte-exact |
| [Silent Hill 2](docs/games/silent-hill-2.md) (2001) | Metrowerks CodeWarrior for PS2 | identified; 54 overlay records read; compiler runs, no judged lane yet |
| [Yakuza](docs/games/yakuza.md) (2006) | Metrowerks CodeWarrior for PS2 | identified; whole game is one overlay record behind a loader |

The measured table across all seven is [`docs/CORPUS.md`](docs/CORPUS.md). Two
rules it forced: vendor strings before the register census (CodeWarrior saves
like ProDG), and the compiler is a per-studio choice, not a per-region one.

## The MCP server

`mcp/server.py` is the kit as a Model Context Protocol server, `ps2decomp`,
for Claude Code, Claude Desktop, Codex, Cursor or a script. It exists because
a language model is good at the repetitive half of matching (reading
disassembly, proposing C) and bad at the rule that makes it matching: a
function is done when the bytes are identical, not when it looks right.

Through the server a model can read evidence (`identify_*`, `name_*`,
`triage_*`, a 2 KB evidence card, named disassembly), write C into a sandbox,
and ask for a verdict. It cannot compile with its own flags, see game bytes,
edit the budget, declare a match, or continue after a stop. Every tool shells
out to the scripts in `tools/`, so a verdict reached through the server is the
verdict the command line gives: compiled bytes compared with the original,
cheats reported as `CHEAT`, unverifiable functions refused before any work,
and a compiler-family check that declines to open a CodeWarrior function in an
ee-gcc lane. The whole-unit verify and the toolchain probe run as background
jobs. Why that helps, in plain language: [`docs/MCP.md`](docs/MCP.md). Install
and the run log: [`mcp/README.md`](mcp/README.md).

## Documentation

| | |
|---|---|
| [`docs/TOOLS.md`](docs/TOOLS.md) | every tool, its options, what it measured, and the arm64-Mac setup for the i386 compiler |
| [`docs/games/`](docs/games/README.md) | one page per game |
| [`docs/CORPUS.md`](docs/CORPUS.md) | the six-game fingerprint table and how to reproduce a row |
| [`docs/MCP.md`](docs/MCP.md) | what the MCP server does and why it helps |
| [`docs/MATCHING-PLAYBOOK.md`](docs/MATCHING-PLAYBOOK.md) | the loop discipline, the evidence card, forty-odd GCC 2.96 codegen idioms each tied to the function that proved it |
| [`docs/TOOLCHAIN-DIFFERENCES.md`](docs/TOOLCHAIN-DIFFERENCES.md) | the catalogue of compiler and assembler behaviours between the archived ee-gcc 2.96 and the retail builds, how each shows up in a diff, and the probe results, including Sony's own 2000 toolchain |
| [`docs/CODEWARRIOR.md`](docs/CODEWARRIOR.md) | the Metrowerks side: the overlay record, linker symbols and command files, the startup code, the compiler's vocabulary, read from the CodeWarrior for PlayStation discs and confirmed against CodeWarrior for PS2 |
| [`docs/SDK-ARCHIVE.md`](docs/SDK-ARCHIVE.md) | the 2000-2002 toolchain archive: Sony's Tool Chain 2.0 (probed; reproduces Sony's own library byte for byte), SDK 1.0/2.0 libraries as symbol donors, CodeWarrior for PS2 R3.0-3.04 running, ProDG 2.0 |
| [`docs/HISTORY.md`](docs/HISTORY.md) | how the kit came to be and why an outside check matters |
| `rootfs.lock.json` | measured SHA-256 of the stock and contract toolchains |
| `symbols/` | the Xenosaga I symbol table, as CSV and in splat's format |

## Acknowledgments

[OpokXeno/xsg-i-decomp](https://github.com/OpokXeno/xsg-i-decomp), the Xenosaga I
matching decomp whose toolchain investigations fill the catalogue and whose
`function_context.py` classifier `triage.py` reproduces;
[splat](https://github.com/ethteck/splat),
[spimdisasm](https://github.com/Decompollaborate/spimdisasm) and
[rabbitizer](https://github.com/Decompollaborate/rabbitizer) for the symbol-file
format and the disassembler; [decompme/compilers](https://github.com/decompme/compilers)
for archiving the historical toolchains that make any of this checkable;
[SSXModding/ps2-ee-toolchain](https://github.com/SSXModding/ps2-ee-toolchain) for
the public SCE EE GCC/binutils source (2.9-ee-991111);
[ran-j/PS2Recomp](https://github.com/ran-j/PS2Recomp) for the SDK symbol
database whose precision set the bar for `transfer_symbols.py`;
[Gcenx](https://github.com/Gcenx) for the Game Porting Toolkit Wine build that
runs CodeWarrior on Apple Silicon.

## License

MIT, see [LICENSE](LICENSE). Symbol addresses are facts about a binary you own;
the game itself is not included and remains the property of its rights holders.
