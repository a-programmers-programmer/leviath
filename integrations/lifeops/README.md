# LifeOps MCP integration

An additive, Google-authenticated MCP service for reading the LifeOps Desk and
supervising Leviath workers. It is a separate Python service; it does not change
Leviath's Rust runtime, Desk's Flask app, the running queue, or Google login on
the existing website. It does not install or deploy itself.

The companion `.codex-plugin/plugin.json` and `.mcp.json` package the connection.
`LIFEOPS_MCP_URL` is the **client-side** setting for the deployed adapter's
`https://<adapter-host>/mcp` URL. A host that does not expand environment values
in plugin JSON should receive that concrete URL before installation. No endpoint
at that address is created by these files.

## What connects to what

The MCP client completes the adapter's Google OAuth flow. The adapter checks
the Google client audience, expiry, verified email, stable subject and explicit
email allowlist on every authenticated HTTP request. FastMCP supplies OAuth
discovery, client registration, PKCE, consent and token exchange. The adapter
then uses its own server-side credentials for two existing APIs:

| Destination | Credential | Purpose |
| --- | --- | --- |
| Desk's configured origin | `X-Xelor-Sync-Token` from `XELOR_SYNC_TOKEN` | Existing machine API only |
| Leviath's configured origin | `Authorization: Bearer` from `LEVIATH_API_TOKEN` | Existing `lev serve` API |

Google tokens never go to Desk or Leviath. Service tokens never go to the MCP
client. Redirects are rejected, including redirects to Google sign-in; they
cannot carry a service token to another origin. The website's existing human
routes stay protected. Missing credentials fail startup rather than creating an
anonymous MCP server.

This is a shared operator connection: each allowlisted Google account can read
the configured Desk and Leviath instance, and can invoke all enabled tools. It
is not tenant isolation. Allowlist only operators intended to have that access.
Workdir/blueprint allowlists limit dispatch choices; they do not sandbox the
worker's shell or revoke the permissions of its operating-system account.

## Tool contract

| Tool | Upstream route | Behavior |
| --- | --- | --- |
| `lifeops_desk_list` | `GET /api/desk` | Read current view; optionally include hidden slips or a goal |
| `lifeops_desk_thread` | `GET /api/desk/{id}/thread` | Read existing conversation/evidence |
| `lifeops_queue_stats` | `GET /queue/stats` | Read counts; upstream also runs its normal expiry sweep |
| `lifeops_request_status` | Local receipt database | Read only this authenticated principal's request receipt |
| `leviath_ps` | `GET /api/runs` | Preserve pagination and `next_cursor` |
| `leviath_status` | `GET /api/agents/{id}` | Preserve authoritative run snapshot |
| `leviath_result` | `GET /api/agents/{id}/result` | Preserve status, error and final-output envelope |
| `lifeops_desk_file` | `POST /api/desk/file` | File a task; normal Desk routing applies |
| `leviath_run` | `POST /api/agents` | Dispatch `qwen-worker`/`fixer` or another operator-allowlisted blueprint |
| `leviath_message` | `POST /api/agents/{id}/message` | Steer a running worker |
| `leviath_control` | Pause/resume POST; cancel `DELETE /api/agents/{id}` | Control execution; preserve the run record |

The final four tools are **absent** unless `LIFEOPS_ENABLE_WRITES=1`. Inspection
mode prevents explicit mutation tools; `queue_stats` still has the upstream
maintenance behavior named above and is deliberately not annotated read-only.
If zero upstream maintenance is required, do not call that tool.

The adapter deliberately has no generic HTTP request, shell, config-edit,
queue-claim, Beads-close, Desk-done or Desk-report tool. In the inspected Desk
source, posting a report can advance a queue row; exposing that as an innocent
artifact write would break LifeOps's acceptance boundary. Run completion remains
evidence, not an assertion of task completion.

## Durable dispatch receipts

Mutation calls require an explicit `request_id`. A SQLite transaction reserves
`(authenticated Google subject, request_id)` before contacting an upstream.
Successful responses are saved and returned on repeat calls, including after
restart. Reusing an ID with a different operation or payload is refused.

If a request is interrupted, times out, is rejected or returns malformed data,
its receipt remains `pending`. A replay returns `OUTCOME_UNKNOWN` and performs
no second write. This conservative behavior includes explicit upstream errors;
the adapter does not guess that an error was side-effect-free. A missing
`run_id` is also uncertain, not a successful dispatch.

To reconcile an uncertain spawn, inspect Leviath's durable run records using
`leviath_ps` (follow **all** pages) and `leviath_status`. Spawn metadata contains
`lifeops_request_id`, `lifeops_principal` and `lifeops_project`; the principal is
a hash of the Google subject, not a bearer token. Desk filings retain an
`origin.ref` equal to the request ID. Do not resubmit with a new ID until the
operator has established the old outcome. There is no automatic retry/reset
endpoint, and the adapter makes no exactly-once claim for arbitrary worker
effects. Receipts are an HTTP-dispatch journal, not a second task queue.

