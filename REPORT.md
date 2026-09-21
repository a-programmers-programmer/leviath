# GQL-24-C — Reconcile REST/MCP/CLI with the old LifeOps GraphQL PoC

Status: COMPLETE (partial scope — see "What is not done").
Date: 2026-09-16. Run: R01M2NCFR2BCB88RW5M1C8H5M6R. Task: T01M2NCFG6TMW3VFDYM0N4MKVTS.

## Question
Can the REST surface (`lev serve`), the MCP surface, and the CLI be reconciled
with the old LifeOps GraphQL PoC, without copying its hand-written parser?

## Answer
Yes. The PoC schema can be served by a real GraphQL engine. I proved it with a
compiling Rust probe. The probe uses `async-graphql` 7.2.1. It copies zero
lines of the PoC tokenizer, parser, or executor.

## What I found

1. The repo has no GraphQL surface. `grep -rl graphql --include=*.rs crates/`
   finds only `crates/leviath-tools/src/validate/format.rs` and its test file.
   That is a file-format label, not a server. This matches the PoC's Step 1.

2. **The repo lockfile does NOT contain async-graphql.** `Cargo.lock` (147,774
   bytes) has zero `async-graphql` entries. The earlier stub claim that "the
   repo already pulls a real GraphQL implementation through its dependency
   tree" is WRONG. I corrected it.

3. The crates are available offline in the local cargo registry cache:
   `async-graphql-7.2.1`, `-derive`, `-parser`, `-value`, and `pest` 2.9.1
   (the parser backend). So adding the dependency needs no network. A real
   parser is available; nobody has to hand-write one.

4. The REST surface is a fixed route table. `api_router()` in
   `crates/leviath-cli/src/commands/serve/mod.rs` mounts 66 `.route(...)` calls
   (54 distinct paths): blueprints, runs, agents, interactions, mcp, tools,
   scripts, config, and `/ws`.

5. The MCP surface is a fixed tool table. `tool_table()` in
   `crates/leviath-cli/src/commands/mcp/serve_tools/mod.rs` lists 16 tools:
   run, wait, status, result, cancel, message, respond, list_runs, list_agents,
   install_tool, list_tools, ps, pause, resume, daemon_status, validate.
   `call_tool` has 15 match arms (`ps` reuses `list_runs`).

6. REST and MCP are two faces of one daemon. Both bridge to the same
   `ControlRequest` / run-record core. GraphQL is a third face over that same
   core, not a replacement for either.

## The probe (the proof)

Source: `probe/src/main.rs` (6,194 bytes). It defines the PoC's exact schema:
`Run{run_id,status,stage,tool_calls,iteration}`,
`Proposal{proposal_id,run_id,proposal,status,created_at}`,
`Query.runs(status,limit)`, `Query.recentRun`, `Query.context(run_id)`,
`Mutation.spawnRun(task,blueprint)`, `Mutation.submitProposal(run_id,proposal)`.
Field names stay snake_case so the PoC's own queries still parse.

Run command (offline, warm target dir reused to save disk):
```
CARGO_TARGET_DIR=/tmp/gqlprobe/target cargo run --offline --manifest-path probe/Cargo.toml
```

Results in `probe_out.txt`:
- `POC_QUERY_OK` — the PoC's verbatim query parses and resolves. 357 bytes of
  data: `runs[]` with all five fields, `recentRun`, `context`.
- `INTROSPECTION_OK=true` — `__schema` exposes `queryType=Query` and
  `mutationType=Mutation`. The schema is self-discoverable.
- `MUTATION_OK` — `spawnRun` and `submitProposal` round-trip with all fields.
- `ERROR_OK` — `{ nope }` returns `{"data":null,"errors":[{"message":"Unknown
  field \"nope\" on type \"Query\"."...}]}`. Proper GraphQL error semantics,
  not a crash.

## Reconciliation shape (recommended)

Add one crate: `leviath-gql`. It mounts a `POST /graphql` route beside the
existing REST routes in the same axum router, and it reuses the same
`ControlRequest` bridge the MCP tools use. Then:
- REST stays the stable control plane.
- MCP stays the model-facing tool surface.
- GraphQL becomes the self-describing surface: one endpoint, introspection
  gives the contract, no tool sprawl.

## What is not done

- The `leviath-gql` crate is not written. The probe proves feasibility only.
- The probe returns fixed sample data. It does not yet call the daemon.
- No auth on the GraphQL route. The PoC noted the same gap.
- No pagination beyond a `limit` cap. No variables/fragments/subscriptions in
  the probe (async-graphql supports them; the probe does not exercise them).

## Evidence
See `EVIDENCE.json`. Raw probe output: `probe_out.txt`. Probe source:
`probe/src/main.rs`.
