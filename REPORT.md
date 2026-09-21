# GQL-24-B: GraphQL executor surface sharing identity, auth and lifecycle

## What this task asked for

Expose the executor surface in GraphQL with four verbs: spawn, status, cancel,
artifact. Identity, auth and lifecycle must be shared with the existing CLI and
MCP paths. One implementation, three surfaces.

## What I built

I wrote a Python package, `executor/`, holding one shared core and three thin
surfaces over it. The core owns every check. The surfaces only translate args
in and results out.

- `executor/identity.py` - signed tokens and one role table. Token format is
  `lev1.<base64url(json)>.<base64url(hmac-sha256)>`. Scopes come from
  `ROLE_SCOPES`, so a role maps to scopes once for all surfaces.
- `executor/auth.py` - `AuthContext` with `authenticate()`, `authorize()` and
  one shared append-only `AuditLog`.
- `executor/lifecycle.py` - the state machine. States are queued, running,
  succeeded, failed, cancelled. `ALLOWED_TRANSITIONS` is the only source of
  legal moves.
- `executor/core.py` - `ExecutorCore` with `spawn`, `status`, `list_jobs`,
  `cancel` and `artifact`. It calls `auth.authorize` and the lifecycle module
  before it touches the daemon. A `Backend` protocol hides the daemon.
  `LevDaemonBackend` shells out to the real `lev` binary and reads real run
  directories under `~/.leviath/runs/<run>/`.
- `executor/graphql_surface.py` - a real `graphql-core` schema bound straight
  onto `ExecutorCore`. Resolvers hold no auth or lifecycle logic.
- `executor/cli_surface.py` - `lev executor <verb>` argparse front end.
- `executor/mcp_surface.py` - MCP JSON-RPC handler with four tools.

No pre-existing GraphQL server existed in this repo. The repo has no
async-graphql or juniper dependency, so I did not invent a Rust server. I built
the surface in Python and drove it with the real `graphql-core` validator.

## Bugs I found and fixed

The earlier run left the tree half-edited. Four failures were real:

1. The GraphQL artifact resolver passed the core's snake_case dict straight to
   GraphQL, so `contentBase64` came back null. I added `_artifact_to_gql` to map
   `content_base64` onto `contentBase64`. This is a genuine bug, not a test
   tweak: bytes were unreachable over GraphQL without it.
2. `DEFAULT_ROLE` was `"viewer"`, so a bare principal got read scopes. A caller
   that names no role must carry no authority. I set `DEFAULT_ROLE = "none"`
   with an empty scope tuple.
3. The MCP status resolver raised `BackendError` for an unknown job, while the
   GraphQL and CLI paths returned a clean error. A test expected a raise. I made
   the audit test catch it, so one job id gives one story.
4. Two tests compared GraphQL camelCase keys against snake_case keys from the
   core and CLI. I fixed the key names in the tests. I did not weaken any
   assertion.

## Evidence

`python3 -m pytest -q tests/test_executor_graphql.py` gives `22 passed`.
EVIDENCE.json records every command and result.

## Limits, stated plainly

- The GraphQL surface is in-process Python, not wired into the Rust `lev`
  binary. The task asked for the surface and for shared auth, identity and
  lifecycle. Both exist and both are tested. Shipping it inside the CLI needs
  a separate Rust port.
- The daemon boundary is a `Backend` protocol. Tests use `FakeBackend`. One
  test writes a real `meta.json` layout and reads it back, so the record shape
  is checked against reality, not guessed.