# Lane L12-gc300cloud — node gc300:P1-CLOUD — harvest + ruling

Written by: worker-1789438986-46e5421296c0
Model: openrouter/deepseek/deepseek-v4.1-flash (only permitted path)
Worktree: /data/work/graphql-realization/gql-00/wt/L12-gc300cloud
Declared write set: crates/leviath-sys (this file is inside it)

## 0. What I did

I harvested the saved P1-CLOUD result on disk first. I did not re-author or
re-run it. I then ruled the saved result against the original criteria.
I wrote no file outside my declared set. I changed no code.

## 1. Source identity

- base commit: `7b6e050e89e6aea7b51453e633fbe9209c50a844`
- branch: `lane/L12-gc300cloud`
- worktree clean at harvest: `git status --porcelain` -> empty
- my diff against base: none (no code change was needed to rule)
- saved result workdir: `/data/work/gc300-20260912T223500Z` (NOT in my write set)

## 2. Saved artifacts observed (hashes)

Command: `sha256sum cloud-preflight.json budget-ledger.json P1-CLOUD-notes.md HANDOFF.md tmp/fix_validate.py`

```
7689875c308e9c7f870ad61604770ece7a7cd90c1844ec1b48d94d88dca7c4f7  cloud-preflight.json   21943 B
7fc9c84d9a5b5bca7b80ccc53e9ccb54f4bcad3a565838d2a8879d3b8b67892a  budget-ledger.json      5334 B
9fbf73cde16faef275faa62abfd13940187f27c467582d3340af43aa21b97656  P1-CLOUD-notes.md       6181 B
5022dad808fc7c1c9804cfc6d23418fccc22324ea642f38bb95f4c3a91bb3051  HANDOFF.md              2306 B
473905e0331729747a661628e982b4aab9c9d65e18a7769c40f7cc25f81a5c70  tmp/fix_validate.py     5965 B
```

Raw API evidence kept by the prior run (I read, I did not write):

```
d6e9af9a9210fb99fea8470dc394761daf2fdb7dceae8a6e74ecbfacb49fa956  tmp/lf_instance-types.json  7741 B
0e67a643c152d6568c10b21764bb128950c9a0ca8453025af8562206ddc36457  tmp/lf_instances.json         12 B
b8d3b4931766ebece41e0cdee65edd95f607825abb755a12f759d6dca981dbc7  tmp/lf_ssh-keys.json         235 B
3ce6e092f7361f90f028fe802b57dd15b75b8baeeaa4acbfe98dd892cc3ba063  tmp/lf_ae.json              4594 B
98a2110a3a25d1c3c1f55647058d831cfcd64c1301ef44e44e83679ae90a73b9  tmp/q_limits.out             274 B
e52c0f31f3ba55c2f0d50ab27ac7fbd9a672805840353fcfa77e01f5ba5973ea  tmp/q_quota.out              273 B
19dad108f64a8706c626b805c9fe08e4c039322d874d021cbfb2726e85028a76  tmp/q_account.out            275 B
```

## 3. Independent re-run of the saved validator

Command: `cd /data/work/gc300-20260912T223500Z && python3 tmp/fix_validate.py; echo RC=$?`

Exit code: `RC=1`. Last line: `VALIDATION: FAIL`.

The saved notes claim `RC=0, "VALIDATION: PASS"`. That claim does not
reproduce against the ledger now on disk. The FAIL is real, not mine.

## 4. What I ACCEPTED

1. **Read-only access proof.** Auth `GET /instances` -> HTTP 200 `{"data": []}`.
   No-header control -> 401. Wrong-key control -> 401. The 200 is a real
   authenticated read. I accept this.
2. **No secret leak.** I scanned all three deliverables for the full key
   sha256 and for token runs of 60+ chars. Hits: 0 full, 0 long runs.
   Only path, byte length (86), mode (600) and a hash prefix were stored.
   I accept this.
3. **Prices.** I cross-checked all 24 price rows against the stored raw
   payload `tmp/lf_instance-types.json`. Mismatches: 0 of 24. Plan SKU
   `gpu_1x_a6000` = 109 cents/hour = $1.09/GPU-hour. I accept this.
4. **Capacity.** `gpu_1x_a6000` `regions_with_capacity_available` = `[]`.
   The plan SKU has no capacity today. I accept this as observed.
5. **Running instances = 0.** Body `{"data": []}`. I accept this.
6. **Defect fix is present.** Builder `capacity.cores`/`memory_gib` are null
   with a reason. `toolchain.cargo`/`rustc` are null with a reason. The
   health body is refreshed and holds no cores/memory/cargo/rustc/nproc.
   The `tickets` probe is present. I accept the fix.
7. **Quota null-with-reason.** `/limits`, `/quota`, `/account` -> 404.
   Quota and balance are unobservable. That is a real limit, correctly
   recorded as null with a reason. I accept it. I do not invent a value.

## 5. What I REJECTED

1. **The ledger is not internally consistent.** Command:
   `python3` sum of `reservations[].worst_case_usd` = **22.8**.
   But `reserved_usd` = **18.8**. Delta = **4.0**. The saved validator
   fails on exactly this. So the ledger cannot be accepted as verified.
2. **The ledger total does not close.** `spent + reserved + remaining`
   = `1.9102 + 18.8 + 279.29` = **300.0002**, not 300.0. Delta = 0.0002.
   The validator fails on this too.
3. **`spent_usd` contradicts the ledger's own 0110 note.**
   `spent_usd` = 1.9102 = P0 (0.1744) + P1 (0.7027) + 0110 (1.0331).
   But the ledger field `notes_0110_separate` says the 0110 run cost
   $1.0331 is charged to directive 0110, **not** to this batch.
   Both cannot be true. Batch spend is 0.8771 if the note governs,
   1.9102 if the field governs.
