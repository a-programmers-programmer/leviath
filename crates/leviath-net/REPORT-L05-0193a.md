# REPORT — lane L05-0193a (node 0193 A)

Written by the lane itself. This is the written report the evidence hard gate
requires: paths, hashes, exact commands, exit codes, base commit, diff, spend.

- lane: `L05-0193a`
- branch: `lane/L05-0193a`
- worktree: `/data/work/graphql-realization/gql-00/wt/L05-0193a`
- base commit: `7b6e050e89e6aea7b51453e633fbe9209c50a844`
- model (only model used): `openrouter/deepseek/deepseek-v4.1-flash`
- report path: `crates/leviath-net/REPORT-L05-0193a.md` (inside the declared set)
- written UTC: 2026-09-15T02:45:19Z

## 1. Declared path set — what this lane may write

    crates/leviath-net
    crates/leviath-agent-client

No shared per-file exceptions. No `Cargo.toml` and no `Cargo.lock` write.
Every path below is inside that set.

## 2. Base identity — observed, not assumed

| Command | Output | Exit |
|---|---|---|
| `git rev-parse HEAD` | `7b6e050e89e6aea7b51453e633fbe9209c50a844` | 0 |
| `git branch --show-current` | `lane/L05-0193a` | 0 |
| `git cat-file -t 7b6e050e` | `commit` | 0 |

Base object type is `commit`, so the base is a commit and not a tag or tree.

## 3. Diff vs base — empty, and that is the finding

| Command | Output | Exit |
|---|---|---|
| `git diff --quiet 7b6e050e -- crates/leviath-net crates/leviath-agent-client` | (none) | 0 |
| `git diff --stat 7b6e050e -- crates/leviath-net crates/leviath-agent-client` | (empty) | 0 |
| `git diff --stat 7b6e050e` (whole worktree) | (empty) | 0 |
| `git diff --stat 7b6e050e -- Cargo.toml Cargo.lock` | (empty) | 0 |
| `git status --porcelain -uall` | two untracked logs, listed in §5 | 0 |

`git diff --quiet` exits 0, so the declared crates are **byte-identical to
base**. This lane authored no source change and no test. `Cargo.toml` and
`Cargo.lock` are unmodified.

## 4. Source identity as hashes (observed)

| Path (inside declared set) | sha256 |
|---|---|
| `crates/leviath-net/src/lib.rs` | `9a194062fa3c3749d8970d97cda7113775f77ad6e32e1e0f2c5e3a40ac646f82` |
| `crates/leviath-net/src/read_caps.rs` | `0fb719741dafd53d78a5454dc8996491c56bb7f41b3ac29fdefbf98c3f9e056e` |
| `crates/leviath-agent-client/src/lib.rs` | `f09a8dfef5dfad5bda03d99d284f7309062a1d9a1bff1b461bca01a52816ab15` |
| `crates/leviath-agent-client/src/mapping.rs` | `16562983e85ea9ebcb5e95fffbd4451068bd080b909d02f42cf2fca194ff465d` |
| `crates/leviath-agent-client/src/protocol.rs` | `6edae7f7ce40f209400c96a36d762a2f9aa888d981e2ed824c0cc76e204c017c` |

These five files are the whole tracked content of the declared set. Five
source files, no more.

## 5. On-disk products from earlier in this lane

Two untracked logs exist. They are the lane's only prior products:

| Path | sha256 | Bytes |
|---|---|---|
| `crates/leviath-net/.lane-L05-0193a/ac-test.log` | `fe33cfd8756ec9dab9a8b5b9842c38aabf7db179e472ab39bdb6d5399b11383e` | — |
| `crates/leviath-net/.lane-L05-0193a/net-test.log` | `8f9cd29ac873f5083bea695c510d6d388b4ad84658f1cc8e8b99b16e6f3318f8` | — |

