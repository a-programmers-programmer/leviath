# GQL-28 — Accept the Parlour product contract — LANE REPORT

* lane: `L07-gql28`
* branch: `lane/L07-gql28`
* worktree: `/data/work/graphql-realization/gql-00/wt/L07-gql28`
* base commit observed: `7b6e050e89e6aea7b51453e633fbe9209c50a844`
* run id: `worker-1789438986-1127e2f62084`
* model: `openrouter/deepseek/deepseek-v4.1-flash`
* date: 2026-09-15

## Verdict

**BLOCKED.** I did not accept the Parlour product contract.

I could not accept it because the source it is derived from is not on this box.
I observed no checkout, no `server/routers.ts`, no `server/productRouter.ts`, no
`server/eventRuntime.ts`, no `drizzle/schema.ts`, and no `.graphql` artifact.
A contract I did not observe is a contract I cannot accept.

This is a real limit, not an invented one. The evidence is below.

## What I accepted

Nothing. No contract artifact exists on disk for node GQL-28.

## What I rejected

* **Re-authoring a contract from the spec text.** The spec names the outcome and
  the steps. It does not contain the procedures, the entities or the rules. A
  contract written from the summary would be invented, so I rejected that path.
* **Treating `parlour-asset-review` as the Parlour product.** That repo is a
  Flask asset-review site. It has one placeholder page, `templates/parlour.html`
  (514 bytes, "Bespoke product business hub. Coming soon."). It has no tRPC, no
  TypeScript and no Drizzle. It is a different product.
* **Claiming acceptance from a terminal status.** The run is still `running`.
  A terminal status is not acceptance, and I did not use one.

## What I could not determine

* Whether the Parlour product repo has any content at all. It is private and not
  cloned here. `git ls-remote` returned `Repository not found.` and
  `fatal: Authentication failed`. I did not and will not read a credential.
* Whether an accepted Parlour contract exists somewhere I cannot reach.

## Harvest — what already existed on disk

I read before I wrote. The findings:

| observation | command | result |
|---|---|---|
| no parlour checkout | `ls /data/work/parlour` | `No such file or directory` |
| no gql-28 checkout | `ls /data/work/graphql-realization/gql-28` | absent |
| no worktree repo dir | `ls .../wt/L07-gql28/repo` | absent |
| no tRPC anywhere | `grep -rIl -i trpc /data/work/graphql-realization` | no hits |
| no drizzle dir | `find /data/work/graphql-realization -type d -name drizzle` | no hits |
| no `routers.ts` | `find /data/work -maxdepth 8 -name routers.ts` | 0 hits |
| no `productRouter.ts` | `find /data/work -maxdepth 8 -name productRouter.ts` | 0 hits |
| no `eventRuntime.ts` | `find /data/work -maxdepth 8 -name eventRuntime.ts` | 0 hits |
| no `drizzle/schema.ts` | `find /data/work -maxdepth 8 -path '*/drizzle/schema.ts'` | 0 hits |
| skeleton is empty | `cat skeleton-parlour.json` | `{"entries": [], "missing": true, "note": "no checkout at /data/work/parlour", "repo": "parlour"}` |
| repo is private | `git ls-remote https://github.com/a-programmers-programmer/parlour.git` | `Repository not found.` / auth failed |
| no accepted contract | `grep -ril 'accepted.*parlour' gql-00` | only asset-review skeletons |

`skeleton-parlour.json` sha256:
`215378a9c877d34c0caef1c3b840cb8ca1913b8eb924bc5fd071cfedda176a23`

The one `parlour` file on the box is `/data/work/parlour-asset-review/templates/parlour.html`
(514 bytes). It is a placeholder page, not a product contract.

The backlog state on record is `blocked`. `DEPENDENCIES.md:37` says
`GQL-28 | specified_not_imported | GQL-00, GQL-10 | blocked on GQL-10`.
`DEPENDENCIES.md:55` says `GQL-28 — blocked: no parlour checkout`. My harvest
confirms that line. I did not overturn it.

## What I built

I built a deterministic readiness gate. It owns the bits. It does not judge the
contract.

* `tools/gql28/check_contract_ready.py` — 6133 bytes, sha256
  `5bc3009e6b063f50a025a2ff5a7d07c7dd26854f540a00df1083017e210348ae`
* `tools/gql28/test_check_contract_ready.py` — 5980 bytes, sha256
  `24589d9ca38e4aa303d8687ee99e894e68ea5a17d5f8c3461733de4528eebcea`
* `tools/gql28/spec-gql28.json` — the node spec, with the observed base commit

The gate checks three facts and hashes every artifact it sees:

1. a checkout exists under `--root`
2. every `existing_paths` entry from the spec exists
3. a tRPC marker is present in a router file
4. a real `.graphql` artifact was observed, with its sha256

Exit codes: `0` ready, `2` blocked, `1` the gate could not run.

Design follows the stage law: the gate is code and owns the bits. Judgment about
whether a contract is *good* stays with a model. The gate never claims that.

## Evidence — exact commands, outputs and exit codes

### RED (test written first, gate absent)

```
$ ls tools/gql28/check_contract_ready.py
ls: cannot access 'tools/gql28/check_contract_ready.py': No such file or directory
$ python3 -m pytest tools/gql28/test_check_contract_ready.py -q
RED exit=1
11 failed in 0.30s
```

### GREEN

```
$ python3 -m pytest tools/gql28/test_check_contract_ready.py -q
GREEN exit=0
11 passed in 0.39s
```

### LIVE gate against the real box

