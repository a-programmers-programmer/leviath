# Oracle workflow (experimental)

Oracle is a bundled, experimental blueprint for repository work that needs an
explicit decision after initial evidence gathering and before coding and
acceptance. It keeps the model responsible for deciding what should happen
while the runtime and a small trusted workspace kernel enforce the work order.

Install the bundled blueprints, then run it from the Git working tree:

```sh
lev setup --install-agents
lev run oracle --task "Add request validation to the users endpoint"
```

The kernel requires Python 3, `git`, and a POSIX shell. macOS, Linux, and WSL
are the supported environments. The blueprint is an app-level control flow;
it is not OS isolation. An approved check may execute project code, so the
normal Leviath runtime policy gates still apply.

## The run shape

The bootstrap creates an initial reader order, so the first evidence pass
precedes Oracle's decisions. The normal path uses two Oracle visits. Each visit
is tool-free and must return one complete JSON decision. Runtime hooks clear the model-facing
conversation and tool result regions around those visits, then rewrite the
controller's workspace call with the operation and run identity supplied by
the runtime.

Between visits, the workflow uses deterministic fan-out regions:

- `items_region` carries the exact reader, coder, or verifier items selected by
  the kernel.
- `transition_region` carries the plain next destination selected by the
  runtime.

Workers receive one canonical item and its order identity. They cannot choose a
different path, run, or order by changing tool arguments. The hook also limits
tool use to the workspace tool and requires one call per turn. Worker execution
is serial for coders and verifiers in this version; readers may run in parallel.
This avoids conflicting writes to the shared checkout and ledger.

The kernel stores a run ledger under the Git worktree's
`leviath-oracle` Git path. It records immutable order and receipt files,
workspace snapshots, and content-addressed artifacts. Before acceptance it
rechecks the current snapshot. A patch carries the file's expected SHA-256;
the kernel refuses a stale compare-and-swap and writes through a temporary file
before replacing the target. Paths are repository-relative POSIX paths, with
Git internals and symlink traversal refused.

These receipts prove what the kernel observed and authorized. They do not make
model output or ignored files source proof: evidence should name the path,
line range, and file SHA, and a verifier should use the repository itself.

## Decisions and work orders

The protocol recognizes `REQUEST_EVIDENCE`, `AUTHORIZE`, `REMEDIATE`, `ACCEPT`,
`ASK_USER`, and `STOP`. The decision object is bounded by the blueprint and
validated again by the kernel. Evidence and coding decisions include the
current `snapshot_id`. A useful evidence decision is:

```json
{
  "action": "REQUEST_EVIDENCE",
  "snapshot_id": "<current snapshot id>",
  "orders": [{
    "id": "read-api",
    "objective": "Locate the request parser and existing validation checks",
    "paths": ["src/api.rs", "tests/api.rs"]
  }]
}
```

Coding orders use exact paths, dependencies, and argv checks:

```json
{
  "action": "AUTHORIZE",
  "snapshot_id": "<current snapshot id>",
  "orders": [{
    "id": "code-api",
    "objective": "Implement the agreed validation and its regression test",
    "paths": ["src/api.rs", "tests/api.rs"],
    "depends_on": ["read-api"],
    "checks": [{
      "id": "api-tests",
      "argv": ["cargo", "test", "api"],
      "cwd": ".",
      "expect_tests": true
    }]
  }]
}
```

`REMEDIATE` also requires a non-empty `reason` and a non-empty
`supersedes` list naming failed orders. Replacement orders are independently
verified on the same final snapshot before the superseded orders are marked
resolved; their failures remain in `resolution_history`.

`paths` accepts at most 32 repository-relative paths per order. Checks are
argv arrays rather than shell strings; their working directory must remain in
the repository. The kernel currently bounds orders, checks, requests, patches,
receipts, visits, and coding waves conservatively in bytes and counts. Keep
patches comfortably below the installed tool's shell command budget; an
oversized patch can be refused before the kernel sees it. These are safety
bounds, not exact token counts.

Acceptance includes the current `snapshot_id` and follows approved coding
orders. If an order reported unread or contradictory evidence, `ACCEPT` must
include `dispositions: [{"order_id": "...", "reason": "..."}]` explaining why
each such finding is resolved or nonblocking. The original findings remain in
the ledger alongside that decision. Execution failures, missing inventory, and
checks that do not cover the final snapshot still prevent acceptance.

Worker calls pass `action` as a top-level string (`inspect`, `read`,
`check`, `patch`, or `seal`) with the action-specific fields alongside it.
`inspect` with `kind: "order"` returns the canonical work order. With
`kind: "file"` and an authorized `path`, it returns complete UTF-8 file content
up to 32 KiB and its SHA-256 to the worker. Raw file content stays out of the
Oracle dossier. `read` captures short exact-line citations. `ASK_USER`
and `STOP` end the run with a terminal report. After the
user answers, start a new run with the answer included in its task; this
workflow does not promise resuming a terminal run or a human interrupt.

## Model and safety choices

Keep the Oracle decision stage on the high-tier model selected by the
blueprint. Evidence readers, coders, and verifiers use the cheaper per-stage
defaults where appropriate. Do not set a global `--model` override when using
Oracle: it flattens the stage choices and can put every worker on the Oracle's
expensive tier.

The blueprint's approvals are application-level authorization. They do not
replace provider policy, Leviath tool policy, write budgets, or configured OS
sandboxing. A project check is trusted only as an allowed runtime action, and
its output is still evidence to assess. Ignored output directories and local
artifacts do not establish source changes.

## Current experimental boundary

The generated `oracle_workspace` tool exposes controller operations
`bootstrap`, `control`, `collect`, and `finalize`, plus worker actions
`inspect`, `read`, `check`, `patch`, and `seal`. Readers must authenticate
evidence before sealing (the initial reader must inspect the inventory), coders
must authenticate a patch, and verifiers must
run every approved check before sealing. Checks run with a 60-second timeout,
capture output as a content-addressed artifact, record test counts when
recognizable, and mark any source mutation during verification as incomplete.

The dossier is capped at 4,400 UTF-8 bytes; ordinary worker packets at 1,400
bytes. The Oracle's assembled request has an 8,000-byte conservative input
ceiling with a framing allowance, and a 2,048-token output cap. Oversized inputs
fail with an error instead of silently losing evidence. Tasks and work orders
must therefore be concise. These bounds can be tuned in the blueprint and kernel.
Approved project commands execute inside the configured runtime sandbox, if any;
the ledger is not a tamper-proof boundary against malicious executable code.

Useful offline checks are available through the normal repository and blueprint
validation commands. The workflow remains experimental because its policy and
blueprint surface may change as the bundled agent evolves.
