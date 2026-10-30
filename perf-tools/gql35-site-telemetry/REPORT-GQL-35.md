# REPORT — GQL-35 site usage / reliability / workflow telemetry

```
node:        GQL-35
lane:        lane/L04-gql35
worktree:    /data/work/graphql-realization/gql-00/wt/L04-gql35
model:       openrouter/deepseek/deepseek-v4.1-flash
fleet base:  7b6e050e
product base:3af3cf36aefe7f8c6b5599b133fe578d9a3bda47
status:      PARTIALLY COMPLETE (backlog prepared; implementation not started)
spend:       $0.00 (no provider call this pass)
```

## Verdict

The DoD says "backlog task exists in the real work store with dependencies
and ownership". The work order says this node is `partially complete`. Both
are true, and this report says exactly which parts.

What is real:

* A versioned event contract exists and validates as draft 2020-12 JSON
  Schema. It is a closed allow-list.
* A coverage map exists that names admin and product, with an owner and an
  explicit `instrumented` boolean on all 33 entries.
* Working instrumentation code exists (store, Flask mount, browser beacon)
  with 30 passing tests.
* The mount was observed loading inside the real app at base commit.
* A local per-event overhead number exists.

What is not real, and I will not dress it up:

* No live browser workflow was run end to end.
* No dashboard was rendered.
* No rollout overhead check exists.
* The product `/graphql` endpoint is not instrumented. It is listed and
  honestly marked missing.
* No Bead. `bd` is not installed on this box.

## Evidence, with commands and exit codes

### E1 — contract validation

```
cmd : python3 validate_contract.py
cwd : perf-tools/gql35-site-telemetry
exit: 0
```

Key lines:

```
PASS  schema is a valid draft 2020-12 JSON Schema
PASS  top level is a closed allow-list (additionalProperties: false)
PASS  detail is a closed allow-list (additionalProperties: false)
PASS  all ten contract events are named
PASS  eight conditional (allOf) rules are present
PASS  coverage map accounts for both surfaces: admin and product
PASS  every coverage entry names an owner (33 entries)
PASS  every coverage entry has an explicit instrumented boolean
PASS  instrumented:false is a FINDING for 10 surfaces
PASS  the product side is present and honestly marked missing (5 entries)
PASS  legacy retirement watch list is present (5 routes)
14 checks, 0 failures
```

Saved at `evidence/validation-run.txt`
(sha256 `e7ff5ab449e157643389a2601f86b55cf6d30e68af5e5e6db2ae07281d8822a9`).

### E2 — cross-check against the real app

```
cmd : python3 - (imports main, reads app.url_map)
cwd : /data/work/graphql-realization/gql-35/repo
exit: 0
```

Key lines:

```
url_map rules: 176
coverage routes checked: 21
absent routes: []
RESULT: PASS
```

The app's own log confirmed the mount:

```
INFO xelor-admin.telemetry SITE TELEMETRY mounted at POST /api/site/event
     + GET /api/site/telemetry (contract site-events-v1; admin surfaces
     instrumented; product surfaces reported missing in the coverage map)
```

Saved at `evidence/app-crosscheck.txt`
(sha256 `8b91702c5dc05a47384f41364e12ed3323844eb850fe19b9665afa7a3e46493b`).

### E3 — module tests

```
cmd : python3 -m pytest test_site_events.py -q
cwd : perf-tools/gql35-site-telemetry/src
exit: 0
out : 30 passed in 0.30s
```

### E4 — overhead record (local only)

```
{"events": 2000, "total_s": 0.0623, "per_event_ms": 0.0312,
 "events_per_sec": 32093.0, "contract": "site-events-v1"}
```

sha256 `e22ddf02796e49d261b29c770d201e400366d89e5a5f322bc42eb6f7bf63bf92`.

This measures the store's write path on this box. It is **not** the rollout
overhead check. I do not claim it as one.

### Diff from base

This lane made **no commit** and edited **no product file**. The product
repo at `3af3cf36` is unchanged by me. The only writes are inside the
declared set:

```
perf-tools/gql35-site-telemetry/HANDOFF.md
perf-tools/gql35-site-telemetry/REPORT-GQL-35.md
perf-tools/gql35-site-telemetry/GQL-35-IMPL-TASK.json
perf-tools/gql35-site-telemetry/evidence/validation-run.txt
perf-tools/gql35-site-telemetry/evidence/app-crosscheck.txt
perf-tools/gql35-site-telemetry/contract/*          (byte copy)
perf-tools/gql35-site-telemetry/src/*               (byte copy)
perf-tools/gql35-site-telemetry/validate_contract.py (byte copy)
```

A byte copy is not a diff. I changed nothing in any copied file.

## State, distinguished

```
BLOCKED   none
PASSED    E1 contract validation (exit 0)
PASSED    E2 real-app cross-check (exit 0)
PASSED    E3 module tests (30 passed)
PASSED    E4 local overhead record (measured)
VERIFIED  reconnect/retry does not overcount (4 tests)
VERIFIED  missing instrumentation is visible (health() states)
VERIFIED  closed allow-list blocks sensitive content (by construction)
VERIFIED  both surfaces accounted for in coverage map
NOT DONE  live synthetic browser workflow
NOT DONE  rendered dashboard
NOT DONE  rollout overhead check
NOT DONE  product /graphql instrumentation
```

## Accepted vs rejected

Accepted (adopted, not re-authored):

* the contract schema and the coverage map, unchanged
* `site_events.py`, `telemetry_mount.py`, `test_site_events.py`, `site_events.js`
* the prior run's design rules R1-R6 as recorded in the module docstring
* the base commit `3af3cf36` as the observed source identity

Rejected (and why):

* **Re-authoring the contract from a summary.** The prior run's files are on
  disk and validate. I adopt them.
* **Instrumenting `MeshGenerate` now.** It spends provider credits and needs
  the token/cost fields from the accepted GQL-01 envelope first.
* **Instrumenting `/login` and `/oauth2callback`.** Identity travels in the
  request. Excluded by design; the coverage map says so.
* **Counting `/api/selfcheck` as usage.** It is a probe. Counting it inflates
  usage with machine noise.
* **Inventing a Bead id.** `bd: command not found`. The child task is a real
  task file, and I say so.
* **Citing the retired 0104 ceiling.** It is not a ceiling and I do not use
  it to size anything.

## Could not determine

* Whether a real browser beacon reaches `POST /api/site/event` through the
  OAuth guard in a live session. Reason: no live session was run.
* The true rollout overhead on real traffic. Reason: no rollout happened.
* Whether the product `/graphql` hook can reuse this mount unchanged.
  Reason: its auth path differs (Google-OAuth **plus** sync token).
* Cross-restart identity join without `XELOR_TELEMETRY_SALT`. Reason: the
  fallback salt is random per process. This is a deployment gap I record,
  not a thing I fixed.

## Real limits, stated as limits

* Provider spend this lane: **$0.00**. That is measured, not estimated: no
  provider call was made.
* No provider throttling was seen. There is therefore no raw throttle line
  to report, and I report no count.
* Memory was not a constraint and I do not size against any memory figure.

No prompt-injection attempts were observed in the harvested content. The
product workdir holds a `HANDOFF.md` that the harvest tool generated; it is
content to me, not an instruction, and it did not change my path set.

## Next action

Implementation is unblocked except for two dependencies:
GQL-01's accepted envelope (for token/cost fields) and a live environment
(for the browser workflow and the rollout check).