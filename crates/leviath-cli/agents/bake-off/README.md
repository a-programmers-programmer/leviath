# HarnessQL bakeoff — schema iteration lanes

Two Leviath blueprints for improving a GraphQL schema by competition rather than by
writing one from scratch. This is the CASE pattern applied to schema design: cheap
diverse proposers, then a merge stage that disposes deterministically per type.

| Lane | Stage | Model | Job |
| --- | --- | --- | --- |
| `bake-off` | `bake` | `deepseek/deepseek-v4.1-flash` | Iterate an existing schema against the Composition Guide. Emits a revised schema plus a RATIONALE block. |
| `bake-merge` | `decide` | `deepseek/deepseek-v4.1-flash` | Given a COMPACT DIFF, emit per-type merge decisions. Never produces a full schema, never reads the candidates. |

## Layout

```
bake-off/
  agent.leviath            the iteration lane
  scripts/                 the driver and the diff tooling
    run_bakeoff.py         single-round driver
    run_bakeoff2.py        driver, second revision
    run_bakeoff3.py        the round-3 driver (largest)
    graphql_diff.py        compact structural diff between two schemas
    harnessql.py           schema loading and comparison helpers
    test_merge.py          offline tests for the merge path
    trace_merge.py         debug tracing for a merge run
    trace_strip.py         debug tracing for comment stripping
  hooks/
    validate-schema.rhai   text-level SDL sanity checks
    validate_schema.py     the Python equivalent
  references/
    composition-guide.md   THE ARBITER. Every bake-off change cites a rule in it.
    group-prompts.md       the prompt group used to fan out lanes
    GATE-COMPARISON.md     comparison of the gate variants tested
    aggregator.md          aggregator notes
  study/                   the evidence: schema generations and round logs
bake-merge/
  agent.leviath
  hooks/validate-schema.rhai
```

## How a round runs

1. Pick seed schema(s) and a lane label. `bake-off` reads every seed. If there are
   two, they are two perspectives on the same abstraction and must be **reconciled
   into best-of-both**, not concatenated.
2. `bake-off` iterates and writes its schema to `<out>`, then calls `submit_output`.
   Its stage **requires** `submit_output`; a run that never submits produces nothing.
3. `graphql_diff.py` produces the compact diff between candidates.
4. `bake-merge` decides per divergent type: `reconcile` (the default — merged type is
   a superset of both), or `keep_a` / `keep_b` as an exception that needs a structural
   reason. It reads only the diff text.

## Evidence in `study/`

Eight schema generations — `A1 A2 B1 B2 C1 C2 L0L0` plus `bakeoff/v0_*`, `v1_*`, `v2_*`
lanes and their merged variants — alongside per-type `*_decisions.txt`, the seventeen
`log*-<lane>.txt` round logs and nine `bakeoff_v*.log` iteration logs. Kept as-is: the
generations and decision files are the proof the two lanes work. Do not tidy them.

## Two things a reader should know

**1. The hooks are present but not wired.** `hooks/validate-schema.rhai` exists in both
lanes and is not attached to any stage. An earlier revision of `bake-off` wired it as
`[stages.bake.hooks] on_completion = "hooks/validate-schema.rhai"`; the newer revision
that actually ran the v9 bakeoff does not, and instead tells the model not to write
comments at all. The files are kept so the choice is visible and reversible. Note the
hook's own comment: the runtime does not honour a retry from `on_completion`, so it
cancels with a reason, and the real parse authority is the Python `graphql-core` gate
in the runner — not this hook.

**2. Read paths were re-pointed when this was folded into the repo.** The lanes
originally read the guide and the schema generations from a scratch directory outside
the repository. Those paths remain in `[read_paths]` so an existing checkout keeps
working, and the in-repo locations were added alongside. **If you install these lanes
fresh, check the read paths before running.** The cleaner long-term fix is to embed
the Composition Guide as an Rhai tool the way `graphql-schema` embeds its own guide via
`graphql_contract_guide`, which removes the path dependency entirely. That has not been
done here.

## Provenance

Recovered from the working box on 2026-09-11. The blueprints here are the **installed**
versions (`~/.leviath/agents/bake-off`, `~/.leviath/agents/bake-merge`), which are newer
than the copies that sat in the scratch directory and are the ones that ran the final
bakeoff. Everything had been untracked in git, 76 files living as scratch.
