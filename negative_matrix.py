#!/usr/bin/env python3
"""Re-executable DoD gate discrimination for work order GQL-01.

The gate proves nothing if it passes on everything. This script:

  1. runs the gate on the BASE SDL (15dc182e) and requires it to FAIL;
  2. runs the gate on the REPAIRED SDL and requires it to PASS;
  3. applies 7 deliberate definition-of-done regressions to the repaired SDL
     in a scratch copy, and requires the gate to FAIL on each one, with a
     reason that names the right defect.

Each regression is generated here from the repaired SDL, so the matrix does not
depend on any untracked scratch file. Exit 0 iff every expectation holds.

Usage: python3 negative_matrix.py <path-to-repo>
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

GATE_REL = "docs/workflow/check-workflow-contract.py"
SDL_REL = "docs/workflow/schema-workflow.graphql"
BASE_REL = "docs/workflow/evidence/schema-workflow.base-15dc182e.graphql"

# (name, old, new, expected substring of the failure reason)
REGRESSIONS = [
    (
        "neg4 interface-as-argument",
        "dispatchWorkflow(workflowId: ID!, input: PortValue!): WorkflowRun!",
        "dispatchWorkflow(workflowId: ID!, input: WorkflowStepInput!): WorkflowRun!",
        # The interface-in-argument position is caught at build time
        # ("Argument type must be a GraphQL input type.") before the DoD
        # collector runs; both phrasings name the same defect (R1).
        "GraphQL input type",
    ),
    (
        "neg5 severity/enforcement conflation",
        "  severity: EdgeInvariantSeverity!\n  enforcedAt: [InvariantEnforcement!]!\n  \"\"\"Why it failed, in the caller's terms.\"\"\"\n",
        "  severity: EdgeInvariantSeverity!\n  \"\"\"Why it failed, in the caller's terms.\"\"\"\n",
        "enforcedAt",
    ),
    (
        "neg6 NEXT revival",
        "enum EdgeKind { BRANCH GATE JOIN PARALLEL }",
        "enum EdgeKind { NEXT BRANCH GATE JOIN PARALLEL }",
        "prev-next revival",
    ),
    (
        "neg7 StepContract/stepNumber revival",
        "type WorkflowStep {",
        "type StepContract { step: WorkflowStep!  binding: String!  stepNumber: Int! }\ntype WorkflowStep {",
        "revival",
    ),
    (
        "neg8 nullable output/error pair",
        "  outcome: WorkflowRunOutcome!\n  artifacts: [ArtifactRef!]!",
        "  output: WorkflowStepOutput\n  error: String\n  artifacts: [ArtifactRef!]!",
        "implicit-state",
    ),
    (
        "neg9 mirror Create*Input",
        "type Mutation {",
        "input CreateWorkflowInput { name: String! }\ntype Mutation {",
        "mirror input type",
    ),
    (
        "neg10 EXPIRED in the choice enum",
        "enum GateDecision { APPROVE REJECT }",
        "enum GateDecision { APPROVE REJECT EXPIRED }",
        "outcome the caller cannot decide",
    ),
]


def run_gate(gate: Path, sdl: Path) -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, str(gate), str(sdl)],
        capture_output=True, text=True,
    )
    return r.returncode, (r.stdout + r.stderr)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: negative_matrix.py <repo>", file=sys.stderr)
        return 2
    repo = Path(argv[0]).resolve()
    gate = repo / GATE_REL
    sdl = repo / SDL_REL
    base = repo / BASE_REL
    for p in (gate, sdl, base):
        if not p.is_file():
            print(f"FAIL missing {p}", file=sys.stderr)
            return 2

    failures: list[str] = []

    # 1. base must fail
    rc, _ = run_gate(gate, base)
    print(f"base SDL             exit={rc} (expect 1)")
    if rc != 1:
        failures.append(f"base SDL gate exit {rc}, expected 1 (gate is vacuous)")

    # 2. repaired must pass
    rc, out = run_gate(gate, sdl)
    print(f"repaired SDL         exit={rc} (expect 0)")
    if rc != 0:
        failures.append(f"repaired SDL gate exit {rc}, expected 0: {out[-300:]}")

    # 3. negative matrix
    src = sdl.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, old, new, want in REGRESSIONS:
            if src.count(old) != 1:
                failures.append(f"{name}: anchor found {src.count(old)} times, expected 1")
                continue
            variant = tmp / (name.split()[0] + ".graphql")
            variant.write_text(src.replace(old, new, 1), encoding="utf-8")
            rc, out = run_gate(gate, variant)
            got = want in out
            print(f"{name:42s} exit={rc} reason-has-{want!r}={got} (expect exit 1, True)")
            if rc != 1:
                failures.append(f"{name}: gate exit {rc}, expected 1")
            if not got:
                failures.append(f"{name}: failure reason did not mention {want!r}: {out[-200:]}")

    if failures:
        for f in failures:
            print(f"FAIL {f}", file=sys.stderr)
        print(f"negative_matrix: FAIL ({len(failures)} expectation(s))")
        return 1
    print(f"negative_matrix: PASS ({len(REGRESSIONS)} regressions caught, base rejected, repaired accepted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
