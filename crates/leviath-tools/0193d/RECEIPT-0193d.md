# RECEIPT-0193d — node 0193 D / collector

Lane: `L10-0193d`. Branch: `lane/L10-0193d`.
Worktree: `/data/work/graphql-realization/gql-00/wt/L10-0193d`.
Declared write set: `crates/leviath-tools` (this directory only). Nothing else written.

## 1. What this lane was asked to do

Repair acceptance and operating receipt, preserved from existing artifacts.
Harvest before producing. Adoption beats re-authoring.
Backlog state on record: `partially complete`.

## 2. Base commit and resulting diff

Observed source identity, read from disk, not from a summary:

```
$ cd /data/work/graphql-realization/gql-00/wt/L10-0193d
$ git rev-parse HEAD
7b6e050e89e6aea7b51453e633fbe9209c50a844
$ git rev-parse --abbrev-ref HEAD
lane/L10-0193d
```

The base commit equals the declared base `7b6e050e`. Confirmed by observation.

Resulting diff:

```
$ git status --porcelain
?? crates/leviath-tools/0193d/
STATUS_EXIT=0
$ git diff --stat HEAD
DIFF_EXIT=0
```

The diff is EMPTY. `git diff --stat HEAD` prints nothing. My only change is one
UNTRACKED directory inside my declared set. No tracked file changed.

## 3. State of the artifacts I harvested

### 3.1 Prior collector run — FOUND, complete, read-only

Run id `worker-1789426305-859645918bae`, model
`openrouter/deepseek/deepseek-v4.1-flash`, status `complete`, error null.

```
run dir exists: True
blobs            <dir>
context.json         35286 bbf1f9f09b105b37
final_output          2940 6ac0e539bc316c2a
meta.json             8475 eae4cc97d579654e
run.lvr             575702 e5eb0f65ca8a9c54
stages           <dir>
stages.json           5126 ...
```

Its delivered artifact, `receipt.md`, 12403 B, sha256
`464622ac00305b15142e044288b625f29b93bb42b33f3b4110a81f54fa3d3ebb`, was published
twice and the two copies are byte-identical:

```
$ cmp -s <scratch receipt.md> <outbox copy>; echo CMP_EXIT=$?
CMP_EXIT=0
```

I did NOT re-author that receipt. I adopted it. It is the harvest.

### 3.2 The published receipt is on disk and intact

```
$ ls -l /data/work/ceo-bus/outbox/0193-worker-evidence-harvest-20260914T2252Z.md
-rw-r--r-- 1 root root 12403 Sep 14 22:53 ...  LS_EXIT=0
$ wc -l <that file>
229   WC_EXIT=0
```

All 7 required `##` sections are present, in order (lines 13, 38, 90, 118, 168,
193, 224). Claimed size 12403 B matches observed size 12403 B.

### 3.3 Owner-reported hashes still match disk — VERIFIED

10 artifact rows named by owners A and B. I re-hashed each. ALL match exactly.

```
20702|1052529db03ec6ff9ea018c7aa47cf6d2d5d64352dafb6a17aa10d4890daf0ff|/data/bin/leviath_bridge_0193.py
46550|a809715bd9f13444a9f1b017a44896a3c0030ab061bd011d05b04e76f09bf42a|/data/bin/hermes_boot.py
44703|8ca3795dfd9003c141a922c30d55a365d3dc3461f0e14b44aa5345a346ea5517|/data/bin/hermes_boot.py.bak-0193-20260914T214955Z
14724|d1de2699178f51377e35ab329761361d19eb11b3c599b722139cb2588c3c9473|/data/bin/test_0193_bridge.py
11251|1f6b61e00a696e9cbfee6d50586a59e2508faf8b82c73e2312f52bcd68ab7122|/data/bin/REPORT-0193-BRIDGE.md
51902|ebad507409348e7891303b6e4ceb3dd2176b7af2ee4cb5400f8629ae91007d24|/data/work/ceo-bus/ceo_bus.py
47871|65ec4bc02e7c5e5ca3becaba87787f603f38c2379f7bd0bfda3575f39370e227|/data/work/ceo-bus/ceo_bus.py.bak-0193-20260914T220801Z
 6102|c5de1fefb2274292a4a23a193f50d9049429104c0c95cf6d7a6121624fc52560|/data/work/ceo-bus/test_ceo_bus_bounded_pass.py
 3758|f9fbb4dc9d74a43e5da81f7b85114a69a2ac9539747518727bd36d798fbf2e18|/data/work/ceo-bus/test_ceo_bus_dedup.py
10272|d266152868c2511291e95a02e13d1d2536240b386cb6197a8ba6a209e68c609c|/data/work/ceo-bus/REPORT-0193-GATEWAY.md
```

