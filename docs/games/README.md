# The games

One page per title the kit has been run on: what the binary is, what built it,
what the kit measured, which tools apply, and what is still open. Everything
on these pages is a measurement made with the kit's tools on a disc the author
owns; no game bytes, assets or decompiled code are reproduced. The one-table
summary is `../CORPUS.md`.

| Game | Serial | Year | Compiler | Status in the kit |
|---|---|---|---|---|
| [Xenosaga Episode I](xenosaga-1.md) | SLUS_204.69 | 2002 | SCE ee-gcc 2.96 | **reference target**: proven by byte-identical compiles; symbols shipped; the verify and match lanes were built on it |
| [Xenosaga Episode III](xenosaga-3.md) | SLUS_213.89 | 2006 | SCE ee-gcc 2.96 | proven on one overlay; 428 names transferred from Xenosaga I |
| [Final Fantasy X](final-fantasy-x.md) | SLUS_203.12 | 2001 | SCE ee-gcc 2.9-ee-991111 family | fingerprint lead; Sony's own 2000 toolchain is now in hand and probed |
| [Metal Gear Solid 3](metal-gear-solid-3.md) | SLUS_209.15 | 2004 | SCE ee-gcc 3.x | fingerprint lead; boot ELF is a zlib stub, unwrapped byte-exact |
| [Silent Hill 2](silent-hill-2.md) | SLUS_202.28 | 2001 | Metrowerks CodeWarrior for PS2 | compiler identified; 54 overlay records read; CodeWarrior compiler now runs, no judged lane yet |
| [Yakuza](yakuza.md) | SLUS_213.48 | 2006 | Metrowerks CodeWarrior for PS2 | compiler identified; whole game is one overlay record behind a loader |

Three compiler families, then, and the kit's judged lanes cover one of them.
The other two are where the next work is: a CodeWarrior lane now that
`mwccps2` compiles (`../SDK-ARCHIVE.md` §4), and a 2.9-ee-991111-01 lane for
the 1999-2001 titles now that Sony's own toolchain is staged and probed
(`../TOOLCHAIN-DIFFERENCES.md` §F).

Legend for the status words used on each page: **proven** means a function or
object was compiled and found byte-identical to the retail bytes; **measured**
means a fingerprint, census or transfer the tools printed; **lead** means the
reading the evidence favours, not yet proven by a compile.
