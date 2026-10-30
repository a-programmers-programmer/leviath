# Lane L09-0150b — node 0150-B — harvest report

Author: worker run `worker-1789438986-c61f1f5864ee`.
Model used for every inference: `openrouter/deepseek/deepseek-v4.1-flash`.
Directive 0193 plus amendments 00:55Z, 01:05Z, 01:12Z. Amendments win.
Status: harvest complete. No product write was made. Reason given below.

## 0. Bottom line

I could not complete node 0150-B in this lane. The part B work lives on disk
under `/data/work/ceo-bus`. That path is outside my declared write set and the
task text forbids it outright. My declared path set holds no part B artifact.
I raise that as a decomposition request instead of writing outside the set.

I did harvest the evidence. Findings:

* Roster output `0076-roster.md` EXISTS and is ACCURATE. I re-derived its
  count line myself and it matches.
* Roster GENERATOR code does NOT exist. No script builds the roster from the
  manifest. So the C6 deliverable "roster generator" is unmet.
* The part B definition-of-done file `0150-notion.md` does NOT exist anywhere.
* The C6 write-side run FAILED with `status=error`.
* The StarterTown workstream row is a projection receipt claim only. I did not
  verify it, and I did not create another.

## 1. Worktree, branch, base commit

* branch `lane/L09-0150b`
* worktree `/data/work/graphql-realization/gql-00/wt/L09-0150b`
* observed base commit `7b6e050e`

Command:

    cd /data/work/graphql-realization/gql-00/wt/L09-0150b && git rev-parse HEAD

Result: `7b6e050e` (GQL-02c corrected acceptance packet). `EXIT=0`.
`git diff --stat 7b6e050e` before my report: empty. `EXIT=0`.

## 2. Declared path set — the core conflict

My only writable path is:

    /data/work/graphql-realization/gql-00/wt/L09-0150b/crates/leviath-scripting

I searched that subtree for any part B content. Command:

    cd /data/work/graphql-realization/gql-00/wt/L09-0150b && \
      grep -rIl --exclude-dir=.git -e "0150" -e "0076" -e "roster" .

Result: no part B hit. The only matches are unrelated files
(`crates/leviath-cli/agents/*/agent.leviath`, `providers.rs`). `EXIT=0`.

The crate holds 12 Rust files, 5001 lines, and no Notion or roster code.
So the declared path set does not contain this node's work.

Command:

    find crates/leviath-scripting -type f -name '*.rs'

Result: `src/{engine,functions,lib,mime_check,output_validator,parts,region_hook,stage_hook,tool,types}.rs`,
`src/output_validator/tests.rs`, `tests/bundled_web_tools_compile.rs`. `EXIT=0`.

Every real part B artifact sits under `/data/work/ceo-bus`. The task text says:
"No write under `/data/work/ceo-bus`". So I cannot produce the deliverable here.

Decision: I wrote only this report, inside the declared set. I did not write
`Cargo.toml` or `Cargo.lock`. I did not touch any other lane.

## 3. What already exists — harvested artifacts (read only)

All paths below are under `/data/work/ceo-bus`. I read them, I did not write them.

    cd /data/work/ceo-bus && sha256sum 0076B3-overlay.json \
      outbox/0076-roster.md outbox/0150-done.md \
      outbox/0150-m2-verdict.md outbox/0150-ack.md

Result (`EXIT=0`):

| file | bytes | sha256 |
|---|---|---|
| `0076B3-overlay.json` | 25000 | `9a769ddf14883186f528de8f768b97523b690731bb266c1152aa2bc2b4a0ce15` |
| `outbox/0076-roster.md` | 10290 | `9e87fd91ded871c9f8e2b3c61af5f8563e539ae2ada56a0bb969d4e99347ca16` |
| `outbox/0150-done.md` | — | `80c0846dcca32ece6095d8a785d5198d8694bde7609e239e405b164fda0566f8` |
| `outbox/0150-m2-verdict.md` | — | `cf1c118da73cbff5bf57973a59f246917899a31461e69c949f85ad6515a8b2ca` |
| `outbox/0150-ack.md` | — | `8dce237a1cc9c1c9c21cd7fcca41fffa94024514ba001902b2ca75a858250039` |

### 3.1 The roster output is correct — verified by hand

The roster claims 35 entries, 10 pinned models, 25 unpinned. I did not trust
that. I parsed the manifest myself.

First parse, using a JSON key regex, found 0 pinned. That was MY error: the
manifest stores TOML text in a `text` key, not a JSON `model` key. I corrected
the parse. Second parse, reading `model = "..."` out of `text`:

    python3 -c "import json,re; d=json.load(open('0076B3-overlay.json')); ..."

Result: 35 entries, 7 pinned from `text`, 28 unpinned. `EXIT=0`.

