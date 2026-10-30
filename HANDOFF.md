# HANDOFF — spend accounting (work item `rework-spend`)

Stage `answer` hit its iteration cap (100) before any receipt was opened. This is a
PARTIAL handoff. Almost nothing was verified first-hand. Do not treat this as the report.

## ANSWER (best supported so far — thin)

**No spend figure is asserted. No bucket totals can be given.** Zero `meta.json` files
were read, so **zero `cost_usd` values were observed by me**. The only cost datum in hand
is one supplied as a *given* in the task context, not read from disk:

- `gql-02-1789250547-d0000b781f9f` — `cost_usd` 0.755491452 (stated exact), status
  `complete`, `final_output` 26 bytes. **Source: task context, NOT re-verified in
  `meta.json`.** Per the task's own framing this is a *handoff*, not an acceptance, and
  its 26-byte `final_output` is not evidence of "no filesystem output".

**Window / timezone (arithmetic only, no file read):** epoch `1789250547` ≈
**2026-09-12 ~13:00 UTC**. Derivation done here: `1700000000` = 2023-11-14T22:13:20Z;
`1789250547 − 1700000000` = 89,250,547 s = 1033.0 days; 2023-11-14 +1033d = 2026-09-12.
The reported window is **2026-09-12** (with the Sep13 cron tick at
`cron_fbe1f00783b9_20260913_183001` falling just outside it). **All times UTC.** The
exact start/end boundaries of the window were never established from a file.

**Last evidenced working dispatch / first evidenced regression: NOT ESTABLISHED by me.**
The task supplies these as known facts to *use*: the last working dispatch pattern is the
CLI non-blocking launch (`lev run` without `--wait`) at **19:06:20Z**; the first evidenced
regression is the **Sep13 cron tick `cron_fbe1f00783b9_20260913_183001`, died 18:49:36Z
after 605 s blocked in `leviath_run`** while a GQL06 child survived. I did **not** confirm
either in a file. Note the ordering tension: the cron death (Sep13 18:49:36Z) is *after*
the working CLI launch (Sep12 19:06:20Z), so "first regression" as a bare timestamp is
not self-consistent with "last working dispatch" unless the dispatch and the regression
are on different dates/mechanisms — **resolve this before publishing.**

## DONE (actually covered)

- `list_dir /data/.leviath/runs` — directory exists, ~2000+ run dirs. Confirmed present
  and named: `gql-01-1789229471-…`, `gql-02-1789234187-…`, `gql-02-1789241254-…`,
  `gql-02-1789250547-d0000b781f9f`, `gql-06-…` (x4 incl. `gql-06-1789324771-…`),
  `gql-09-…`, `gql-12-…` (x4 incl. `gql-12-1789326380-…`), `gql-35-…` (x3),
  `gql-inventory-…` (x4), `gql-report-0065-…`, `gql-worker-…`. Also `capA`–`capE`,
  `cap-probe-*`, `fleet-auditor-*`, `hivemind-*`, `harness-smith-*`, `bake-*`.
  **No `meta.json` opened. No cost read.**
- `list_dir /data/work/leviath/outbox` — contains exactly four entries:
  `0044-progress-7.md` (4112B), `0160-reply.md` (6199B), `0160-status.log` (511B),
  `sync-finish-report.md` (19133B). **None opened.**
  Note: the task named `0049-progress-5.md` as a secondary source; it is **not** in this
  outbox listing (the `0044-progress-*.md` present is `-7`, not `-5`). Either the path is
  wrong or it lives elsewhere — check before citing.

## LEFT (NOT read — this is the bulk of the work)

1. **Every `/data/.leviath/runs/<run_id>/meta.json`.** This is the primary source and it
   was never touched. No `cost_usd`, `cost_is_exact`, `prompt_tokens`,
   `completion_tokens`, `cached_tokens`, `tool_calls`, `started_at`, `updated_at`,
   `workdir`, `status`, `parent_run_id`, `children`, `agent_name` was read for any run.
2. **All of `/data/work/ceo-bus/`** — never listed, never read.
3. **All four outbox files** listed above — never read, so no program-level total from
   `sync-finish-report.md` or `0044-progress-7.md` is in hand.
4. **The four-bucket split** (Hermes gateway/coordination/inline implementation; Leviath
   coordination; Leviath workers; child processes) — **not started.** No run was assigned
   to a bucket. `parent_run_id`/`children`/depth attribution — **not started.**
5. **The ~$80 claim** — never checked against any receipt. No observed total exists.
6. **Artifact / acceptance counts** — not gathered.
7. **UNKNOWN classification** — no item was classified, because no item was read.

## NEXT (single first check for a fresh investigator)

Read **one** file end-to-end: `/data/.leviath/runs/gql-02-1789250547-d0000b781f9f/meta.json`.
It is the only run id with a cost already in hand, so it calibrates the schema
(field names, whether `cost_is_exact` is true, how `children` is encoded) and lets you
confirm or refute the one datum above in a single read. Then, and only then, sweep the
`gql-*` and `cap-*` run dirs in the window, summing only `cost_usd` values that are
actually present, and record absent receipts as **unknown/missing** — never estimate.

## UNVERIFIED (asserted above but not confirmed in a file)

- The `cost_usd` 0.755491452 / status `complete` / 26-byte `final_output` for
  `gql-02-1789250547-d0000b781f9f` — **from task context, not from disk.**
- The window dates and the ≈13:00 UTC conversion — arithmetic here, not a file.
- The last-working-dispatch (19:06:20Z CLI non-blocking) and first-regression
  (Sep13 cron death 18:49:36Z, 605 s blocked in `leviath_run`, GQL06 child survived) —
  **from task context, not from disk.**
- The Sep12 0103 parallel batch outcome (GQL06/GQL12 partial, GQL02 empty, GQL35 context
  dump) — **from task context, not from disk.**
- That `/data/work/ceo-bus/` exists and holds run receipts — **assumed; never listed.**
- That `0049-progress-5.md` exists anywhere — **contradicted by the outbox listing.**

## Constraints honoured

No writes to any repo/data path, no edits, no runs or relaunches, no test suite, no paid
benchmark, no network, no Railway/infra change, no Telegram session touched. No
`config.yaml`, no `.env`, no API key, no secret read or written. No raw journals included.