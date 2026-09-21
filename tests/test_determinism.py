"""GQL-07 determinism proof: the same DAG must report the same bytes.

`check_cycles` walked a Python `set`, and `WorkflowRunner._topo_sort` drained a
`set` into its Kahn queue. Set iteration order changes with hash randomization,
so two runs of the SAME graph produced different cycle text ("A -> C -> A" vs
"C -> A -> C") and, for independent steps, a different execution order. Error
text and run order must be reproducible, so both now visit in declaration order.

This module re-runs each case under several distinct PYTHONHASHSEED values in
child processes and asserts the evidence is byte-identical.

Run: python3 -m pytest tests/test_determinism.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"

SEEDS = ["0", "1", "7", "12345", "random"]


def _run(args: list[str], seed: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    return subprocess.run(
        [sys.executable, "-m", "leviath_workflow", *args],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=120,
    )


def _evidence_across_seeds(args: list[str]) -> tuple[list[str], list[int]]:
    """Return the stdout of the CLI for each seed, plus the exit codes."""
    outs, rcs = [], []
    for seed in SEEDS:
        proc = _run(args, seed)
        outs.append(proc.stdout)
        rcs.append(proc.returncode)
    return outs, rcs


# ---------------------------------------------------------------------------
# T11: a cycle reports the same path no matter the hash seed
# ---------------------------------------------------------------------------
def test_cycle_error_text_is_stable_across_hash_seeds():
    outs, rcs = _evidence_across_seeds(["run", "--dag", str(FIX / "cycle.json")])
    assert rcs == [1] * len(SEEDS), rcs
    assert len(set(outs)) == 1, f"cycle evidence changed between runs: {sorted(set(outs))}"
    payload = json.loads(outs[0])
    assert payload["blocked"] is True and payload["results"] == {}
    assert any("cycle" in e.lower() for e in payload["errors"]), payload["errors"]


# ---------------------------------------------------------------------------
# T12: the branch/join DAG runs in the same step order and yields the same bytes
# ---------------------------------------------------------------------------
def test_branch_join_run_is_stable_across_hash_seeds():
    outs, rcs = _evidence_across_seeds(["run", "--dag", str(FIX / "branch_join.json")])
    assert rcs == [0] * len(SEEDS), rcs
    assert len(set(outs)) == 1, "branch/join evidence changed between runs"
    payload = json.loads(outs[0])
    assert payload["results"]["join"]["outputs"]["combined"] == "HELLO|olleh"
    # declaration order = start, left, right, join — reproducible, not set order
    assert list(payload["results"]) == ["join", "left", "right", "start"]


# ---------------------------------------------------------------------------
# T13: an unapproved sensitive flow blocks with identical evidence every time
# ---------------------------------------------------------------------------
def test_sensitive_block_evidence_is_stable_across_hash_seeds():
    outs, rcs = _evidence_across_seeds(
        ["run", "--dag", str(FIX / "sensitive_unapproved.json")]
    )
    assert rcs == [1] * len(SEEDS), rcs
    assert len(set(outs)) == 1, "sensitive-flow evidence changed between runs"
    assert json.loads(outs[0])["blocked"] is True


# ---------------------------------------------------------------------------
# T14: the waived run keeps its waiver identity and reason under every seed
# ---------------------------------------------------------------------------
def test_waiver_evidence_is_stable_across_hash_seeds():
    outs, rcs = _evidence_across_seeds([
        "run", "--dag", str(FIX / "sensitive_unapproved.json"),
        "--waive-step", "push_prod",
        "--waiver-identity", "release-manager",
        "--waiver-reason", "approved by CAB-42",
    ])
    assert rcs == [0] * len(SEEDS), rcs
    assert len(set(outs)) == 1, "waiver evidence changed between runs"
    w = json.loads(outs[0])["waivers"][0]
    assert (w["identity"], w["reason"], w["severity"]) == (
        "release-manager", "approved by CAB-42", "WARN")


# ---------------------------------------------------------------------------
# T15: a multi-branch fan-out is ordered by declaration, not by hashing
# ---------------------------------------------------------------------------
def test_independent_step_order_follows_declaration():
    from leviath_workflow.model import DAG, PortDef, PortBinding, PortType, StepDef
    from leviath_workflow.runner import WorkflowRunner

    dag = DAG(steps=[
        StepDef(name="src", inputs=[], outputs=[PortDef("v", PortType.STRING)],
                command=["python3", "-c", "import json;print(json.dumps({'v':'x'}))"]),
        StepDef(name="b1", inputs=[PortBinding(step="src", port="v", as_name="i")],
                outputs=[PortDef("o", PortType.STRING)],
                command=["python3", "-c", "import json,sys;d=json.load(sys.stdin);print(json.dumps({'o':d['i']+'1'}))"]),
        StepDef(name="b2", inputs=[PortBinding(step="src", port="v", as_name="i")],
                outputs=[PortDef("o", PortType.STRING)],
                command=["python3", "-c", "import json,sys;d=json.load(sys.stdin);print(json.dumps({'o':d['i']+'2'}))"]),
        StepDef(name="b3", inputs=[PortBinding(step="src", port="v", as_name="i")],
                outputs=[PortDef("o", PortType.STRING)],
                command=["python3", "-c", "import json,sys;d=json.load(sys.stdin);print(json.dumps({'o':d['i']+'3'}))"]),
    ])
    order = WorkflowRunner(dag)._topo_sort()
    assert order == ["src", "b1", "b2", "b3"], order
    ev = WorkflowRunner(dag).run()
    assert ev.ok, ev.errors
    assert sorted(ev.results) == ["b1", "b2", "b3", "src"]
