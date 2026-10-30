# GQL-24 — GraphQL control surface: caller map

Lane `L01-gql24`, base commit `7b6e050e`, branch `lane/L01-gql24`.
This file is an analysis artifact inside the declared set. It records the
callers the GQL-24 control surface must cover, harvested from source on disk.
It is NOT the control surface itself; see `L01-gql24-verification-report.md`
for why the surface cannot be built in scope.

## Done criteria this map serves

> CLI, MCP and GraphQL operations share identity, authorization and lifecycle
> semantics. Spawn/status/cancel/artifact operations are covered; variables and
> fragments use a standard GraphQL executor.

The map below fixes, per operation, the three existing caller surfaces that a
GraphQL surface must agree with. Coordinates are `path:line` at `7b6e050e`.

## Surface 1 — CLI (`lev`)

Command enum: `crates/leviath-cli/src/dispatch.rs:36` (`pub enum Commands`),
dispatched at `dispatch.rs:358`. Run args: `crates/leviath-cli/src/commands/run/`.

| Operation | CLI verb | Coordinate |
|---|---|---|
| spawn | `lev run` | `dispatch.rs` `Commands::Run(commands::run::RunArgs)` |
| status (list) | `lev ps` | `dispatch.rs` `Commands::Ps(commands::ps::PsArgs)` |
| status (one) | `lev ps <id>` | `crates/leviath-cli/src/commands/ps/` |
| cancel | `lev cancel` / alias `kill` | `dispatch.rs` `Commands::Cancel(commands::ctl::CancelArgs)` |
| pause/resume | `lev pause` / `lev resume` | `dispatch.rs` `Commands::Pause/Resume(commands::ctl::*)` |
| message | `lev msg` | `dispatch.rs` `Commands::Msg(commands::ctl::MsgArgs)` |
| artifact | `lev run` output / files | `commands/run/attach.rs`, `commands/run/session*` |

## Surface 2 — MCP (`lev mcp serve`)

Tool table: `crates/leviath-cli/src/commands/mcp/serve_tools/mod.rs:68`
(`pub(super) fn tool_table() -> Vec<ServerTool>`), dispatch at `:279`
(`call_tool`). Wire shapes: `crates/leviath-mcp/src/server.rs`.

| Operation | MCP tool name | Coordinate |
|---|---|---|
| spawn | `run` | `serve_tools/mod.rs:85` |
| spawn (sync) | `wait` | `serve_tools/mod.rs:110` |
| status | `status` | `serve_tools/mod.rs:123` |
| artifact/result | `result` | `serve_tools/mod.rs:130` |
| cancel | `cancel` | `serve_tools/mod.rs:144` |
| message | `message` | `serve_tools/mod.rs:151` |
| respond | `respond` | `serve_tools/mod.rs:165` |
| status (list) | `list_runs` / `ps` | `serve_tools/mod.rs:182`, `:223` |
| list agents | `list_agents` | `serve_tools/mod.rs:195` |

The MCP tool surface already carries the spawn/status/cancel/artifact quartet
the done criteria name. A GraphQL surface must reuse these names and semantics,
not invent parallel ones.

## Surface 3 — HTTP (`lev serve`)

Router: `crates/leviath-cli/src/commands/serve/mod.rs` (route table from `:118`).
Handlers: `crates/leviath-cli/src/commands/serve/agents.rs`.

| Operation | Route | Handler |
|---|---|---|
| spawn | `POST /api/agents` | `agents.rs:74` `spawn_agent` |
| status (list) | `GET /api/agents` · `GET /api/runs` | `agents.rs:188` `list_agents` |
| status (one) | `GET /api/agents/{id}` | `agents.rs:206` `get_agent` |
| cancel | `DELETE /api/agents/{id}` | `agents.rs:841` `kill_agent` |
| artifact/result | `GET /api/agents/{id}/result` | `agents.rs:766` `agent_result` |
| artifact/files | `GET /api/agents/{id}/files` | `agents.rs:455` `agent_file` |
| message | `POST /api/agents/{id}/message` | `agents.rs` (message handler) |
| pause/resume | `POST /api/agents/{id}/pause` · `/resume` | `agents.rs:859`, `:875` |

Identity/authorization for this surface: bearer token, checked in
`crates/leviath-cli/src/commands/serve/auth.rs`. Capability announcement:
`crates/leviath-cli/src/commands/serve/config_types.rs` `API_CAPABILITIES`
(`:167`) and `API_VERSION` (`:164`).

## Shared semantics a GraphQL surface must preserve

- **Identity**: run id `<agent>-<timestamp>-<hash>`; all three surfaces key on it.
- **Authorization**: HTTP uses bearer token + `--allow-admin` for mutating
  routes; MCP and CLI run on the host. A GraphQL surface inherits the HTTP
  model, so admin-gated operations must stay admin-gated.
- **Lifecycle**: the status vocabulary is one word across the API
  (`starting|running|waiting_input|paused|complete|complete_interactive|error|cancelled`),
  documented in `docs/content/api.md` and announced as `events.run_status`.

## Source identity (sha256, at `7b6e050e`)

```
2eb565245ba6955d3efdc30d28d194ff8bccc9022a0adac45289c7beed1dcffd  crates/leviath-cli/src/dispatch.rs
1995ef25b65dee004677b1da6d632c0f697de832d74a1904a414fb1956434ce5  crates/leviath-cli/src/commands/mcp/serve_tools/mod.rs
8403a21a7b1f11d82f80f8bb8d702d4a31985aac581aaf65d67c53261e412019  crates/leviath-cli/src/commands/serve/mod.rs
0d87ee3a8c6b411615520012ad38e72a1f9d4cf8a62892f02ffa6ebd74d1b83c  crates/leviath-cli/src/commands/serve/agents.rs
23d0391e03440b2277a4ce5446a95433c43018a08add8ef64efd4b2ba06055e5  crates/leviath-cli/src/commands/serve/auth.rs
186f71b96601e779447fa382adcb9b279157245899191c7a12892bbc86ff3ebb  crates/leviath-cli/src/commands/serve/config_types.rs
9b9826ba815ab49e794cb6667ae35286e2d43ab301fe4bce8256220bd0ae3a78  docs/content/api.md
```

## Harvested pre-existing GraphQL work (read-only, outside declared set)

- `crates/leviath-cli/agents/graphql-schema/` — schema-authoring blueprint
  (commit `4889045e`); README, gate, runner, task-board example.
- `docs/design/graphql-schema-workflow.md` — the blueprint's design doc.
- `crates/leviath-cli/agents/bake-off/` — HarnessQL bake-off (commit `8ea26e7d`).
- `docs/workflow/schema-workflow.graphql` — an existing *application* schema,
  separate from the authoring workflow (181 lines).
- `docs/workflow/evidence/gql-02-acceptance.md` — GQL-02 acceptance (other lane).

These are the intended GraphQL surface referenced by the node, but none of them
is a GraphQL *control surface over Leviath runs*; they are a schema-authoring
pipeline. Whether they are the intended GQL-24 deliverable is NOT DETERMINED
here (it needs the decomposition request answered).
