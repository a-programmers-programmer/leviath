# REPORT — GQL-10: Join proposal/merge work to the deterministic acceptance loop

- Work order: `T01M2KX1Z7YEK76464Y9VPRMPWW` (GQL-10)
- Run id: `R01M2MCP7STSB120JBAFDDV0DNC` (lev run `T01M2KX1Z7YEK76464Y9VPRMPWW-a1`)
- Repository: `a-programmers-programmer/leviath`, checkout under `/data/work`, copy at `repo/`
- Branch: `fleet/gql-10` (local commit `b1e67845`, no push, no merge)
- Base commit: `15dc182e0bc6c28dfa93f4b8dd3c423372808f61`
- Owned path: `crates/leviath-cli/agents/graphql-schema` (one writer)
- Prior work resumed from: `/data/work/graphql-realization/gql-10/` (HANDOFF-GQL-10.md,
  REPORT-GQL-10.md, commands.log, evidence/). Those files are copied into `evidence/`.
- Bead ID: **null** (no bead existed for this task; honest null, not invented)
- Provider spend: **0.00000000 USD** — no lane dispatched, no paid probe, no provider call

## What I did

I resumed the GQL-10 handoff instead of redoing it. The old run had already written the
join module and its tests but had neither committed them nor produced the work-order
report in this new workdir. I then:

1. Copied the four owned changes and the prior evidence into this workdir.
2. Ran the live join against the REAL GQL-09 merge identity — exit 0, `all_pass: true`.
3. Proved the reused gate is byte-identical to base, so acceptance is unchanged.
4. Committed the four owned paths on branch `fleet/gql-10` (local only).
5. Ran the full 43-test suite (24 existing + 19 new) — exit 0.

## What was built (deterministic code, not a reviewer, not an approver)

`scripts/join_candidates.py` joins the reconciled GQL-09 merge result to the **existing**
contract gate and review route. It:

1. verifies source identity (candidate paths and hashes, decision document, result
   hashes, lane run ids; a repeated run id is rejected as self-merge);
2. installs the reconciled SDL as the contract schema under test;
3. checks that the merged schema adds no obligation the contract omits;
4. feeds the candidate into the existing gate (`scripts/gate.py`, unchanged) and the
   existing independent review route;
5. records checker version, guide version, source identity and review evidence;
6. keeps two repair passes and an explicit terminal failure route.

Integration edits touch only two existing files:

- `agent.leviath`: **one added** pinned context region `repairs` (256 tokens). No tool,
  permission, credential, resource-limit or iteration change. The review stage keeps its
  cleared context and narrow tool set.
- `hooks/protocol.rhai`: counts each `revise` entry into the pinned `repairs` region, and
  on stage exit rewrites `route = "revise"` to `route = "blocked"` once `used >= 2`.

No new verifier, framework, queue or blueprint was added.

## Definition of done, each line shown by a real command

| Done-means line | Evidence | Exit |
|---|---|---|
| Existing 24 regressions pass | `unittest discover -p 'test_gate.py'` → Ran 24 tests, OK | 0 |
| Stale reviews BLOCK | `test_stale_review_blocks_and_leaves_no_receipt`; live scenario 4 → blocked | 0 |
| Omitted obligations BLOCK | `test_omitted_obligation_blocks`; live scenario 5 → blocked | 0 |
| Exhausted repair BLOCKS | `test_exhausted_repair_blocks_terminally`; live scenario 6 → route `terminal` | 0 |
| Proposal/merge success CANNOT mint accepted.json | `test_merge_success_alone_never_mints_accepted_json`; live scenario 3 → blocked, no receipt | 0 |
| Runtime verification and product judgment stay SEPARATE | `test_runtime_and_product_judgment_stay_separate_from_acceptance`; live output `runtime_verification.state="unverified"`, `product_judgment.state="not_made"`, each `separate_from_acceptance: true` | 0 |

Full suite: **43 tests OK** (24 existing + 19 new), exit 0.
Live join (`live_join.py`) against the real GQL-09 accepted merge identity: `all_pass: true`,
exit 0.

## Reused gates preserved (not bypassed)

- The existing full contract gate (`scripts/gate.py`) is called exactly once per stage
  through the unchanged `graphql_contract_gate` tool. `test_join_reuses_the_existing_gate`
  spies on `gate.dispatch` and asserts op `check`, the exact root, and that the returned
  fingerprint equals the gate's own fingerprint for the same root.
- `scripts/gate.py` is byte-identical to base
  (sha256 `554c3f7e66e1bfed7c808cbf16a92cff28e7ebac0157ea44e85845be83f8ee39`) and remains
  the only writer of `accepted.json`.
- `tests/test_gate.py` is byte-identical to base: the 24 existing regressions were not
  weakened. The join module never writes `accepted.json`.
- Two repair passes and the terminal route are preserved and asserted.

## Source identity recorded (real, from GQL-09)

- candidate A `/data/work/graphql-realization/gql-09/live/cands/cand_a.graphql`
  sha256 `252b15726fb749481f60d76278c62aec20a2500d716e49bf21df28a4d6a0d3b3`
- candidate B `…/live/cands/cand_b.graphql`
  sha256 `e0f1084e4e763a2235328f65b33c7f42ef21bb6ad10f367d157232f3f0bf880f`
- decisions `…/live/work/v1_decisions_lane0.json`
  sha256 `31fffd6be956a6d9c60f8f025cc07ae19057f000aebd63ebc9dad7ae43b64363`
- merged `…/live/work/v1_merged_lane0.graphql`
  sha256 `ba4985f621e27dba0c98b4465729e3bb6c83297790b47f1fda8c7189b75edfc9`
- lane run ids `bake-merge-1789376732-976109904b31`, `bake-merge-1789376732-661590885a9e`
  (distinct → independent convergence claimed only for distinct run ids)
- Checker: `gate.VERSION` = 1, `scripts/gate.py` sha256 `554c3f7e…f8ee39`
- Guide: version `v1`, `README.md` sha256 `e02ba304…c3512`,
  tool `tools/graphql_contract_guide.rhai` sha256 `00d981f1…01a240`

## Honest limitations

- The real reconciled GQL-09 merged SDL is a 2-type fragment (Thing/Node), not the
  task-board contract. The live harness pairs the REAL GQL-09 candidate/decision/lane
  identity with the example task-board contract policy map so the gate's own obligations
  are satisfiable. The join logic and gate run on real identity, not on a real end-to-end
  24-field merge of that fragment. Scenario 2's receipt is minted by a synthetic harness
  review fixture, not by a model reviewer; it proves the gate route, not review quality.
- No Leviath review lane was dispatched for GQL-10 (spend 0). The run id above is the
  worker run that performed this work, not a review lane.
- Runtime verification and Josh's product/rollout judgment remain separate and
  unperformed — contract acceptance does not cover them.
- No Bead was created (bead id null, honest).

## Rollback

Revert the four owned paths only:
`git checkout 15dc182e -- crates/leviath-cli/agents/graphql-schema/agent.leviath
crates/leviath-cli/agents/graphql-schema/hooks/protocol.rhai` and delete
`scripts/join_candidates.py` and `tests/test_join.py`, or reset the one local commit
`b1e67845`. No data migration, no store change, no cutover, no push.