### 3.4 Owner final_output hashes — VERIFIED

```
worker-1789421967-b6bd71cfc5a4 1136 4535c9482ef3adfc6c71780b043d62645acea2095a15d9d8546f12b64ededbd1
worker-1789422003-9a10cb5dfeaf 1079 0aea06ba83ad4a481e86009e3ca9fdae10fb2f6d8d20e18d6637f7085882137d
worker-1789422076-9b99c1d98865   40 1ddd094f66e11f13b91a43be92c82d59f940f261e9249117651bbab2a256b192
```

C is still 40 B. C is still one bare hex string with no path. Unchanged.

### 3.5 Acceptance gap — MEASURED, and it is ABSENT

I asked whether ANY record accepts this node. Bounded byte-scan over
`gql-00` (excluding `wt/`), `ceo-bus/outbox` (665 files), `ceo-bus/tasks` (196 files):

```
== run_id 1789426305-859645918bae : 6 hits ==
  outbox/0193-progress-20260914T2247Z.md
  outbox/0193-progress-20260914T2259Z.md
  tasks/0193-coordinator-s24-20260914T2326Z.txt
  tasks/0193-relay-s22-20260914T2253Z.txt
  tasks/0193-relay-s22-receipt.json
  tasks/0193-coordinator-s23-20260914T2305Z.txt

== receipt name 0193-worker-evidence-harvest-20260914T2252Z : 5 hits ==
  outbox/0193-progress-20260914T2247Z.md
  tasks/0193-coordinator-s24-20260914T2326Z.txt
  tasks/0193-evidence-harvest-20260914T2252Z.txt
  tasks/0193-relay-s22-20260914T2253Z.txt
  tasks/0193-coordinator-s23-20260914T2305Z.txt

== "collector" : 70 hits, all prose or dispatch text ==
```

Every hit is a DISPATCH or PROGRESS record. Zero hits are an acceptance ruling.
So: `ACCEPTANCE-0193-D-collector-*.json` does NOT exist. The predecessor
coordinator never ruled on node D. My search is the only check that ran.

For contrast, the sibling rulings DO exist on disk:
`ACCEPTANCE-0193-A-bridge-s20.json`, `ACCEPTANCE-0193-B-gateway-s21.json`,
`ACCEPTANCE-0193-C-gql05-*.json`, `ACCEPTANCE-GQL-08-0193-s20.json`, and 20 more.

## 4. What I ACCEPTED

1. **The harvest itself.** The collector run completed with error null and
   `cost_is_exact` true. Its receipt is on disk, intact, 12403 B, hash-verified,
   byte-identical across both copies. I adopt it; I did not re-author it.
2. **The 10 owner-reported A and B artifact hashes.** Re-hashed on disk. All match.
3. **The three owner final_output hashes and byte counts.** Re-hashed. All match.
4. **Base commit `7b6e050e`** as the source identity. Observed, not quoted.
5. **The receipt's own C-section verdict**, which says NOT REPORTED and refuses
   to guess. That refusal is correct behaviour and I preserve it.

## 5. What I REJECTED

1. **I reject calling node 0193 D accepted.** No acceptance artifact exists for it.
   A terminal status and an intact receipt are evidence, not acceptance. The
   hard gate says a terminal status alone is not acceptance. I hold that line.
2. **I reject the earlier `jobs.json` hash as a stable fingerprint.** B reported
   8424 B / `be9bec2d...2ad3f`. I observed `9799` B / `1d89a0f0...`. An earlier
   receipt logged 8424 B / `fba9aee3...`. Three different hashes, same file name.
   The file carries `repeat.completed`, which increments on every 1-minute tick.
   So a whole-file hash of that file is the wrong fingerprint. That is a
   methodological rejection, not a claim that B lied.