File inventory of the declared set: `crates/leviath-agent-client/{Cargo.toml,
README.md,src/lib.rs,src/mapping.rs,src/protocol.rs}` and
`crates/leviath-net/{Cargo.toml,README.md,src/lib.rs,src/read_caps.rs}`, plus
the two logs above and this report.

## 6. Test evidence — exact command, output, exit code

Command:

    cargo test -p leviath-net -p leviath-agent-client

Output (trimmed to the result lines):

    running 43 tests
    test result: ok. 43 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
    running 30 tests
    test result: ok. 30 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out
    running 0 tests        (doc-tests leviath_agent_client)
    running 0 tests        (doc-tests leviath_net)

Exit code: `0` (`TDD_EXIT:0`). State: **PASS (green)**. The two logs in §5
report the same suites and the same totals, so the recorded run and the fresh
re-run agree. This freshly run result is the second run; it is not taken from
the producing run's word.

No RED phase exists. The lane added no test, so there is no failing-first
step to record. This is a harvest-and-verify lane, not a build lane.

## 7. What was harvested, and from where (read-only, outside the write set)

Read for identity. Nothing outside the declared set was written, built or run.

| Artifact | sha256 | Bytes |
|---|---|---|
| `ACCEPTANCE-0193-A-bridge-s20.json` | `70e38b4caa0b54a006c8bc58bf7d642a573206645839fe42015c8fa532c8dbd8` | 5585 |
| `tasks/0193-bridge-20260914T2140Z.txt` | `972364deb0e16e68b99100afbe82024b53f017799a8bfa7ba22b4ece3e294d00` | 7728 |
| `tmp/lane-tasks/L05-0193a.task.md` | `8939f18e4e34d8dbf38337acc06c78de35ea9fb249389a5bf482d33ee18d7035` | 4080 |
| `LANES-0193-s28.json` | `36b8f4e05b080b1ded76744c637c6a167feed1a71126116868a79185b59c82a7` | 13025 |

Bridge code named by the parent task (§A), hashed read-only. It is **outside**
this lane's declared set, so this lane did not read its text or change it.

| Path | sha256 | Bytes |
|---|---|---|
| `/data/bin/leviath_bridge_0193.py` | `1052529db03ec6ff9ea018c7aa47cf6d2d5d64352dafb6a17aa10d4890daf0ff` | 20702 |
| `/data/bin/REPORT-0193-BRIDGE.md` | `1f6b61e00a696e9cbfee6d50586a59e2508faf8b82c73e2312f52bcd68ab7122` | 11251 |
| `/data/bin/hermes_boot.py` | `a809715bd9f13444a9f1b017a44896a3c0030ab061bd011d05b04e76f09bf42a` | 46550 |

## 8. Duplicate route — the exported node (a) result

The Task says: take the existing native coordinator route, and preserve the
original task and workdir. The node (a) result is exported to this lane.

| Field | Value |
|---|---|
| run_id | `worker-1789429391-bf8aa518ef28` |
| status | `complete` |
| model | `openrouter/deepseek/deepseek-v4.1-flash` |
| tool_calls | 409 |
| cost_usd | 1.5692602979999988 |
| result path | `/data/.leviath/runs/worker-1789429391-bf8aa518ef28/final_output` |
| result bytes | 2592 |
| result sha256 | `38600ddeee7e80a5b919bdd4e832894e6c674ba728e2fcfc26675c0a09dc12a2` |

This result is delivered **once**. This lane does not re-send it and does not
write it to a bus.

## 9. Provider spend — actual, not estimated

| Run | Role | Model | cost_usd | At |
|---|---|---|---|---|
| `worker-1789438986-7ef2aad3cab2` | this lane | `openrouter/deepseek/deepseek-v4.1-flash` | 0.45401711 | iteration 71, tool_calls 121 |
| `worker-1789429391-bf8aa518ef28` | node (a), exported | `openrouter/deepseek/deepseek-v4.1-flash` | 1.5692602979999988 | iteration 282, tool_calls 409 |
| `worker-1789421967-b6bd71cfc5a4` | §A bridge, accepted s20 | `openrouter/deepseek/deepseek-v4.1-flash` | 0.6534401250000005 | tool_calls 118 |

