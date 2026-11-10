# Agents

Claude Code agent definitions for running the kit's matching loop with a team of
models: an orchestrator, four tiered matchers, a reviewer, two integrators, a stock
verifier, an evidence enricher and an IOP matcher. They come in two flavours with the
same roles and rules:

- **generic** (`agents/generic/`): any PS2 decomp the kit can serve (a tree in the
  kit's native config layout or one served through a target manifest).
- **Xenosaga** (`agents/xenosaga/`): the same bodies plus what the Xenosaga lanes
  learned, with episode notes for I, II and III. The names are the KOS-MOS pack's
  personas with a `kit-` prefix, so they never collide with the pack-rendered agents a
  Xenosaga checkout may already track in `.claude/agents`.

The lane these agents run is the kit's: one function per `match_kit.py` sandbox (or the
`ps2decomp` MCP tools that wrap it), judged by compiled bytes and the anti-cheat lint,
then reviewed by another model family and landed one patch at a time under the upstream
project's own whole-file gate. A match_kit `MATCH` is never acceptance; the upstream's
gate is.

## Roles

| role | generic | Xenosaga | model | effort | forms | classes |
|---|---|---|---|---|---:|---|
| orchestrator | `decomp-orchestrator` | `kit-shion-orchestrator` | claude-sonnet-5 | medium | | |
| matcher T1 | `matcher-t1` | `kit-momo-decompiler` | claude-haiku-4-5-20251001 | default | 4 | trivial, easy |
| matcher T2 | `matcher-t2` | `kit-chaos-decompiler` | claude-sonnet-5 | medium | 15 | medium, unknown |
| matcher T3 | `matcher-t3` | `kit-ziggy-decompiler` | claude-opus-5-5 | medium | 40 | hard, cop2 |
| matcher T4 | `matcher-t4` | `kit-kosmos-decompiler` | claude-fable-5-1 | high | 40 | huge |
| reviewer | `decomp-reviewer` | `kit-gaignun-reviewer` | claude-sonnet-5 | medium | | |
| integrator | `decomp-integrator` | `kit-hammer-integrator` | claude-haiku-4-5-20251001 | default | | |
| integrator (fallback) | `decomp-integrator-senior` | `kit-matthews-integrator` | claude-sonnet-5 | medium | | |
| verifier | `stock-verifier` | `kit-encephalon-verifier` | claude-haiku-4-5-20251001 | default | | |
| enricher | `evidence-enricher` | `kit-umn-enricher` | claude-haiku-4-5-20251001 | default | | |
| IOP matcher | `iop-matcher` | `kit-nephilim-decompiler` | claude-sonnet-5 | high | 30 | IOP modules |

Every matcher also stops at the kit's NO PROGRESS rule (4 charged forms without a lower
`diff_words` or a higher match count). Escalation goes up one tier with the previous
worker's next hypothesis, never down. The IOP matcher has no tier above it: the EE
matchers judge only match_kit sandboxes, so an IOP miss gets at most one same-lane retry
with a changed hypothesis and is otherwise recorded as a blocker.

Models, effort and tool restrictions are copied from the KOS-MOS pack's routing table
(`models_from` in `roles.json` records the date). The four matchers' form ceilings and
classes are that table's `stock_lane` tiers (`momo-stock` 4, `chaos-stock` 15,
`ziggy-stock` 40, `kosmos-stock` 40), the pack's match_kit lane, not its TU-queue rows of
the same personas (`momo-decompiler` 6, `chaos-decompiler` 30 and also trivial and easy).
The TU queue does not exist here, so the stock-lane values fold into these matcher tiers;
the IOP row is the pack's `nephilim-decompiler`.

Matchers carry `disallowedTools: Agent, Workflow` and no `tools` allowlist, so the MCP
tools of a registered `ps2decomp` server stay available to them.

**Review across families.** The reviewer's model family is never the author's. Every
matcher here is a Claude model, so their candidates need a reviewer of another family
(a Codex or other-provider session, a review pool, a person); the Claude reviewer agent
reviews only candidates another family authored. The orchestrator records the
reviewer's model with each verdict.

