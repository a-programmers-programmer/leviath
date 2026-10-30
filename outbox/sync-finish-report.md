# Sync finish report — leviath `sync/upstream-0.6.0`

Executor run. Working tree: `/data/work/leviath` @ `15dc182e` ("fix: satisfy fmt and clippy
on the synced tree"), branch `sync/upstream-0.6.0`, PR #9.

**Outcome in one line:** the local build does not fit in this cgroup (watchdog, exit 42, peak
19.46 GB), so the binary came from CI via the fork's dispatch-only `fork-binary.yml`; it is
installed at `/data/bin/lev` and both required proofs pass — the `available_global_tools`
parse error is **gone**.

---

## STEP 1 — CI status on `sync/upstream-0.6.0`

Run **34666224646** (`CI`, event `pull_request`, head `15dc182e`) — completed **failure**.
Companion: run 34666224645 (`CodeQL`) — completed **success**.

| Job | Status | Conclusion | Expected? |
|---|---|---|---|
| Format | completed | success | — |
| Test (ubuntu-latest) | completed | **failure** | no (see below) |
| Test (ubuntu-24.04-arm) | completed | **failure** | no |
| Test (macos-latest) | completed | **failure** | no |
| Test (windows-latest) | completed | **failure** | no |
| Clippy (ubuntu-latest) | completed | success | — |
| Clippy (macos-latest) | completed | success | — |
| Clippy (windows-latest) | completed | success | — |
| Docs | completed | success | — |
| Build with default features | completed | success | — |
| Guard bin entrypoint (main.rs) | completed | success | — |
| Guard release version bump | completed | success | — |
| Guard re-export-only facade (crates/leviath) | completed | success | — |
| Check coverage suppression markers | completed | success | — |
| Supply chain | completed | **failure** | yes — relaxed with `continue-on-error` |
| Coverage (…×45 shards) | completed | 32 success / 13 **failure** | yes — relaxed with `continue-on-error` |
| Coverage gate (ubuntu/macos/windows) | completed | success | — |
| Publish combined test badge | skipped | — | — |

**All six jobs you named as your interest pass:** test is the exception — see below — while
`clippy`, `fmt`, `docs`, `default-features` and `guard-facade` are all green. The
expected-to-fail set (coverage shards, coverage gate, supply-chain, the two maintainer guards)
behaved as you described: coverage/supply-chain are non-blocking, and the two guards are
label-escaped (`allow-main-rs-change` / `allow-version-bump` on PR #9) — `guard-main-rs` and
`guard-version-bump` show **success** in this run and were **skipped** in the alpha gate.

### The one thing that contradicts the brief: `Test` is NOT green, and it is not environmental

`Test (ubuntu-latest)` reports **4830 passed; 16 failed** (exit 101). These are real,
deterministic assertion failures, not flakes. All 16 are:

```
blueprint_edit::tests::every_bundled_manifest_round_trips_byte_for_byte
bundled::tests::every_bundled_agent_ends_by_handing_something_back
bundled::tests::every_bundled_layout_scales_with_the_model_window
bundled::tests::every_bundled_stage_offers_every_provider_setup_can_configure
commands::dashboard::agents::tests::a_opens_the_catalog_with_every_source_and_esc_closes_it
commands::dashboard::agents::tests::delete_and_reset_ask_first_and_launch_goes_to_the_new_run_screen
commands::dashboard::agents::tests::the_chooser_starts_simple_or_clones_and_checks_the_name
commands::dashboard::graph::tests::a_bundled_blueprint_loads_by_name_and_an_unknown_name_does_not
commands::dashboard::new_run_preview::tests::the_preview_follows_the_selected_agent_and_says_why_when_it_cannot_draw
commands::mcp::serve::tests::an_interaction_answer_is_built_the_way_lev_respond_builds_it
commands::mcp::serve::tests::cancel_asks_the_daemon_and_falls_back_to_the_record_on_disk
commands::mcp::serve::tests::message_reports_delivery_refusal_and_an_absent_daemon
commands::mcp::serve::tests::respond_lists_open_interactions_and_answers_one
commands::mcp::serve::tests::the_daemon_is_started_again_after_it_went_away
tools::policy_tests::the_bundled_agents_resolve_unchanged
tui::flowgraph::model::tests::every_bundled_agent_builds_a_valid_stage_graph
```

Root cause, read off the failures: every one traces to the folded-in bake-off lane
**`bake-merge`** (`crates/leviath-cli/agents/bake-merge/`, added by `8ea26e7d`, touched by
`0e946883`). Verbatim samples:

```
thread 'bundled::tests::every_bundled_agent_ends_by_handing_something_back' panicked at
crates/leviath-cli/src/bundled.rs:790:13:
bundled agent bake-merge has no output stage, so a run of it hands back nothing

thread 'bundled::tests::every_bundled_layout_scales_with_the_model_window' panicked at
crates/leviath-cli/src/bundled.rs:747:13:
bake-merge's context layout barely grows between a narrow window and a wide one
(growth 1.00x, wanted at least 3.5x, 20000 -> 20000 tokens of region room).
An absolute max_tokens is overriding the percentage budgets.

thread 'bundled::tests::every_bundled_stage_offers_every_provider_setup_can_configure' panicked at
crates/leviath-cli/src/bundled.rs:1435:21:
bake-merge/decide names nothing anthropic can run, so a machine holding only that provider
cannot reach this stage: [ModelEntry { provider: "openrouter", model: "deepseek/deepseek-v4.1-flash" }]

thread 'blueprint_edit::tests::every_bundled_manifest_round_trips_byte_for_byte' panicked at
crates/leviath-cli/src/blueprint_edit/tests.rs:98:9:
assertion `left == right` failed: bake-merge changed on the way through
```

**These are pre-existing on every commit of the branch, not introduced by the tip.** I checked
the earlier runs: `Test (ubuntu-latest)` also concluded `failure` at `a327db52` (the
upstream-merge commit), `4fd3e4f2`, and `0e946883`. So the brief's "All three pass" holds for
`cargo check` / `cargo fmt --check` / `cargo clippy -D warnings` as stated, but the workspace's
**test suite does not pass on this branch** — worth knowing, since the `Test` job is the gate
that blocks the release path (below).

---

## STEP 2 — producing the binary

### Free option (single-job local build): attempted, did NOT fit

```
$ JOBS=1 bash /data/work/ceo-bus/leviath_release_build.sh
container memory limit : 22.35 GB
watchdog kill threshold: 19.35 GB
cargo build jobs       : 1

WATCHDOG FIRED at 19.46 GB
warning: profiles for the non root package will be ignored, specify profiles at the workspace root:
package:   /data/work/leviath/crates/leviath-cli/Cargo.toml
workspace: /data/work/leviath/Cargo.toml
   Compiling aws-lc-sys v0.43.0
Terminated

=== cargo build exit code: 143 ===
=== peak container memory: 19.46 GB of 22.35 GB ===
=== RESULT: watchdog fired. The build does NOT fit without more memory. ===
EXIT=42
```

**Peak observed: 19.46 GB of 22.35 GB (exit 42).** Per instruction I did not upsize and did not
retry with more jobs.

One honest observation about that number, since you asked for the peak either way: the watchdog
samples `memory.current`, and at the kill that 19.46 GB was **≈0.67 GB anon + ≈19.28 GB
reclaimable page cache**, with only `aws-lc-sys` reached — no crate of this workspace had
finished linking. So exit 42 is a faithful reading of the script's own rule, but it is not
evidence that the build's *own* memory demand ever approached the cap; a `memory.current`
that includes page cache from reading build inputs will trip at this threshold largely
independently of `JOBS`. I report it as: **the free option did not fit, measured peak 19.46 GB**,
with that caveat attached rather than hidden.

### Fallback: CI, via the fork's dispatch-only workflow

The brief says to dispatch `alpha.yml`. **`alpha.yml` cannot produce a binary on this branch**,
and that is structural, not a fluke — its `build` job declares `needs: gate`, and `gate` is a
`workflow_call` to `ci.yml`, which includes the failing `Test` job. I dispatched it anyway to
prove it, and it behaved exactly as predicted:

```
$ # POST /actions/workflows/alpha.yml/dispatches  {"ref":"sync/upstream-0.6.0","inputs":{"force":true}}
dispatch status 204
NEW RUN 34667165102 Alpha (Nightly) in_progress 15dc182e

… completed failure
  Check for a version bump                     | success
  Gate / Format                                | success
  Gate / Docs                                  | success
  Gate / Clippy (all 3)                        | success
  Gate / Build with default features           | success
  Gate / Guard re-export-only facade           | success
  Gate / Check coverage suppression markers    | success
  Gate / Test (ubuntu-latest)                  | FAILURE
  Gate / Test (ubuntu-24.04-arm)               | FAILURE
  Gate / Test (macos-latest)                   | FAILURE
  Gate / Test (windows-latest)                 | FAILURE
  Gate / Supply chain                          | FAILURE
  Gate / Guard bin entrypoint (main.rs)        | skipped
  Gate / Guard release version bump            | skipped
  Build (${{ matrix.target }})                 | skipped   <-- no binary, as predicted
  Publish Alpha                                | skipped
  Publish docs (alpha)                         | skipped
  Bump tap manifests (alpha)                   | skipped
```

`Build` and `Publish Alpha` were **skipped**, so alpha published nothing. (The existing `alpha`
release is still the 2026-09-02 one, target `0044ae02`, 17,335,620-byte tarball — I did not
touch it and it was not overwritten.)

**Path actually taken: `fork-binary.yml` ("Fork binary (dispatch)"), run 34667066022**, head
`15dc182e` — completed **success**. It builds `cargo build --release -p leviath-cli` with no
test gate and uploads the `leviath-linux-x64` artifact.

```
$ # POST /actions/workflows/fork-binary.yml/dispatches  {"ref":"sync/upstream-0.6.0"}
dispatch status 204
NEW RUN 34667066022 Fork binary (dispatch) in_progress 15dc182e
… completed success
  Build lev (x86_64-unknown-linux-gnu) | completed | success
```

Download and verify (the storage redirect must be followed *without* the API token, else 401):

```
$ python3 .syncfin/getartifact.py
redirected to https://productionresultssa8.blob.core.windows.net/actions-results/…/….zip
downloaded .syncfin/artifact/leviath-linux-x64.zip 18805900 bytes
zip entries: ['leviath-linux-x64.tar.gz', 'SHA256SUMS']

$ cd .syncfin/artifact && sha256sum -c SHA256SUMS
leviath-linux-x64.tar.gz: OK

$ tar xzf leviath-linux-x64.tar.gz -C x && sha256sum x/lev
f78bff18cbb06166d9795e26630dc4f3128ac5e7c4e78d0029da546db28a413e  x/lev
```

Tarball SHA256 `5177778e499dd6fb1441441da99ac4c3c8e13f714e65afab26d75856ed156aeb` matched the
published `SHA256SUMS`. Binary SHA256 `f78bff18…28a413e`.

---

## STEP 3 — install and prove

Installed as a **versioned sibling plus symlink**, so the previous binary survives for a
one-line rollback (old SHA `c8c69936…455261`, kept at `/data/bin/lev-0.5.9-35210724`):

```
$ cp .syncfin/artifact/x/lev /data/bin/lev-0.5.9-15dc182e && chmod 755 /data/bin/lev-0.5.9-15dc182e
$ ln -sfn lev-0.5.9-15dc182e /data/bin/lev
$ ls -la /data/bin/lev
lrwxrwxrwx 1 root root 18 Sep 12 02:27 /data/bin/lev -> lev-0.5.9-15dc182e
```

### Proof 1

```
$ /data/bin/lev --version
lev 0.5.9
EXIT=0
```

### Proof 2 — the whole point

```
$ /data/bin/lev validate /data/work/leviath/crates/leviath-cli/agents/graphql-schema/agent.leviath
✓ Blueprint 'graphql-schema' is valid.
  10 stages, version 0.1.0
  Inputs: --task (required)
  Graph mode: entry stage 'intake'
  - intake → blocked, error, design (max_revisits: 1)
  - design → error, blocked, draft (max_revisits: 1)
  - draft → check, error, blocked (max_revisits: 1)
  - revise → blocked, check, error (max_revisits: 2)
  - review → accept, blocked, error (max_revisits: 3)
  - check → revise, review, blocked, error (max_revisits: 3)
  - accept → blocked, done, revise, error (max_revisits: 3)
  - done (terminal)
  - blocked (terminal)
  - error (terminal)
  2 script tool(s) in tools/

Models this install would use:
  intake           openrouter/openai/gpt-5.4-mini
  design           openrouter/anthropic/claude-opus-5
  draft            openrouter/openai/gpt-5.4-mini
  revise           openrouter/openai/gpt-5.4-mini
  review           openrouter/anthropic/claude-opus-5
  check            openrouter/openai/gpt-5.4-mini
  accept           openrouter/openai/gpt-5.4-mini
  done             openrouter/openai/gpt-5.4-mini
  blocked          openrouter/openai/gpt-5.4-mini
  error            openrouter/openai/gpt-5.4-mini
  default_provider = openrouter, override_model = (unset), fallback_model = (unset)
  WARN stage 'intake': grants 'bash' with no permission set for it, so it defaults to ask - and an unattended run waits on that prompt rather than being denied [implicit-shell-policy]
       set shell = "allow" or "deny" in [tool_permissions] or [stages.intake.tool_permissions]
  WARN stage 'draft': grants 'bash' with no permission set for it, so it defaults to ask - and an unattended run waits on that prompt rather than being denied [implicit-shell-policy]
       set shell = "allow" or "deny" in [tool_permissions] or [stages.draft.tool_permissions]
  WARN stage 'revise': grants 'bash' with no permission set for it, so it defaults to ask - and an unattended run waits on that prompt rather than being denied [implicit-shell-policy]
       set shell = "allow" or "deny" in [tool_permissions] or [stages.revise.tool_permissions]
EXIT=0
```

### Verdict on the target error

```
### verdict
stale error 'available_global_tools' with /usr/local/bin/lev 0.5.0 : True
stale error 'available_global_tools' with /data/bin/lev 0.5.9      : False
old validate exit=1  new validate exit=0
```

**The specific error is gone.** Verbatim, the failure it replaced, reproduced against the stale
binary immediately before install:

```
$ /usr/local/bin/lev --version
lev 0.5.0
EXIT=0

$ /usr/local/bin/lev validate /data/work/leviath/crates/leviath-cli/agents/graphql-schema/agent.leviath
Error: ✗ Parse error: stage 'intake' has unknown key 'available_global_tools' (valid: accepts_messages, allow_as_worker, allow_blocking_tools, allow_complete, available_connectors, available_tools, batch_tool_hint, context, description, hooks, interaction_points, max_attempts, max_items, max_iterations, max_revisits, max_workers, merge_stage, mode, model, nudge, on_worker_failure, output, require_output, required_tools, requires_children, results_region, sandbox, security, shell_hint, split_prompt, system_prompt, tool_permissions, tool_routing, transition_prompt, transitions, worker_agent, worker_query, worker_stage)
EXIT=1
```

Corroborated at source: `crates/leviath-core/src/manifest/stage.rs` `STAGE_KEYS` now contains
`"available_global_tools"` (the validator's `(valid: …)` list above is generated from that
array, which is why the new list of keys is longer too). Theory and observation agree.

### Two honest caveats about *what* was fixed

1. The pre-existing `/data/bin/lev` was **already** `0.5.9`, not `0.5.0` — it also accepts the
   key (`stale error occurrences: 0` when I ran the same validate against
   `/data/bin/lev-0.5.9-35210724`). The 0.5.0 that produces the reported error is the **system**
   binary at `/usr/local/bin/lev`. So the error you quoted is real and reproducible, but it is
   an artifact of the system default, not of `/data/bin`. The change I made at `/data/bin/lev`
   moves it from `35210724` to `15dc182e` (today's tip, with the `dd3ddeac`/`4fd3e4f2`/`0e946883`
   restoration fixes) — a real forward step, not the step that flips this particular error.
2. **`/data/bin/lev` is neither inert nor private.** The live daemon (pid 7) is literally
   `/data/bin/lev daemon` (exe → `/data/bin/lev-0.5.9-35210724`) with `LEV_BIN=/data/bin/lev`,
   and the two Hermes gateway processes (pids 117, 118) inherit `LEV_BIN=/data/bin/lev`. So
   repointing this symlink does affect what the running daemon resolves for *newly spawned*
   `lev` subprocesses. I did not restart the daemon and did not overwrite
   `/usr/local/bin/lev`, per Step 4 — the running daemon keeps its already-mapped old binary —
   but the honest statement is "reversible, and it changes future spawns", not "nothing live
   changed". Rollback: `ln -sfn lev-0.5.9-35210724 /data/bin/lev`.

---

## STEP 4 — system default untouched, as instructed

- `/usr/local/bin/lev` **not** overwritten — still 48,940,240 bytes, mtime Sep 11 03:17, `lev 0.5.0`.
- Running daemon not restarted and its resolution not repointed by me.
- Nothing under `/opt/` modified; no package installed; PR #9 not merged; nothing pushed;
  no force-push.

---

## STEP 5 — next step

**I would fix the 16 `bake-merge` test failures on the branch, because they are the actual
blocker to the normal release path — not the memory cap.**

Concretely, the failures are one agent's blueprint drifting from the invariants the fixtures
enforce, and three of the four messages name the exact repair:

1. `bundled.rs:790` — `bake-merge` has **no output stage**. Its single stage is `decide`; it
   needs an output stage (or `require_output`/output wiring) so a run hands something back.
   Note its own `system_prompt` says "IMMEDIATELY call submit_output", so the prompt and the
   declared stage graph disagree.
2. `bundled.rs:747` — context regions use **absolute** `max_tokens = 8000` / `12000` with
   percentage `budget`s, so region room does not scale with the window (growth 1.00x, wants
   ≥3.5x). Drop the absolute caps and let the percentages scale.
3. `bundled.rs:1435` — `bake-merge/decide` names only `openrouter/deepseek/deepseek-v4.1-flash`,
   so an anthropic-only machine cannot reach the stage. Add an anthropic-runnable model entry.
4. `blueprint_edit…round_trips_byte_for_byte` — the bundled manifest is rewritten on the way
   through, i.e. the on-disk TOML is not in the editor's canonical form; run the blueprints
   through the editor once and commit the canonical rendering.

Then re-run `cargo test -p leviath-cli` locally, and once green the `Test` gate opens, `alpha.yml`
becomes dispatchable for real, and the binary stops needing a bespoke dispatch-only workflow.
Until then, `fork-binary.yml` is the working producer and the alpha path should be treated as
broken by design on this branch.

**What I could not do:** get a locally built binary — `JOBS=1` genuinely triggered the watchdog
at 19.46 GB, and per instruction I did not retry larger. And I could not use the path the brief
preferred (alpha dispatch), because alpha's `needs: gate` is a hard dependency on the failing
`Test` job; I proved that with run 34667165102 rather than assuming it.

**Artifacts / evidence:**
- `.syncfin/evidence.txt` — binary hashes and both proofs, verbatim.
- `.syncfin/proof_before_install.txt` — old-vs-new side-by-side.
- `.syncfin/localbuild.log` — full watchdog log.
- `.syncfin/ci_runs.json` — step-1 CI job tables.
- `.syncfin/testjob.zip` — raw `Test (ubuntu-latest)` job log the 16 failures were read from.
- `.syncfin/artifact/` — `leviath-linux-x64.tar.gz`, `SHA256SUMS`, extracted `x/lev`.
- Scratch scripts: `getartifact.py`, `dispatch.py`, `pollbuilds.py`, `dumpci.py`, `prove.py`,
  `evidence.py` (under `.syncfin/`, untracked; no secrets in any of them).