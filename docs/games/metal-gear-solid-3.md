# Metal Gear Solid 3: Snake Eater (SLUS_209.15, 2004, Kojima Productions)

The title whose boot executable is not the game at all, and the one that
separates ee-gcc 3.x from 2.96 in the kit's heuristics.

## The binaries

| file | what it is |
|---|---|
| `SLUS_209.15` | a **zlib decompressor stub**: one `PT_LOAD` at `0x800000` holding `[u32 length][zlib stream]` and the stub's own code after it |
| the payload | a 1,069,700-byte headerless memory image that opens with a Konami container header whose first word is a Unix build timestamp (2004-10-29 11:43:09 UTC), then zero words |

`unwrap_image.py` inflates the stub, checks the declared length against the
inflated one (0x105284, equal), describes the container header, and lists the
addresses the stub's own code forms outside its segment as candidates for the
payload's destination (listed, never asserted; pass `--base` when known). The
inflated image hashes identically to an independently produced reference.

## What built it: SCE ee-gcc 3.x (lead)

Fingerprint of the inflated image: `sd` saves in 1,330 of 1,361 prologues
(1,035 at 8 bytes, 295 at 16); **2,649 float constants loaded from `.rodata`
against 5 formed as immediates**, the 3.x shape (2.96 forms them as
immediates); 193 of 197 `div.s` padded with `nop;nop` but **0 of 61 `sqrt.s`**,
which is the shape no 2.9x build shows; SDK banners libcdvd/libgraph/libkernl
3.0.0.0 and libpad2 3.0.2.0, which with the 2004 build date fit the
`3.2-ee-040921` era.

An earlier pass had called this "2.9x-consistent" from the save stride alone;
the float-constant idiom is what corrected it. Still unproven: no function has
been compiled against a 3.x `cc1`.

## What the kit learned here

- A boot ELF can be a container, and the fingerprint tool must say so and
  stop rather than census the stub's code (`kind: zlib-stub-elf`).
- The float-constant idiom and the `sqrt.s` padding are the two tells that
  separate 3.x from 2.96 when the save stride is the same.
- Metrowerks and Sony are not the only axis: Konami's two studios chose
  differently (this one SCE gcc, Team Silent CodeWarrior), so the compiler is a
  per-studio fact.

## Running the kit on it

```sh
tools/unwrap_image.py SLUS_209.15 --describe
tools/unwrap_image.py SLUS_209.15 --out work/
tools/fingerprint_elf.py work/SLUS_209.15.image           # reads the sidecar
tools/transfer_symbols.py --donor sdk20.elf --donor-symbols sdk20.csv \
    --acceptor work/SLUS_209.15.image --acceptor-base 0x<load> -o mgs3_sdk.tsv
```

## Open

The load address of the payload (the stub's candidates are listed, not
proven), a 3.x compiler to probe and match against, and the SDK 3.0 libraries
as a donor, which the kit does not hold. The game's data formats (MTAF audio,
the Konami containers) are documented in the author's separate extraction
work, not here.