3. **I reject any count of provider throttling.** The `provider_429_count` metric
   is invalid at source; its regex matches log-timestamp milliseconds.
4. **I reject any numeric ceiling from the retired 0104 clause.** Not cited, not
   used to size anything, not reported as a limit.
5. **I reject re-reading C's 40-byte hex as an artifact identity.** C named no
   path. So it maps to no file. The prior receipt already said "do not assume".
   I did not assume.

## 6. What I could NOT determine

1. **Why `jobs.json` changed again after B finished.** Bytes and hash both moved.
   I can state the mechanism (a per-minute counter) but not the exact writer. I
   did not identify the process and I will not invent one.
2. **Whether C's single 40-byte hex belongs to any deliverable.** No path was
   named. The path `/data/work/graphql-realization/gql-05/REPORT-GQL-05.md` is
   ABSENT. C's task text names it; C's final does not. Undetermined, so unmapped.
3. **Whether node 0193 D will ever be accepted.** No successor has ruled. Not mine
   to rule. Reported as owed.
4. **Real fleet throughput limits.** No genuine HTTP 429 line was observed. I
   report raw lines, not a count, because the count metric is invalid.

## 7. Exact commands, outputs and exit codes

| command | key output | exit |
|---|---|---|
| `git rev-parse HEAD` | `7b6e050e89e6aea7b51453e633fbe9209c50a844` | 0 |
| `git status --porcelain` | `?? crates/leviath-tools/0193d/` | 0 |
| `git diff --stat HEAD` | (empty) | 0 |
| `wc -c` + `sha256sum` on 10 owner paths | all match owner reports | 0 |
| `wc -c` + `sha256sum` on 3 final_outputs | all match | 0 |
| bounded scan for run id / receipt / "collector" | 6 / 5 / 70 hits, none an acceptance | 0 |
| `ls -l` on outbox receipt | 12403 B, Sep 14 22:53 | 0 |
| `wc -l` on outbox receipt | 229 | 0 |
| `grep -n '^## '` on outbox receipt | 7 sections in order | 0 |
| `grep -n` secret-shape | 1 hit, the receipt's own denial | 0 |
| `cmp` scratch vs outbox copy | byte-identical | 0 |

Verification transcripts on disk:
`crates/leviath-tools/0193d/verify-collector.txt` (3592 B) and
`crates/leviath-tools/0193d/verify-receipt.txt` (3467 B).

Hash both after this run:
`sha256sum crates/leviath-tools/0193d/verify-*.txt`

## 8. Provider spend — actual, and states kept apart

| item | value | state |
|---|---|---|
| prior collector `worker-1789426305-859645918bae` | $0.12550181600000002 | COMPLETE, exact |
| this lane `worker-1789438986-21430672570f` | see live read below | RUNNING at write time |
| owner A | $0.6534401250000005 | complete, exact |
| owner B | $0.6559529889999995 | complete, exact |
| owner C | $1.3787588309999999 | complete, exact |

States kept distinct, as the hard gate requires:
* **VERIFIED** — hashes re-read from disk by me (10 artifact rows, 3 finals).
* **BLOCKED** — nothing. No blocked state exists in this lane.
* **PASSED** — the receipt's own checks: 12403 B, 229 lines, 7 sections, copies
  byte-identical, no secret shape present.
* **UNPROVEN** — node 0193 D acceptance. No ruling artifact exists.

## 9. Gates honoured

Wrote ONLY inside `crates/leviath-tools` in my own worktree. No `Cargo.toml`, no
`Cargo.lock`. No write to `ceo-bus`, `/data/bin`, `/opt/hermes`, `/data/state`.
No config, metrics or sweeper edit. No credential or permission change.
No secret read or written. No admission file, no outbox write, no bus write.
No commit, push, deploy, restart or Railway change. No new scheduler or service.
Never `--wait`, never `--yolo`. No coordinator spawned.
Did not touch `gql-05/repo/integrations/typed-ls-ts`.
Read-only outside my declared set.

## 10. Verdict

Node 0193 D is **harvested and hash-verified, but NOT accepted**.
The deliverable exists and is intact. The acceptance ruling does not exist.
That gap is a real limit, and I report it instead of closing it myself.