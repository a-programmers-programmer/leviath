# Lane B2-0002 — node 0002 (Early Fleet operations) — evidence recovery report

* Lane id: **B2-0002**
* Directive: **0193** (P0, author Codex) + amendments
* Correlation: `fleet-sustained-throughput-20260914T1448Z` + `backlog-shape-20260915T0000Z`
* Branch: `lane/B2-0002`
* Worktree: `/data/work/graphql-realization/gql-00/wt/B2-0002`
* Base commit: `7b6e050e` (full: `7b6e050e89e6aea7b51453e633fbe9209c50a844`)
* Model path used: `openrouter/deepseek/deepseek-v4.1-flash` (only permitted path)
* Report time: 2026-09-15T04:40Z

This is a recovery job. It is not an implementation job. I wrote only inside
`/data/work/graphql-realization/gql-00/wt/B2-0002/docs/recovery/0002`.

## Bottom line

1. The **original 0002 assignment body does NOT exist** under any local tree I can read.
2. An **explicit close decision for 0002 does NOT exist** locally.
3. Only **one** of the two cited reports exists locally: `0001-progress-railway-unblocked.md`.
   The other, `0008-railway-credential-live.md`, is **absent** — zero hits.
4. The correct answer is **"parent acceptance criteria absent"**. I do not invent 0002's scope.

## Q1 — Does the original 0002 assignment body exist locally?

**No.** I found no original 0002 assignment body anywhere on the local box.

Searches and results:

| # | Command | Result | Exit |
|---|---|---|---|
| S1 | `grep -rln 'railway-credential-live' /data /root /opt /tmp` | zero hits | 0 |
| S2 | `find /data /root /opt /tmp -name "0008-railway-credential-live.md"` | zero hits | 0 |
| S3 | `ls -la /data/work/ceo-bus/inbox` | `No such file or directory` | 2 |
| S4 | `grep -rl "Early Fleet operations" /data/work/ceo-bus /data/state` | zero hits | 0 |
| S5 | `find /data -xdev -name "*0002*"` | only cache/hash noise, no 0002 body | 0 |
| S6 | `grep -rln -iE "(close\|closed\|withdraw\|supersed\|retire)" /data/work/ceo-bus --include=*.md \| xargs grep -ln "0002"` | hits are unrelated (substring `0002` inside numbers) | 0 |

The string `0002` does appear in five outbox files. Every one is a **false hit**:
the digits `0002` occur inside floats such as `1.8223442040000002` and model names
like `openai/gpt-5.5-pro`. None is an assignment body.

* `/data/work/ceo-bus/outbox/0049-gql-00-report.md:77` — `0.00002/0.00008` (pricing)
* `/data/work/ceo-bus/outbox/0186-progress-1.md:25,37` — hash `6d84ca0002eb…`, cost `0.8573669580000002`
* `/data/work/ceo-bus/outbox/0168-handoff-2.md:102` — pricing
* `/data/work/ceo-bus/outbox/fleet-sustained-throughput-20260914T1448Z-reply.md:118` — model string
* `/data/work/ceo-bus/outbox/0193-progress-20260915T0245Z.md:75` — cost `1.8223442040000002`

The only real 0002 records are the two later reports that *reference* it, plus the
backlog-shape residual rows. Those are downstream references, not the assignment.

## Q2 — Does an explicit close decision for 0002 exist locally?

**No.** I found no close, withdraw, supersede or retire decision for node 0002.

The strongest local statements only say 0002 is **"unblocked"** or **"ready"**.
They never say 0002 is closed, accepted, or withdrawn. See Q3 quotes.

The backlog-shape residual row itself says the disposition is **"Awaiting verification"**
and the action is **"Recover original assignment or explicit close decision."**
That is the state on record. No later file changes it.

## Q3 — What the two cited reports actually say

### Cited source 1 — `0001-progress-railway-unblocked.md` — **EXISTS**

* Path: `/data/work/ceo-bus/0001-progress-railway-unblocked.md`
* Lines: 59
* SHA256: `4a9f72c2d5c2db36467272fedf72f5fcbf4c70d20c846796a6c645d504174976`
* Note: the backlog-shape row cites this as a Drive link
  (`https://drive.google.com/file/d/1wJ8dY_sB38FcKEbwKetOn9ri9lFB0xLv/view`).
  A local copy exists at the path above. I did not open the link.

Verbatim lines (1-based numbering from `cat -n`):

```
 11	# PROGRESS — 0001: Railway credential found; 0002 unblocked
 33	**Effects on the queue:**
 34	- **0002 — UNBLOCKED.** All of step 1 (last 5 builds + 5 deploy logs) is readable.
 40	0002 asserts `hermes-gateway` has been build-failing and crash-looping "since Aug 24,
 41	most recently Sep 8". Observed 2026-09-11:
 47	**I am not going to modify a working control plane to satisfy a directive written
 48	from older evidence.** Either the premise is stale, or the failure is intermittent
 49	and not visible in the last deployment's status. I will confirm against the last 5
 50	builds and the last 5 deploys and report the actual timeline before proposing any
 51	change — but if 0002's premise is wrong, the directive should be withdrawn rather
 52	than executed.
```

Key point: this report says 0002 is **unblocked**, and it **questions 0002's premise**.
It does not restate 0002's acceptance criteria. It does not close 0002.

### Cited source 2 — `0008-railway-credential-live.md` — **ABSENT**

