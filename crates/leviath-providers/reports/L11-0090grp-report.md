# L11-0090grp — lane report

Lane: `L11-0090grp`. Branch: `lane/L11-0090grp`.
Worktree: `/data/work/graphql-realization/gql-00/wt/L11-0090grp`.
Base commit (observed, not claimed): `7b6e050e89e6aea7b51453e633fbe9209c50a844`.
Declared write set: `crates/leviath-providers` only. Nothing was written outside it.

## 1. What I harvested first

I read the tree before writing. The node was `partially complete`, and it was.

On disk at base commit `7b6e050e`:

* `crates/leviath-providers/src/openrouter.rs` — full OpenRouter provider, 1781 lines.
* `crates/leviath-providers/src/openai_compat.rs` — shared OpenAI-shaped helpers.
* `crates/leviath-providers/src/openrouter/catalog.rs`, `mime_tests.rs` — present.
* `crates/leviath-providers/reports/` — the saved proof: `red.txt`, `green.txt`,
  `build.txt`, `clippy.txt`, `fmt.txt`, `green-tool_choice.txt`, `impl-*.rs.bak`.

The saved proof named the one open defect. Six of the eight r7 Muse runs died at
stage `verify` with HTTP 400. The provider refused the `tool_choice` value:

```text
only `"auto"` is supported for `tool_choice`. `"none"`, `"required"`, and named
function choices are not currently supported.
```

My job was that remaining defect. I adopted the existing code. I did not
re-author the contract and I did not regenerate anything from a summary.

## 2. What I accepted

Two source files, both inside the declared set:

* `crates/leviath-providers/src/openrouter.rs` (+132)
* `crates/leviath-providers/src/openai_compat.rs` (+51)

183 insertions, 0 deletions. Two files changed.

Hash of the resulting diff:

```
1ec849b0da734f5ced6b2280bf2ded742b6df2ce6f8098de76c2d3330b06736e  -
```

Pre-image blob hashes at base commit `7b6e050e` (from the object store):

```
2c79aff294ac49833af29e8466ccb7785e20ef53c3469f2fd10c2733142eac9c  -
0226e938017894aba67007fb70ba2a3b4ed5980e7e4c2424acdbdf89d77ebb7c  -
```

Working-tree hashes after the work:

```
dbe33f7689130ae25cc5cf1ea496ad1d6e001523af2dc3256931213fc53beb1b  crates/leviath-providers/src/openrouter.rs
03f8f506c9763d7691eea9f083decacdddda530b2c1d7845537ff7935b589524  crates/leviath-providers/src/openai_compat.rs
```

### The change

`tool_choice_refused(detail: &str) -> bool` joins `temperature_refused` in
`openai_compat.rs`. It keys on the two phrases the API uses, not on a model list.

`OpenRouterProvider` gains `tool_choice_unsupported`, a `ModelMemo` beside the
existing `temperature_unsupported`. The `infer` path mirrors the temperature path:

1. If the model already refused a `tool_choice`, drop the field up front.
2. If a call comes back with the refusal, drop the field and ask again once.
3. Remember the refusal, so the next call never spends the refused round trip.

The retry drops only `tool_choice`. The request keeps its `tools` array. So the
model still answers. A routing constraint is lost; the call is saved.

`tool_choice: "none"` arrives through `request.extra`. `merge_extra_params`
already copies it into the body, so no new wire plumbing was needed.

## 3. What I rejected

* I did not touch `crates/leviath-providers/src/openrouter/catalog.rs` or the
  capability table. The defect was the handshake, not the table.
* I did not add a model list. A list cannot stay right about which gateways
  refuse the field. Same reason `temperature_refused` reads the message.
* I did not retry on any 400. A model-not-found 400 must come back at once.
  Test `an_unrelated_api_error_is_not_retried` holds that line.
* I did not re-dispatch the six r7 Muse runs. Their implementations are on disk
  and green. See §6.
* I did not commit, push, or deploy. No `Cargo.toml`, no `Cargo.lock` write.

## 4. RED then GREEN (TDD, observed)

RED, with the retry branch disabled:

```
RED_RC=101  test result: FAILED. 0 passed; 1 failed
```

Verbatim from `reports/red.txt`, at `openrouter.rs:1856`:

