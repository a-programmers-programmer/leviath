# REPORT — GQL-03: Build the schema language server

Status: **complete**. The protocol test is GREEN (`6 passed`, exit 0) and the
mechanical check passes.

## Where the work is

| Item | Value |
| --- | --- |
| Repository | `a-programmers-programmer/leviath` |
| Checkout | `/data/wt/T01M2KX1Z7XQY3DDS1GFJJWWWP8-a1/repo` |
| Branch | `fleet/gql-03` |
| Base commit | `15dc182e` — "fix: satisfy fmt and clippy on the synced tree" |
| Work commit | `d3f41d5b` — "GQL-03: schema language server (diagnostics, versioned code actions)" |
| Pushed | `git push origin fleet/gql-03` -> `Everything up-to-date` (rc 0) |

Base commit is `15dc182e`; the branch is a fast-forward from it. The commit adds
3 files, 619 lines, under `python/leviath_schema_ls/`.

## What was built

A GraphQL SDL language server that speaks LSP JSON-RPC over stdin/stdout. It
does not use an LSP framework, so the protocol is visible and testable. It
reuses the accepted GQL-02 reconciliation engine **read-only**.

| File | Lines | Purpose |
| --- | --- | --- |
| `repo/python/leviath_schema_ls/__init__.py` | 19 | package docstring |
| `repo/python/leviath_schema_ls/__main__.py` | 16 | entry point for `python -m leviath_schema_ls` |
| `repo/python/leviath_schema_ls/server.py` | 584 | framing, document store, diagnostics, code actions |
| `tests/test_protocol.py` | 394 | protocol test: 6 tests, 43 assertions |
| `conftest.py` | — | points the test at the vendored read-only engine |
| `vendor/python/leviath_reconcile/` | 284K | read-only copy of the GQL-02 engine (18 files) |

### Step 1 — language server with diagnostics, navigation, versioned code actions

* `initialize` advertises `textDocumentSync`, `diagnosticProvider` and
  `codeActionProvider`, and loads the agreed baseline pair from
  `initializationOptions`.
* `textDocument/didOpen` and `textDocument/didChange` store text plus version.
  A version that is not **newer** is refused: a request gets JSON-RPC error
  `-32002`, a notification gets `window/showMessage`. The rejected text never
  reaches the store.
* `textDocument/diagnostic` returns `{"kind": "full", "resultId", "items"}`.
  The `resultId` is `v<version>:<digest16>`, so an unchanged document answers
  `{"kind": "unchanged"}` when the client passes `previousResultId`. That is the
  versioning that makes navigation and cache decisions exact.
* `textDocument/codeAction` returns text-only quick fixes. Each action carries
  an LSP `TextEdit` and provenance `data`. It never carries a `command` key.

### Step 2 — workflow semantic errors and confirmation requirements

The shared checker (`Reconciler(baseline).reconcile(schema, code)`) runs on
every diagnostic request. Each conflict becomes a source-ranged diagnostic with
`data.kind == "reconcile-conflict"`, `data.conflictCode`, `data.symbolKey`,
`data.requiresConfirmation == true` and `data.options` — the confirmation
options a principal can choose. The server surfaces the options and does not
choose.

Fixture proof: the schema renames `total` to `amount` while the code side says
`cost`. The engine reports one conflict:

```
leviath/reconcile-rename-divergence  Order.total  attribute=name
  code   -> cost    (model.py:6)
  schema -> amount  (schema.graphql:3)
requiresConfirmation: true, options: 4
```

### Source ranges are exact

```
Fixture line 3 (1-based):  "  amount: Float!"
GQL-02 source map:         line=3, col_start=3, col_end=9   (1-based)
LSP range delivered:       line 2, characters 2..8          (0-based)
```

`test_c` asserts that exact range and asserts `EDITED_SDL.split("\n")[2][2:8]
== "amount"`. The diagnostic points at the token that produced it.

