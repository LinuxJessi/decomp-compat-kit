# How the kit came to be, and why it is shaped this way

## History

The kit started in September 2026 as the *Xenosaga I Decomp Compatibility Kit*:
two scripts written to check a second project's matching decompilation of
`SLUS_204.69` from the outside. One compiled its C with a stock, unpatched
ee-gcc 2.96 and compared every function against the retail bytes; the other
exported the game's unstripped symbol table in the format splat reads. The
first run settled the compiler question for that game (817 of 857 functions
identical, every difference a documented toolchain patch) and the two projects
have exchanged symbols, verification and tooling since.

Working alongside that decomp taught more than the verifier could hold. Its
toolchain investigations found, one byte patch at a time, how the archived
compiler and assembler differ from whatever built the retail game; running
language models against its gate showed what a matching loop has to refuse
before a byte match means anything; and the per-function matching tool that
grew out of that became part of the kit.

In the same months the workspace fingerprinted the executables of other
PlayStation 2 games on the shelf: Xenosaga III, Final Fantasy X, Metal Gear
Solid 3, Silent Hill 2 and Yakuza. The heuristics that had been "obviously
right" on Xenosaga broke on each of them in a different way: a compiler that
saves registers like SN ProDG but is Metrowerks CodeWarrior, a boot executable
that is only a decompressor stub, a game whose entire code is a CodeWarrior
overlay behind a tiny loader, a 2001 title built with the 1999 compiler. The
methods survived; the defaults did not.

So on 2026-10-30 the kit was renamed and regrouped around the whole pipeline a
new PS2 decomp walks before its first matched function: **identify** the
compiler, **name** what the SDK already named, **triage** the rest, **probe**
the toolchain you hold, **verify** from the outside, **match** one function at
a time, with the lessons written down in `docs/`. The same day it grew an MCP
server so an agent can drive that pipeline without being able to overrule it.

On 2026-10-31 two archives changed what the kit could know. The CodeWarrior for
PlayStation discs (the PS1 product) explained the Metrowerks side of the
corpus; a set of 2000-2002 toolchain archives then supplied Sony's own ee-gcc
2.9-ee-991111-01 with its source and patch, the SDK 1.0 and 2.0 libraries,
CodeWarrior for PS2 R3.0-3.04 with a command-line compiler, and ProDG 2.0.
Sony's compiler was probed and shown to reproduce Sony's shipped library
object byte for byte; the SDK libraries became a symbol donor; and
CodeWarrior's `mwccps2` now compiles on this machine. `docs/SDK-ARCHIVE.md`
and `docs/CODEWARRIOR.md` are the records.

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
functions are cheap. Half the tools exist for that.

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