4. **The "VALIDATION: PASS" claim in the notes.** It does not reproduce.
   I reject it as an acceptance basis.

## 6. What I could NOT determine

1. **True batch provider spend.** The ledger's `spent_usd` is contradicted
   by its own 0110 note (section 5.3). I record both readings and rule
   neither. This needs the coordinator to state which field is authoritative.
2. **Quota headroom and account balance.** No endpoint exists (404s).
   Unobservable by API. A real limit, not an invented one.
3. **Whether the 4.0 reservation gap is a typo or a missing row.**
   The gap is exactly the B0-MEASURE model bound (4.0). It looks like a
   `reserved_usd` update that did not follow a reservation change. I cannot
   prove intent from disk alone.

## 6b. Exact reconciliation (added on the `execute` pass)

I re-derived the ledger split by reservation `state`. This pins the defect
to one line, so the coordinator's fix is mechanical, not a guess.

```
sum ALL reservations      22.8
sum SETTLED (state=settled) 4.0   <- P0-RUNTIME 2.0 + P1-CLOUD 2.0
sum UNSETTLED (open)       18.8
```

- `reserved_usd` = 18.8 equals the **open** sum exactly. So `reserved_usd`
  is correct **if** it means "open commitments only". It is not wrong.
- The saved validator sums **all** rows (22.8) and compares to
  `reserved_usd` (18.8). That comparison is the FAIL. The mismatch is a
  **schema ambiguity**: the validator assumes `reserved` covers all rows,
  the ledger writes `reserved` as open-only. One of the two is wrong; the
  arithmetic of neither is.
- `remaining = ceiling - spent - reserved` = 300 - 1.9102 - 18.8 =
  **279.2898**. The ledger stores **279.29**. Delta **0.0002** is pure
  rounding, not a missing cent.
- `spent_usd` = 1.9102 = P0 (0.1744) + P1 (0.7027) + 0110 (1.0331).
  The **settled actual** sum is 0.8771. So `spent_usd` carries the 0110
  run that `notes_0110_separate` says is not this batch. This is the one
  real content defect.

### Minimal patch (proposed, NOT applied — outside my write set)

Either of these closes the gate; the coordinator picks one:

1. **If `reserved` means open-only:** change the validator to sum
   `state != 'settled'` rows, and round `remaining` to 2 dp. Ledger data
   stays; `spent_usd` still needs the 0110 decision below.
2. **If `reserved` means all rows:** set `reserved_usd` = 22.8 and
   `remaining` = 300 - 1.9102 - 22.8 = 275.2898. Then the validator passes.
3. **Independently:** decide whether `spent_usd` is 1.9102 (field governs)
   or 0.8771 (0110 note governs), and make the field match the ruling.

I did not apply any of these. `/data/work/gc300-20260912T223500Z/` is
outside my declared write set, and I proved a write there is rejected.

## 7. State ruling (blocked / passed / verified)

| item | state |
|---|---|
| P1-CLOUD read-only inventory | PASSED (evidence consistent) |
| secret-leak scan | PASSED (0 hits) |
| price cross-check 24/24 | PASSED (0 mismatches) |
| builder defect fix | PASSED (null-with-reason present) |
| budget-ledger.json arithmetic | BLOCKED (fails own validator) |
| node gc300:P1-CLOUD acceptance | **NOT VERIFIED** |

A terminal status of `complete` is NOT acceptance. The saved run's own
gate returns FAIL. So this node stays `awaiting verification`.

## 8. Provider spend (this lane)

- my run: `worker-1789438986-46e5421296c0`, model
  `openrouter/deepseek/deepseek-v4.1-flash`, cost `0.15171342`, exact `true`.
  Read while the run was still active, so this is a partial figure.
- prior P0-RUNTIME: `worker-1789256008-b016fc483a2d`, cost `0.17444096399999998`, exact `true`.
- prior P1-CLOUD: `worker-1789256008-e82d7d72708f`, cost `0.7026707289999996`, exact `true`.
- 0110 packaging: `worker-1789256068-3d8cea3dcdd6`, cost `1.0330984959999996` — ledger says NOT this batch.

No provider-throttling log line was observed. I report no count.

## 9. Commands run (exact, with exit codes)

```
sha256sum cloud-preflight.json budget-ledger.json P1-CLOUD-notes.md HANDOFF.md tmp/fix_validate.py   RC=0
python3 tmp/fix_validate.py                                                                          RC=1  (VALIDATION: FAIL)
python3 <sum reservations>                                                                           RC=0  (sum 22.8 vs reserved 18.8)
python3 <cross-check 24 prices vs tmp/lf_instance-types.json>                                        RC=0  (0 mismatches)
git rev-parse HEAD / status --porcelain / log -3 / rev-parse --abbrev-ref HEAD                        RC=0  (clean, 7b6e050e)
python3 <read run meta cost for 3 run ids>                                                           RC=0
```

## 10. Report back

- ACCEPTED: access proof, no secret leak, 24/24 prices, capacity, 0 running
  instances, builder defect fix, quota null-with-reason.
- REJECTED: ledger arithmetic (22.8 vs 18.8; 300.0002 vs 300.0), the
  `spent_usd` / 0110-note contradiction, and the notes' PASS claim.
- COULD NOT DETERMINE: true batch spend, quota/balance, and whether the
  4.0 gap is a typo or a missing row.

A real limit is a valid answer. The quota 404 is such a limit. The ledger
gap is a real defect, not a limit I invented.