This lane dispatched no run. It spent nothing beyond its own inference.

No provider throttling was observed. There is no raw throttle line to report,
so none is reported. No count is reported; the count metric is invalid at
source.

## 10. States — blocked, passed and verified kept apart

| Item | State | Basis |
|---|---|---|
| Path discipline | **PASS** | whole-worktree diff vs base is empty; all files under the declared set |
| `Cargo.toml` / `Cargo.lock` untouched | **PASS** | `git diff --stat 7b6e050e -- Cargo.toml Cargo.lock` empty |
| Base identity | **VERIFIED** | `rev-parse HEAD` and `cat-file -t` observed in §2 |
| Tests green | **PASS** | fresh `cargo test`, exit 0, 43 + 30 passed (§6) |
| Source change by this lane | **BLOCKED (none intended)** | diff vs base empty; the lane is a harvest lane |
| Bridge activation | **INERT, NOT PERFORMED** | not this lane's to perform (§11) |
| Node (a) contract | **OBSERVED, NOT ACCEPTED BY THIS LANE** | exported result hashed in §8; acceptance is the coordinator's |

A terminal status alone is not acceptance. This report is the evidence, not
the verdict.

## 11. Activation — left inert

The accepted §A packet (`ACCEPTANCE-0193-A-bridge-s20.json`) records activation
as NOT PERFORMED. Its field `activation.exact_required_operation_reported_only`
names the exact operation. This lane quotes that field; it does **not** perform
it, and it did not re-derive the operation.

    1. Set Railway service `hermes-gateway-development` startCommand to:
       python /opt/hermes/reaper.py python /data/bin/hermes_boot.py
    2. Restart `hermes-gateway-development` once so the new startCommand takes
       effect.

The packet's recorded confirmation signal, after that activation:

    boot log `[boot] volume shim: leviath native bridge registered = True`
    plus a tools/list carrying the four leviath.* names

Why a restart is needed, as the packet records it: the running gateway is the
image boot script (`python /opt/hermes/reaper.py python /opt/hermes/railway-boot.py`),
not the volume shim `/data/bin/hermes_boot.py`. A Python function cannot be
added to a running process without a restart.

This lane made no Railway change, no restart, no redeploy, and no
`startCommand` change. The bridge stays INERT. Activation is the principal's
call. No live native MCP `tools/list` or tool-call evidence exists, so this
report claims none.

## 12. What I accepted, what I rejected, what I could not determine

**Accepted.** The base identity and the absence of any diff. Both were observed
by hand in this stage, not inferred from a summary. The five source-file hashes
in §4. The green test result in §6. The exported node (a) result hash in §8.

**Rejected.** Any claim that this lane produced a source change: the diff is
empty, so there is none to claim. Any claim of activated capability: activation
is inert. Any count of throttling events: that metric is invalid at source.

**Could not determine.** The real end-to-end cost of the 0193 workstream. Three
run costs are on record in §9, but the full chain of 0193 runs is not in this
lane's reach, and this lane will not invent a total.

## 13. Limits — real, not invented

No RAM estimate is used. The retired numeric ceiling is not cited and not used
to size anything. There is no fabricated limit in this report.

## 14. Out of scope — not done, by design

No write outside the declared set. No edit to the config, the metrics script,
or any sweeper. No write under the ceo-bus, bin, hermes or state paths. No
admission file, no outbox write, no bus write. No new scheduler, queue,
service or dashboard. No credential or permission change. No secret read or
write. No Railway mutation, no restart, no redeploy. No commit, push or deploy
from this lane. Never `--wait`, never `--yolo`. No second coordinator spawned.