Keep `/state` on persistent storage. Losing it loses replay protection and
OAuth registrations. OAuth state is encrypted with a separately supplied
Fernet key; request receipts contain returned application data and are protected
by filesystem permissions (not encrypted by this adapter). Back up the state
volume securely. Run one replica with a local persistent volume in v1; do not
put SQLite on an unverified network filesystem. Multi-replica operation needs a
shared transactional receipt store and shared encrypted OAuth storage.

## Prepare and verify locally

From this directory, using Python 3.12+:

```bash
python3 -m venv .venv
.venv/bin/pip install -c requirements.lock '.[test]'
.venv/bin/python -m pytest -q
```

`requirements.lock` pins the tested dependency resolution, including optional
test dependencies. Production installs apply it as constraints and do not
install the optional test extras.

Tests use the real FastMCP protocol/client and HTTP authentication middleware,
with fake upstream responses and a simulated Google token swap. They exercise
OAuth discovery, PKCE advertisement, client-registration persistence, Google
identity checks, separate upstream credentials, exact API paths, malformed/error
responses, request-size limits, concurrent reservations, restart replay and
ambiguous dispatch. They do **not** prove live Google consent, live worker
availability, Docker deployment, or production connectivity. No Rust files are
changed; this package's gate is its Python test suite, not a substituted claim
that `cargo check` ran.

## Deployment preparation — execute only after approval

1. Choose a separate HTTPS origin for this adapter. Do not replace the Desk
   website or send `/mcp` through its existing Google-session redirect guard.
   The adapter terminates MCP OAuth itself; a proxy may route its whole origin
   through TLS without stripping Authorization or discovery routes.
2. Run this package where it can reach both configured APIs. A process on the
   Leviath host can use `http://127.0.0.1:3000`; a container's loopback is its own
   container, not another service. Use a verified HTTPS Leviath endpoint across
   hosts. The package does not start `lev serve`, install worker blueprints, or
   change the existing gateway process.
3. Configure a Google **Web application** OAuth client in the existing project
   (or an explicitly approved dedicated client). Add the exact adapter callback
   `https://<adapter-host>/auth/callback`. Keep the existing Desk callback. The
   adapter requests identity/email scopes, not Gmail or Drive access.
4. Supply the variables in `.env.example` through the deployment secret manager.
   Use the existing Desk machine token and a configured Leviath API token;
   neither should be pasted into chat or committed. Set explicit operator
   emails and project-to-workdir mappings. Those paths are on the **Leviath
   host**, not on this adapter. Map to worktrees reserved for this work when
   other agents are active; the adapter does not create remote worktrees.
5. Supply a stable random JWT signing key of at least 32 characters and a
   separate Fernet key, and mount persistent storage at `LIFEOPS_STATE_DIR`.
   Keep writes disabled until connection checks pass. `LIFEOPS_UNATTENDED=1`
   is an operator choice passed as `yolo`; callers cannot choose it per request
   or override model/tool policy. Existing Leviath host policy may still refuse.
6. Start `lifeops-mcp` with the supplied environment, or build this directory's
   Dockerfile. The Dockerfile listens on port 8000 and runs as UID 10001; make
   the mounted state directory writable by that UID. A persistent volume is
   required; the image filesystem is not a substitute.
7. Verify `/healthz`, then that unauthenticated `/mcp` returns **401 with OAuth
   discovery**, not a redirect. Connect the MCP client to the adapter's `/mcp`,
   complete Google sign-in through its secure UI, and test Desk/run reads.
   Unlisted accounts must be rejected. Only then enable mutation tools and run
   an explicitly authorized bounded worker task in a reserved worktree.

This package does not register an installed plugin or modify a marketplace.
The `.mcp.json` companion is a client connection template, not a live connection.

## Reviewed upstream contract

Leviath baseline: `zephyyrrr/leviath` commit
`712a9e0042c7111054a0323685369b236f8ce1ea`, specifically
`crates/leviath-cli/src/commands/serve/{agents,types,runs}.rs` and `docs/content/api.md`.
The adapter uses the HTTP spawn shape (not the distinct native MCP run schema)
and preserves the HTTP response envelopes.

Desk machine routes were inspected in the operator's Desk repository at commit
`8ccc314102c036139d1716cc1c47892fcc2722ae`: `main.py`'s machine allowlists,
`desk_pub.py` and `kanban_pub.py`. The supplied routes are present and permitted
there. Recheck the deployed schema before rollout; repository contents alone do
not establish which build is running.

OAuth implementation references:
[FastMCP Google OAuth](https://gofastmcp.com/v2/integrations/google) and
[FastMCP OAuth proxy](https://gofastmcp.com/v2/servers/auth/oauth-proxy).
The tested dependency version is pinned, and the adapter adds the audience and
verified-identity checks not enforced by that version's Google verifier.