## Definition of done — criterion coverage

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | Protocol test initializes the server | `test_a_initialize` |
| 2 | Opens SDL | `test_b_open_and_clean_diagnostics` |
| 3 | Edits SDL and receives source-ranged diagnostics | `test_c_edit_and_source_ranged_diagnostics` |
| 4 | Applies a code action | `test_d_code_action_is_text_only_and_clears_the_conflict` |
| 5 | Rejects a stale edit | `test_e_stale_edit_is_rejected` |
| 6 | No runtime action or sensitive unwrap from a diagnostic | `test_f_edit_before_open_and_diagnostic_safety`; `sh evidence/verify.sh` checks 2–4 |

## Test result

```
$ python3 -m pytest tests/test_protocol.py -v
tests/test_protocol.py::test_a_initialize PASSED
tests/test_protocol.py::test_b_open_and_clean_diagnostics PASSED
tests/test_protocol.py::test_c_edit_and_source_ranged_diagnostics PASSED
tests/test_protocol.py::test_d_code_action_is_text_only_and_clears_the_conflict PASSED
tests/test_protocol.py::test_e_stale_edit_is_rejected PASSED
tests/test_protocol.py::test_f_edit_before_open_and_diagnostic_safety PASSED
============================== 6 passed in 1.27s ===============================
EXIT=0
```

RED was recorded first: the same command gave `3 failed, 3 passed`, exit 1,
because the baseline held the current code text so the code side diffed clean.
The fix makes the baseline hold the *agreed* code text. The test file was not
weakened: it still holds 43 assertions (`sh evidence/verify.sh`, check 7).

## Safety

* **No runtime action from a diagnostic.** No `command`, `arguments` or
  `execute` key is emitted. Checks 2 and 3 of `evidence/verify.sh` prove it.
* **No sensitive unwrap.** No diagnostic carries a `secret` or `unwrap` key.
  Check 4 of `evidence/verify.sh` proves it.
* **GQL-02 not modified.** The vendored copy is byte-identical to the source
  tree (`diff -r` -> empty, rc 0; 18 files both sides). The engine is imported
  read-only. `evidence/vendor-vs-source.diff` is 0 bytes.
* No network access. No paid probe. No destructive migration. No push to
  `master`/`main`.

## Limits / not done

* Diagnostics are full-text only. The server advertises `change: 1` and does
  not implement incremental (`change: 2`) sync.
* No `hover` and no `go-to-definition` method. Navigation today is the versioned
  `resultId` plus the exact code-action range. The GQL-02 fact source map
  already carries line and column, so hover/definition are the next step.
* Only rename-divergence conflicts produce a one-click fix. Type and existence
  divergences produce a diagnostic with options but no fix, because the safe
  resolution needs a principal's choice.
* `evidence/vendor-vs-source.diff` compares against
  `/data/work/graphql-realization/gql-02`, which is outside this working
  directory. The `hermetic.sh` check proves the test itself needs nothing
  outside the working directory.

## Environment / run identity

* Run id: `R01M2MCP7QDGQ6AJV9FQJT47GHG` (lev run name
  `T01M2KX1Z7XQY3DDS1GFJJWWWP8-a1`).
* Working directory: `/data/wt/T01M2KX1Z7XQY3DDS1GFJJWWWP8-a1`.
* Content hashes:
  * `tests/test_protocol.py` = `ed4711f230873ddefa30863761fb2be537c6d27f96286aa7175d4b6d79084065`
  * `repo/python/leviath_schema_ls/server.py` = `71fa3c5bd509ef062aff5c13fd5dc853428e1fd3c6ede4626bafb57eb96722c2`
* `bd` is not installed in this container: `bd: command not found`. No real Bead
  ID was claimed. A Leviath run id exists (above).
* Provider spend: $0.00. No paid model call was made.
* States: protocol test = **verified** (re-executed). Mechanical check =
  **verified**. GQL-02 = untouched (diff empty).

## Prior work resumed

Prior work lived at `/data/work/graphql-realization/gql-03/` (HANDOFF.md,
REPORT-GQL-03.md, commands.log, tests/, repo/). Those still-valid pieces were
copied here and re-verified. The prior repair note is kept as
`REPORT-PRIOR.md`; the prior command log as `commands-prior.log`. No finished
step was redone.