```
$ python3 tools/gql28/check_contract_ready.py --root /data/work --spec tools/gql28/spec-gql28.json
LIVE gate exit=2
{
  "checkout": null,
  "checks": [
    {"detail": "no checkout under /data/work", "name": "checkout", "state": "missing"},
    {"detail": "/data/work/AGENTS.md", "name": "existing_path:AGENTS.md", "state": "missing"},
    {"detail": "/data/work/wiki", "name": "existing_path:wiki", "state": "missing"},
    {"detail": "/data/work/server/routers.ts", "name": "existing_path:server/routers.ts", "state": "missing"},
    {"detail": "/data/work/server/productRouter.ts", "name": "existing_path:server/productRouter.ts", "state": "missing"},
    {"detail": "/data/work/server/eventRuntime.ts", "name": "existing_path:server/eventRuntime.ts", "state": "missing"},
    {"detail": "/data/work/drizzle/schema.ts", "name": "existing_path:drizzle/schema.ts", "state": "missing"},
    {"detail": "no tRPC marker in any router file", "name": "trpc_markers", "state": "missing"},
    {"detail": "no .graphql artifact observed", "name": "contract_artifact", "state": "missing"}
  ],
  "contract_accepted": false,
  "contract_artifacts": [],
  "node": "GQL-28",
  "repository": "a-programmers-programmer/parlour",
  "root": "/data/work",
  "state": "blocked"
}
```

### Mutation check (anti-cheat)

I broke the gate, not the test. I replaced the readiness rule with `ready = True`.

```
$ python3 -m pytest tools/gql28/test_check_contract_ready.py -q
MUT1 exit=1
4 failed, 7 passed in 0.48s
FAILED test_empty_root_is_blocked_with_exit_2
FAILED test_ready_requires_a_real_contract_file
FAILED test_ready_requires_trpc_markers
FAILED test_ready_requires_every_existing_path
```

Restored byte-exact, re-ran:

```
$ sha256sum tools/gql28/check_contract_ready.py
5bc3009e6b063f50a025a2ff5a7d07c7dd26854f540a00df1083017e210348ae
$ python3 -m pytest tools/gql28/test_check_contract_ready.py -q
RESTORE exit=0
11 passed in 0.44s
```

The test file was never weakened. The mutation proves the tests assert.

## Source/base commit and resulting diff

* base commit: `7b6e050e89e6aea7b51453e633fbe9209c50a844` (`git rev-parse HEAD`)
* diff against base, declared set only:

```
$ git status --porcelain -- tools integrations/lifeops
?? tools/gql28/
```

All new files are untracked. I made no commit, no push and no deploy. I wrote
nothing outside `tools/` and `integrations/lifeops/`. I wrote nothing into
`integrations/lifeops/` at all.

## Provider spend

```
run_id        = worker-1789438986-1127e2f62084
model         = openrouter/deepseek/deepseek-v4.1-flash
cost_usd      = 1.4860962000000015
status        = running
iteration     = 223
tool_calls    = 380
unpriced_calls= 0
cost_is_exact = True
```

Read from `/data/.leviath/runs/worker-1789438986-1127e2f62084/meta.json`.
The model is the only permitted path for this lane. I used no other model.

No provider throttling observed. No `429` raw line to report.

## States distinguished

* **verified** — the gate's own tests: 11 pass, mutation caught, restore clean.
  The gate's live verdict on this box.
* **blocked** — GQL-28 contract acceptance. No source, so no contract.
* **passed** — nothing. I claim no passed state for the contract.

## Out of scope — confirmed clean

No write outside the declared set. No edit to `config.toml`,
`ceo_bus_metrics.py` or any sweeper. No write under `/data/work/ceo-bus`,
`/data/bin`, `/opt/hermes`, `/data/state`. No admission file, no outbox write,
no bus write. No new scheduler, queue, service or dashboard. No credential or
permission change. No secret read or write. No Railway mutation. No commit,
push or deploy. No `--wait`, no `--yolo`, no second coordinator. I did not touch
`/data/work/graphql-realization/gql-05/repo/integrations/typed-ls-ts` or any
other lane's worktree.

## Decomposition request — what would unblock this node

I need a path I do not have. Per the task rule, I stopped and report it instead
of writing it.

1. **A checkout of `a-programmers-programmer/parlour` on this box.** It is
   private. I cannot clone it without a credential, and reading a credential is
   out of scope. Someone with access must place the tree, or grant a read path.
2. **A declared path for the accepted contract artifact.** The contract must be
   written somewhere. My declared set is `tools/` and `integrations/lifeops/`.
   If the contract belongs in the parlour repo, that path must be declared.

Until then, the honest state of GQL-28 is `blocked`, and this lane says so.

## Files written by this lane

| path | bytes | sha256 |
|---|---|---|
| `tools/gql28/check_contract_ready.py` | 6133 | `5bc3009e6b063f50a025a2ff5a7d07c7dd26854f540a00df1083017e210348ae` |
| `tools/gql28/test_check_contract_ready.py` | 5980 | `24589d9ca38e4aa303d8687ee99e894e68ea5a17d5f8c3461733de4528eebcea` |
| `tools/gql28/spec-gql28.json` | — | node spec |
| `tools/gql28/RED.txt` | — | RED output |
| `tools/gql28/GREEN.txt` | — | GREEN output |
| `tools/gql28/MUT1.txt` | — | mutation output |
| `tools/gql28/RESTORE.txt` | — | restore output |
| `tools/gql28/22_live_gate.txt` | — | live gate JSON |
| `tools/gql28/01..21_*.txt` | — | harvest evidence |