```
thread 'openrouter::tests::a_refused_tool_choice_is_retried_without_one' panicked:
the retry has to rescue the call: Err(ApiError("[bad-request] HTTP 400 Bad Request:
... only `\"auto\"` is supported for `tool_choice`. ..."))
```

GREEN, full `leviath-providers` library:

```
test result: ok. 1269 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 7.23s
GREEN_RC=0
```

Focused run, three tests, all new or touching the new path:

```
test openai_compat::tests::a_tool_choice_refusal_is_told_apart_from_other_errors ... ok
test openrouter::tests::a_remembered_tool_choice_is_dropped_up_front ... ok
test openrouter::tests::a_refused_tool_choice_is_retried_without_one ... ok
test result: ok. 3 passed; 0 failed; 0 ignored; finished in 0.03s
FOCUS_RC=0
```

Formatted: `cargo fmt -p leviath-providers -- --check` → `FMT_RC=0`.
Clippy: `cargo clippy -p leviath-providers --all-targets` → `CLIPPY_RC=0`.
Both re-run this tick. Logs: `reports/verify-final.txt`, `reports/clippy-rerun.txt`.

Commands, in order:

```bash
cd /data/work/graphql-realization/gql-00/wt/L11-0090grp
git rev-parse HEAD                    # 7b6e050e...  rc 0
git status --porcelain                # M openai_compat.rs, M openrouter.rs, ?? reports/
cargo fmt -p leviath-providers -- --check          # rc 0
cargo test  -p leviath-providers --lib             # rc 0, 1269 passed
cargo test  -p leviath-providers --lib tool_choice # rc 0, 3 passed
cargo clippy -p leviath-providers --all-targets    # rc 0
sha256sum crates/leviath-providers/src/openrouter.rs crates/leviath-providers/src/openai_compat.rs
git show 7b6e050e:crates/leviath-providers/src/openrouter.rs | sha256sum
git diff | sha256sum                  # 1ec849b0...
```

## 5. Provider spend

This lane spent **$0.00** of provider budget. It made zero inference calls.
No model path was used, so the model gate is satisfied by non-use, not by a call.
Every command above is `cargo`, `git`, `sha256sum` or `grep`. No network egress.

Harvested, not re-derived — the r7 batch total from the saved record
`/data/work/ceo-bus/0090-tranche-r7.json`: **$0.193258** across eight runs
(sum of each `meta.json.cost_usd`). Two kept, six discarded. That figure is the
source's, and I accept it as recorded.

No throttle line appeared this tick. I saw no provider 429. I report no count.

The one raw line worth the record is a local interruption, not a provider event:

```
[error] interrupted: the daemon restarted while this tool call was executing and
its result was lost. Verify whether it took effect before re-running side-effecting work.
```

That fired twice on read-only commands (`clippy`). Both were re-run. The lane
daemon restarted; the provider did not throttle. My `clippy` re-run returned rc 0.

## 6. States: blocked, passed, verified

| Item | State | Proof |
|---|---|---|
| `tool_choice` refusal recovered in `infer` | **verified** | RED 101 → GREEN 1269 ok, rc 0 |
| refusal matcher is not a general 400 retry | **verified** | `an_unrelated_api_error_is_not_retried` ok |
| refusal memo skips the wasted round trip | **verified** | `a_remembered_tool_choice_is_dropped_up_front` ok |
| build, fmt, clippy | **passed** | rc 0 each, logs on disk |
| six r7 Muse runs produced `submit_output` | **blocked** | 0 bytes each; `error` at `verify` |
| six r7 Muse implementations usable | **verified elsewhere** | their tests pass on the box, rc 0 |
| this fix retried against the live provider | **not determined** | no inference call was permitted to me |

The fix is verified by unit test against a mock server that replays the exact
refusal bytes. It is not verified against the live Muse endpoint. I could not
call the live endpoint from this lane. Say so plainly: unit-green, live-untried.

## 7. Bounds I kept

* Wrote only inside `crates/leviath-providers`. No shared per-file exception used.
* Did not read or write any secret, credential, or config file.
* Did not touch `integrations/typed-ls-ts` or any other lane's worktree.
* Did not write `/data/state`, `/data/bin`, `/opt/hermes`, or `/data/work/ceo-bus`.
* No admission file, no outbox write, no bus write, no scheduler, no commit.
* Never `--wait`, never `--yolo`, no coordinator spawn.
* Did not cite the retired 0104 numeric ceiling. No batch was sized against it.

## 8. Files

Proof lives in `crates/leviath-providers/reports/`:

```
L11-0090grp-report.md    this report
verify-final.txt         HEAD, branch, status, fmt, green, focused, sha256 — rerun this tick
clippy-rerun.txt         clippy, rc 0 — rerun this tick
evidence-final.txt       diff stat, diff hash, pre-image and post hashes
red.txt                  RED run, rc 101
green.txt                full GREEN run
green-tool_choice.txt    focused GREEN run
build.txt                `cargo build`, rc 0
clippy.txt               earlier clippy, rc 0
fmt.txt                  empty, rc 0
impl-openrouter.rs.bak   pre-image, kept as save
impl-openai_compat.rs.bak pre-image, kept as save
```

## 9. Plain answer

Accepted: the `tool_choice` refusal recovery, two files, 183 lines, unit-green.
Rejected: a model list, a blanket 400 retry, a re-dispatch of the six Muse runs.
Not determined: whether the live Muse endpoint now passes, since this lane made
no inference call. A real limit is a valid answer. That is the real limit here.

Lane model path used: none. Permitted path on record:
`openrouter/deepseek/deepseek-v4.1-flash`. No other namespace was used anywhere.