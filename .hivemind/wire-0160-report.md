# wire-0160 — existing coordination mechanism restored/wired

- **Work item:** wire-0160 (role: ops)
- **Workdir:** `/data/work/leviath` — branch `sync/upstream-0.6.0`, HEAD `15dc182e` (unmoved)
- **Status:** DONE — nonblocking dispatch demonstrated end to end with existing primitives only.

## (a) `which bd` — plainly

**ABSENT.** `which bd` exits **1**; `bd --version` → `bd: command not found`.

This is a **FINDING, not a blocker**. The beads CLI is not on PATH, so the
bead-claim step could not run. I fell back to the other two named primitives —
`spawn_agent`/`check_agent` semantics (via `lev run` + `lev ps`) and the run
store under `/data/.leviath/runs` — exactly as the goal directs. No bead was
claimed, because no `bd` exists to claim one with.

## (b) Nonblocking dispatch — exact command + real run id

The dispatch **was** made nonblocking: `lev run` without `--wait` hands the run
to the daemon and returns at once.

```
$ lev run investigator --workdir /data/work/leviath \
    --task .hivemind/wire-0160-worker-task.txt --json
{
  "run_id": "investigator-1789339768-6f33646874c1",
  "blueprint_path": "/data/.leviath/agents/investigator/agent.leviath",
  "workdir": "/data/work/leviath",
  "yolo": false
}
```

Real run id: **`investigator-1789339768-6f33646874c1`**. Returned immediately —
the shell prompt came back while the run was still starting.

Second dispatch (used for the mid-flight check in (d)):

```
$ lev run investigator --workdir /data/work/leviath \
    --task .hivemind/wire-0160-worker-task-2.txt --json
{ "run_id": "investigator-1789339802-0c89c5b8abd7", ... }
```

`investigator` is a cheap, read-only blueprint (deepseek-v4.1-flash, no shell,
no write tools) — chosen deliberately so a child can never contend for the
checkout.

## (c) Where run id + parent/task association were persisted

**File:** `/data/work/leviath/.hivemind/wire-0160-dispatch.json`

The run store keeps a run's own metadata at
`/data/.leviath/runs/<run_id>/meta.json` (verified by reading it — see (d)).
**But** a CLI-dispatched run's `meta.json` carries `parent_run_id: null`
(`lev run` exposes no `--parent-run-id` flag). So the run store records the
child, not the edge.

The parent/task association is therefore persisted **by the caller**, in the
coordination file above, one record per dispatch:

| field | value |
|---|---|
| `child_run_id` | `investigator-1789339768-6f33646874c1` |
| `parent_run_id` | `hivemind-1789339438-1083d0ffd3b3` (this worker) |
| `grandparent_run_id` | `hivemind-1789338291-ad3831600bcd` (the fan-out director) |
| `task_id` | `wire-0160-check-1` |
| `meta_path` | `/data/.leviath/runs/investigator-1789339768-6f33646874c1/meta.json` |
| `launch_command` | the exact `lev run … --json` line used |

The grandparent link was independently corroborated: a child read
`/data/.leviath/runs/hivemind-1789339438-1083d0ffd3b3/meta.json` and reported
`parent_run_id = hivemind-1789338291-ad3831600bcd`, `depth = 1`,
`workdir = /data/work/leviath` — matching this ledger.

## (d) Short-check collection — run is progressing

Two checks on the second run, both real `lev ps` reads:

```
t≈3s
investigator-1789339802-0c89c5b8abd7  active  answer 1/2  iter 1  tools 3  age 3s
  meta.json status = running

t≈25s
investigator-1789339802-0c89c5b8abd7  complete  answer 1/2  iter 3  tools 4  age 25s
  meta.json status = complete
```

Caught it genuinely **mid-flight** at check 1 (`active`, `running`), then
terminal at check 2. `lev result` returned the child's answer:

> "Yes — the run store keeps a run's metadata at
> `/data/.leviath/runs/<run_id>/meta.json`." … plus a per-file breakdown of
> `meta.json` / `stages.json` and the parent `hivemind` record
> (`status running`, `depth 1`, `parent_run_id hivemind-1789338291-ad3831600bcd`).

Run 1's result likewise confirmed the layout and the parent chain.

## (e) Mechanism is the EXISTING one — nothing new introduced

Confirmed. The loop used only:

- **`lev run` (nonblocking)** — the existing spawn path (`spawn_agent` semantics)
- **`lev ps`** — the existing check path (`check_agent` semantics)
- **`lev result`** — the existing collect path
- **the run store** `/data/.leviath/runs/<run_id>/` — the existing persistence

**No** new scheduler, queue, framework, dashboard, architecture package, or
broad redesign was added. No new code was written. The only new bytes are
coordination state: a dispatch ledger, two task prompt files, two captured
dispatch outputs.

## (f) Changed file paths + sha256

All files are **new and untracked** (`.hivemind/`). The tracked tree is
untouched: `git status --porcelain | grep -v '^??'` is empty, and HEAD is still
`15dc182e0bc6c28dfa93f4b8dd3c423372808f61`.

| path | sha256 |
|---|---|
| `.hivemind/wire-0160-dispatch.json` | `612cb0bf636c64e20a0156503b9703ea81ad1f10fe222f1053d360a011568e2f` |
| `.hivemind/wire-0160-worker-task.txt` | `7990dfd76f7b4126ee97f6f892d4e773dde6a75737d20d22cd4088f8dc5cf61f` |
| `.hivemind/wire-0160-worker-task-2.txt` | `e400d191b99954cddad86a50dcf6a606a62fbbf8f68eea14a92f15b8c0530e73` |
| `.hivemind/wire-0160-dispatch.out` | `80c697d13bfd34b426918e477853ad3e6890ced493633aa8405e8d3a54c04122` |
| `.hivemind/wire-0160-dispatch-2.out` | `263deb39cafc433f302409ce9911d6d17104292491d6d6880e226b92792589ab` |

## Constraints honoured

One writer per checkout — re-checked `lev ps` before editing; no other run held
`/data/work/leviath`. Writes limited to coordination state. No global model
override, no Gemini/google model, no infra change, restart, deploy, or
destructive action. No push/PR/comment, no public or upstream write, no Railway
mutation, no new paid benchmark, no message to a human. No secret value written
anywhere. Telegram session `20260825_202520_447f5122` untouched. Lambda/Vulkan/
capture work untouched.

## Acceptance against the ORIGINAL issued criteria

Criteria were not expanded and no closed work was reopened. Every element of the
done_check is answered above: (a) `bd` absent, stated plainly; (b) exact
nonblocking launch command + real run id; (c) persistence path
`.hivemind/wire-0160-dispatch.json`, plus the honest finding that the run store
itself leaves `parent_run_id` null; (d) a short-check collection showing
`active` → `complete` with real `lev ps` output; (e) confirmation the mechanism
is the existing one; (f) changed paths with sha256.