**IOP.** `match_kit.py` judges EE functions built by ee-gcc. The IOP matcher works one
function at a time against the upstream project's own linked comparison of the module,
with the same discipline (one hypothesis per build, a ceiling, a no-progress stop).

## Install

```sh
tools/install_agents.py detect --project ~/src/my-decomp          # what would be installed, and why
tools/install_agents.py install --project ~/src/my-decomp --dry-run
tools/install_agents.py install --project ~/src/my-decomp         # -> ~/src/my-decomp/.claude/agents
tools/install_agents.py install --user --flavour generic          # -> ~/.claude/agents
claude --agent decomp-orchestrator                                # or kit-shion-orchestrator
```

`install` writes `.kit-flavour.json` beside the agents (`flavour`, `game`, `reason`,
`kit_commit`, the agent names and the hash of each file it wrote); the Xenosaga agents
read `game` to pick their episode notes. The kit's own path goes to
`~/.claude/decomp-compat-kit.json` (`kit_root`), outside the project, and the agents
read it there unless `DECOMP_KIT` is set; nothing the installer writes into a project
holds a machine path. When the project is a git work tree in which the agents are not
ignored, the summary says so: commit them only if the project should carry them.

The project directory must exist. `--user` installs one set for every project, so it
needs an explicit `--flavour` and records `game` null (the agents then ask the `doctor`
which binary is configured).

Without `--force`, a same-named file that lacks the kit marker (a hand-written agent) or
a kit agent edited since the kit wrote it refuses the whole install before anything is
written; with `--force` it is overwritten after a copy to `NAME.md.kit-backup`. A
symlink in place of an agent or a state file is always refused. The installer removes
every kit-marked agent in the directory whose name a kit flavour renders but the
installed set does not contain (the other flavour's set, or an agent dropped from
`roles.json`), so a project never has both sets; files without the marker are never
removed.

## Detection

`--flavour auto` (the default) takes the first rule that fires and reports it as
`reason`:

1. `--flavour generic|xenosaga`;
2. `DECOMP_GAME` (`xenosaga`, `generic`, `xenosaga-1/2/3`, or any other game id, which
   means generic);
3. a file in the project (three levels deep) named after a serial: `SLUS_204.69` (I),
   `SLUS_208.92` and its disc 2 twin `SLUS_211.33` (II), `SLUS_213.89` (III);
4. a git remote of the project matching `xsg-i-decomp`, `xsg-ii-decomp`,
   `xsg-iii-decomp` or `xenosaga`;
5. the project's `config/originals.json` or splat yaml naming one of those serials;
6. the `DECOMP_TARGET` manifest naming a Xenosaga serial or title;
7. `DECOMP_BINARY` or the `DECOMP_GAME_DIR` listing naming a serial.

The project's own evidence comes before the environment because `DECOMP_*` variables
are often exported shell-wide for one game: with `DECOMP_GAME_DIR` pointing at Episode
I's files, a clone of xsg-ii-decomp still detects as `xenosaga-2` from its remote.
Otherwise generic: an empty directory gives generic only when no `DECOMP_*` game variable
is set, so when `DECOMP_GAME_DIR` or `DECOMP_BINARY` is exported shell-wide for one game,
run `detect` first or pass `--flavour generic`. An unreadable `DECOMP_TARGET` manifest is
named in `reason`. Once the flavour is Xenosaga, the later rules still run to find the
episode; `game` stays null when none names it, and the agents then ask the `doctor`
tool which binary is configured.

## Editing

The rendered files are output. Edit `roles.json` (names, models, effort, tools, ceilings,
classes, descriptions, per-role advice), a body in `templates/` (placeholders are
`{{key}}`; an unknown key fails the render) or the game text in `games/generic.md` and
`games/xenosaga.md` (`## setup` for every agent, `## <template or role id>` for one
role, `## <key> xenosaga-N` for an episode note), then:

```sh
tools/install_agents.py render            # rewrites agents/generic and agents/xenosaga
tools/install_agents.py render --check    # exit 1 and a list when a rendered file drifted
tools/install_agents.py self-test
```

The models and ceilings follow the KOS-MOS pack's routing table; when it changes,
update `roles.json` and its `models_from` date, then render.