* Path searched: `/data`, `/root`, `/opt`, `/tmp` (by name and by content)
* Hits: **zero**
* The backlog-shape row cites it only as a Drive link
  (`https://drive.google.com/file/d/195ubgZGxDAxZpRs94Zu2DEAviDxL8Or3/view`).
* I did not fetch it. External access is not permitted to me.

So the only copy of `0008-railway-credential-live.md` that is known to me is a
network link. That is an honest, permitted negative. It is the expected finding.

### Other local 0001 reports that mention 0002 (supporting, not cited)

`/data/work/ceo-bus/0001-ack-bus-online.md` — 46 lines —
SHA256 `4f6cd6bae882d64cab1330c6ab02eb0646ef8879604a0cf07603e8f7c005a834`

```
 41	Because there is no Railway credential, **directives 0002 and 0005 cannot execute as
 42	written**, and rule 7 (the `CEO_DIRECTIVE` fallback channel) cannot exist.
```

`/data/work/ceo-bus/0001-progress-github-unblocked.md` — 58 lines —
SHA256 `6a0637cf17646a849ae3981b5bf98a8585ad46f39dd6e1087324471eb74feb4e`

```
 48	| 0002 | **ready** (Railway readable) — but see the stale-premise caveat below |
 55	0002's premise ("hermes-gateway build-failing and crash-looping since Aug 24") does
 56	not match reality today: latest deployment **SUCCESS** (2026-09-08) and the service is
 57	alive. **Awaiting the CEO's ruling before changing a working control plane.**
```

`/data/work/ceo-bus/state.md` — 4759 lines —
SHA256 `406614dda6c117a75b75b55dcd1300763589d2fd9645267c2b1c2630452ea203`

```
112	1. ~~0002 and 0005 blocked on a Railway credential~~ — **RESOLVED 2026-09-11.** A
115	   projects, services, deployments, buildLogs, deploymentLogs. Directive **0002 is
116	   unblocked.** Variable *writes* (needed by 0005) are still unproven.
130	## Open question for the CEO — 0002's premise looks stale
```

`/data/work/ceo-bus/capabilities.md` — 149 lines —
SHA256 `32dc47ed379de0f66ff136617d95d53381337f88b89eb34e885560b92ebee07b`

### Backlog-shape residual rows that carry the 0002 disposition

`/data/work/ceo-bus/tmp/route-20260915T0013/backlog-shape-20260915T0000Z-residuals.md`
— 691 lines — SHA256 `405186f630ad09e506c73fcc5ab4bfed31b892159981e938c45f4cde14ba38cb`

```
 56	### 0002
 57	Parent: Early Fleet operations
 58	Baseline disposition: Awaiting verification
 59	Original: Original 0002 directive is unavailable; 0008 and the 0001 progress report say the Railway credential unblocked it.
 60	Evidence: Awaiting verification
 61	Residual/action: Recover original assignment or explicit close decision.
 62	Next: Do not invent its scope or treat missing queue entry as completion.
 63	Sources:
 64	- 0008-railway-credential-live.md — https://drive.google.com/file/d/195ubgZGxDAxZpRs94Zu2DEAviDxL8Or3/view?usp=drivesdk
 65	- 0001-progress-railway-unblocked.md — https://drive.google.com/file/d/1wJ8dY_sB38FcKEbwKetOn9ri9lFB0xLv/view?usp=drivesdk
```

`/data/work/ceo-bus/tmp/tick20260915T0102/inbox/backlog-shape-20260915T0000Z-comprehensive-goals.md`
— 166 lines — SHA256 `1877acc4c6672087e76021cdb381d85ea72140461698994af574390db6fd6398`

```
 52	0002 | 0002 | https://app.notion.com/p/3dc2f9fb9d28815cadafd3516e6fdfbc | awaiting verification
```

## What exists, what does not, what I could not determine

**Exists (local, read):**
* `0001-progress-railway-unblocked.md` (cited source 1), full body, hashed above.
* `0001-ack-bus-online.md`, `0001-progress-github-unblocked.md`, `state.md`,
  `capabilities.md` — 0001-family reports that mention 0002.
* Backlog-shape residual and goals rows carrying the 0002 disposition.
* `0193-addendum-20260915T0424Z.md` — names this lane (`B2-0002`) and its worktree.

**Does not exist (local):**
* The original 0002 assignment body.
* `0008-railway-credential-live.md` (cited source 2) — zero hits.
* Any explicit close / withdraw / supersede decision for 0002.
* The original inbox folder `/data/work/ceo-bus/inbox` — absent.
* Original inbox 0001–0005 — not present in the accessible folder.

**Could not determine:**
* 0002's actual scope and acceptance criteria. The parent criteria are absent.
* Whether `0008-railway-credential-live.md` says anything beyond the credential
  event. Its only known copy is a network link I must not open.

## Plain statement of limits

* Parent acceptance criteria for 0002 are **absent**. That is the answer.
* The Railway credential becoming live is an **event**, not acceptance of 0002.
* A missing queue entry is **not** completion of 0002.
* I invented no scope. I made no Railway call. I read no credential.
* I wrote nothing outside `/data/work/graphql-realization/gql-00/wt/B2-0002/docs/recovery/0002`.

## Residual / action for the owner

Recover the original 0002 assignment body from an external archive, or record an
explicit close decision. Until then, node 0002 stays **Awaiting verification**.

## Files written by this lane

* `/data/work/graphql-realization/gql-00/wt/B2-0002/docs/recovery/0002/report.md` (this file)
* `/data/work/graphql-realization/gql-00/wt/B2-0002/docs/recovery/0002/searchlog.txt`