Then I added the `set_model` entries, whose pin sits in a `value` key
(`grok-scout-only-ceo`, `grok-scout-only-cto`, `grok-scout-only-researcher-model`).

Result:

    CORRECTED pinned entries = 10
    unpinned = 25
    roster says 10 / 25 -> True
    EXIT=0

I also checked row coverage and the three count-line numbers:

    roster rows total: 35
    roster names present: 35 of 35
    roster count line 35: True
    roster count line 10: True
    roster count line 25: True
    EXIT=0

Verdict: `outbox/0076-roster.md` is accurate. I ACCEPT that artifact.

### 3.2 The roster GENERATOR does not exist

The C6 dispatch asks for a generator, not only a one-time table. I searched:

    cd /data/work/ceo-bus && grep -rIln "0076B3-overlay" .

Result: only reports and logs mention the manifest. `EXIT=0`.
Then:

    ls -la notion_roster.py notion_write.py notion_workstream.py

Result: `No such file or directory` for all three. `EXIT=2`.

So no code builds the roster from the manifest. The one table that exists was
written by hand in one run. Verdict: the generator deliverable is UNMET.

### 3.3 The part B definition of done is absent

Part B's DoD file is `0150-notion.md`. Two searches:

    ls -la outbox/0150-notion.md
    find /data/work -maxdepth 4 -name "0150-notion.md"

Result: `No such file or directory`, `EXIT=2`; and zero hits, `EXIT=0`.
So the DoD was never met. No row counts, no URLs, no owed-outcome list.

### 3.4 The C6 write-side run failed

The revival dispatch is `/data/work/ceo-bus/tasks/0162-C6-notion-write.txt`
(1020 B). The run that took it:

| field | value |
|---|---|
| run_id | `worker-1789347782-5656e1990f41` |
| workdir | `/data/work/ceo-bus` |
| model | `openrouter/deepseek/deepseek-v4.1-flash` |
| status | **error** |
| iteration | 199 |
| tool_calls | 337 |
| cost_usd | 0.8257504930000003 |

So the write side errored out. The generated scripts it was told to build are
absent (§3.2). Verdict: C6 is NOT complete.

### 3.5 Part B was skipped once already, by its own gate

`outbox/0150-done.md` states part B was skipped: its gate was "only if Part A
finds banked proof incomplete", and part A came back complete. So part B
recorded zero instances and $0. Part A's M2 verdict is MET: 428 external-actor
packages, `save_returned: true`, `spawned: 420`, `failed: 0`.
That supports backlog state `partially complete` — part A done, part B open.

## 4. What I could not determine

* Whether a StarterTown workstream row truly exists in Notion. The 00:35Z relay
  claims it does at `3dc2f9fb9d288165b9bfeb12a8f1a6a4`. I did not query Notion.
  I did not create a second row, per the relay.
* Whether the durable-fact writeback works. I found related script names
  (`_tick0100_notion_rulings.py`), but no writeback path and no evidence of a
  fact written on close. I did not open them, so I claim nothing.
* Row counts before and after for any Notion database. No evidence found.

These are honest gaps. I do not invent values for them.

## 5. Decomposition request (blocking)

This node cannot be done inside its declared path set. The work needs:

1. Write access to `/data/work/ceo-bus` for the roster generator and the
   write-side scripts. That path is currently forbidden to this lane.
2. Read access to the shared Notion client for readback verification.
3. A decision on the failed C6 run. It needs a restructured task, not a blind
   retry.

I did not write any of these paths. I did not write an admission file, an
outbox file, or a bus message. Those are all outside my set.

## 6. Spend

My run `worker-1789438986-c61f1f5864ee`, model
`openrouter/deepseek/deepseek-v4.1-flash`, observed cost
**$0.2933768879999999** at iteration 79, 137 tool calls, status `running`.
This is real provider spend, read from run meta.
The failed C6 run spent $0.8257504930000003. That is prior spend, not mine.

States, plainly:

* VERIFIED: base commit, roster accuracy, roster-generator absence, DoD absence.
* PASSED: my independent re-derivation of the roster count line.
* BLOCKED: the product deliverable, by the declared path set.
* REJECTED: nothing. Every existing artifact I checked I accepted or left open.

No throttling line was observed, so no raw 429 line is reported.

## 7. Gates honoured

No commit, push, deploy, restart, or infra change. No Railway call.
No secret read or write. No new scheduler, queue, service or dashboard.
No write outside `crates/leviath-scripting`. No `Cargo.toml` or `Cargo.lock`.
No write into any other lane worktree. I did not touch
`gql-05/repo/integrations/typed-ls-ts`. I never used `--wait` or `--yolo`.
I spawned no coordinator. Read-only commands only, except this one report file.