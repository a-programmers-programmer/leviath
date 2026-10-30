# L01-gql24 — node GQL-24 verification report (CORRECTED)

VERDICT: **NOT ACCEPTED — no control surface delivered; one reported blocker is
FALSE.** Decomposition request required for the remainder. This is a
*verification* report; it records that no GQL-24 implementation exists and that
the prior version of this file cited an invented limit.

## Source identity

| Item | Value |
|---|---|
| base / HEAD commit | `7b6e050e` |
| worktree | `/data/work/graphql-realization/gql-00/wt/L01-gql24` |
| branch | `lane/L01-gql24` |
| sha256(`docs/content/api.md`) | `9b9826ba…3a78` — identical to HEAD |
| tracked diff over declared set | empty |

## Correction — the prior report was wrong on two of four blockers

The prior version of this file asserted four blockers and cited them as
"reproduced, not inferred". Two do not survive re-running.

**Blocker B (a route cannot be added) — FALSE.** The prior report claimed a new
route necessarily fails `the_openapi_spec_documents_exactly_the_routes_this_router_serves`.
It does not. That guard reads `declared_routes()`, which reads only THIS file's
own text for `.route("` literals (`production_source()` → `include_str!("mod.rs")`,
cut at `"\nmod tests {"`). A route mounted from a *different* file via `.merge()`
is served but invisible to the guard.

Experiment (in-set, reverted):

```
serve/graphql_probe.rs:
    pub(super) fn router() -> Router<AppState> {
        Router::new().route("/api/graphql-probe", get(super::config::get_config))
    }
serve/mod.rs:
    mod graphql_probe;
    let app = api_router().merge(graphql_probe::router());

$ cargo test … the_openapi_spec_documents_exactly_the_routes_this_router_serves
test result: ok. 1 passed; 0 failed;  EXIT=0
$ cargo test -p leviath-cli --lib commands::serve::tests
test result: ok. 74 passed; 0 failed;  EXIT=0
```

No `docs/schema/openapi.json` edit was required. The claim that the spec file
being outside the declared set makes a route impossible is an invented limit.

**Blocker A (no executor reachable) — evidence WRONG, conclusion survives.**
The prior report's evidence row checked `/data/.cargo/registry` and found
nothing. That is not where the crates are. `CARGO_HOME=/data/rust/cargo`, and
`async-graphql-7.2.1` (+ `async-graphql-derive`, `-parser`, `-value`) is
vendored there and in the `.crate` cache — so "no vendored executor available
offline" is false. What survives: it is in no `Cargo.toml` (`TOML_RC=1`) and no
`Cargo.lock` (`LOCK_RC=1`), and declaring it needs `Cargo.toml`, outside the
declared set. So the *standard executor* the criteria name remains out of scope
— but a hand-rolled GraphQL-shaped surface is NOT blocked by B and was never
attempted.

## Blockers that survive

**C — declared path 3 cannot be created.** TRUE, reproduced.
`commands/mod.rs:59` declares `pub mod run;` and `commands/run/mod.rs` exists,
so a sibling `commands/run.rs` is ambiguous:

```
$ touch crates/leviath-cli/src/commands/run.rs && cargo check -p leviath-cli
error[E0761]: file for module `run` found at both
  "crates/leviath-cli/src/commands/run.rs" and "crates/leviath-cli/src/commands/run/mod.rs"
```

Reverted. This one needs a decomposition request (pick one of the two forms).

**D — api.md.** Moot now: since B is false, a route could be announced and
documented in the guide. `api.md` binds `API_CAPABILITIES`
(`every_announced_capability_is_explained_in_the_guide`, mod.rs:791). It was
never written (`grep -c -i graphql docs/content/api.md` → 0).

## What was actually produced

Only the caller map, which is real and non-vacuous:
`crates/leviath-cli/src/commands/serve/L01-gql24-caller-map.md`,
sha256 `0fc3a6f7…d8eb1`, 111 lines. The control-surface half is NOT delivered.

## Requests to the coordinator

1. The report of record must be read as corrected (this file).
2. The in-scope route path (merge-mounted in-set module) is open and unblocked;
   the deliverable could be attempted without touching `Cargo.toml` or
   `openapi.json`, at the cost of a hand-rolled (non-"standard") executor.
3. For the *standard* executor and for declared path 3, a decomposition request
   is required: at minimum `Cargo.toml`/`Cargo.lock`, and either
   `commands/run.rs` OR `commands/run/mod.rs` (not both).

## Scope defect reported

Untracked `.v24_*.log` files sit in the worktree root, outside the declared set.
Not deleted — removal is equally a write outside the set. Flagged.

## Provider spend

None. No inference was issued by this lane. No throttling line appeared, so
there is no raw log line to report.
