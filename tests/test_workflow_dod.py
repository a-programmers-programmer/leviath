"""GQL-07 follow-up RED/GREEN tests for the unmet DoD lines.

DoD lines under test here:
  * an INDIVIDUAL STEP runs through the SAME checks as a whole workflow;
  * an UNAPPROVED sensitive flow BLOCKS;
  * a WARN waiver identity and reason survive in the run evidence.

Run: python3 -m pytest tests/ -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leviath_workflow.model import DAG, PortDef, PortBinding, PortType, StepDef
from leviath_workflow.evidence import WaiverRecord
from leviath_workflow.runner import WorkflowRunner
from leviath_workflow.model import Waiver


def _sensitive_dag() -> DAG:
    return DAG(steps=[
        StepDef(
            name="push_prod",
            inputs=[],
            outputs=[PortDef("msg", PortType.STRING)],
            command=["python3", "-c", "import json;print(json.dumps({'msg': 'pushed'}))"],
            sensitive=True,
        ),
    ])


def _branch_join_dag() -> DAG:
    return DAG(steps=[
        StepDef(name="start", inputs=[], outputs=[PortDef("result", PortType.STRING)],
                command=["echo", '{"result": "hello"}']),
        StepDef(name="left", inputs=[PortBinding(step="start", port="result", as_name="in_val")],
                outputs=[PortDef("result", PortType.STRING)],
                command=["python3", "-c",
                         "import json,sys;d=json.load(sys.stdin);print(json.dumps({'result':d['in_val'].upper()}))"]),
        StepDef(name="right", inputs=[PortBinding(step="start", port="result", as_name="in_val")],
                outputs=[PortDef("result", PortType.STRING)],
                command=["python3", "-c",
                         "import json,sys;d=json.load(sys.stdin);print(json.dumps({'result':d['in_val'][::-1]}))"]),
        StepDef(name="join",
                inputs=[PortBinding(step="left", port="result", as_name="left_val"),
                        PortBinding(step="right", port="result", as_name="right_val")],
                outputs=[PortDef("combined", PortType.STRING)],
                command=["python3", "-c",
                         "import json,sys;d=json.load(sys.stdin);print(json.dumps({'combined':d['left_val']+'|'+d['right_val']}))"]),
    ])


# ---------------------------------------------------------------------------
# T7: an UNAPPROVED sensitive flow blocks
# ---------------------------------------------------------------------------
def test_unapproved_sensitive_flow_blocks():
    """A sensitive step with no approved waiver blocks the run before it executes."""
    dag = _sensitive_dag()
    evidence = WorkflowRunner(dag).run()
    assert not evidence.ok, "unapproved sensitive flow must block"
    assert evidence.blocked is True
    assert evidence.results == {}, "a blocked run must not execute steps"
    joined = " ".join(evidence.errors).lower()
    assert "unapproved" in joined and "sensitive" in joined, evidence.errors


# ---------------------------------------------------------------------------
# T8: an APPROVED sensitive flow records a WARN waiver; identity + reason survive
# ---------------------------------------------------------------------------
def test_approved_sensitive_waiver_identity_and_reason_survive():
    """A waiver carrying identity + reason lets the run proceed and survives in evidence."""
    dag = _sensitive_dag()
    waiver = Waiver(step="push_prod", identity="release-manager", reason="approved by CAB-42")
    evidence = WorkflowRunner(dag, waivers=[waiver]).run()
    assert evidence.ok, evidence.errors
    assert evidence.results["push_prod"].outputs["msg"] == "pushed"
    assert len(evidence.waivers) == 1
    w = evidence.waivers[0]
    assert (w.step, w.identity, w.reason, w.severity) == (
        "push_prod", "release-manager", "approved by CAB-42", "WARN")

    # the identity and the reason survive serialization into run evidence
    serialized = json.loads(evidence.to_json())
    assert serialized["waivers"][0]["identity"] == "release-manager"
    assert serialized["waivers"][0]["reason"] == "approved by CAB-42"
    assert serialized["waivers"][0]["severity"] == "WARN"


# ---------------------------------------------------------------------------
# T9: an INDIVIDUAL STEP runs through the same checks
# ---------------------------------------------------------------------------
def test_individual_step_runs_same_checks_happy_path():
    """Step mode runs one step, after the same structural checks, and types its output."""
    dag = _branch_join_dag()
    evidence = WorkflowRunner(dag).run_single_step("start", {})
    assert evidence.ok, evidence.errors
    assert evidence.mode == "step" and evidence.step == "start"
    assert list(evidence.results) == ["start"], "only the named step may run"
    assert evidence.results["start"].outputs["result"] == "hello"


def test_individual_step_blocks_on_missing_endpoint():
    """Step mode blocks on a missing endpoint exactly as the whole-DAG run does."""
    dag = DAG(steps=[
        StepDef(name="consumer", inputs=[PortBinding(step="ghost", port="x")],
                outputs=[PortDef("out", PortType.STRING)], command=["echo", "{}"]),
    ])
    evidence = WorkflowRunner(dag).run_single_step("consumer", {})
    assert not evidence.ok and evidence.blocked is True
    assert evidence.results == {}
    assert any("ghost" in e for e in evidence.errors), evidence.errors


def test_individual_step_blocks_on_unapproved_sensitive_flow():
    """Step mode blocks an unapproved sensitive step too."""
    evidence = WorkflowRunner(_sensitive_dag()).run_single_step("push_prod", {})
    assert not evidence.ok and evidence.blocked is True
    assert evidence.results == {}
    assert any("unapproved" in e.lower() for e in evidence.errors), evidence.errors


def test_individual_step_blocks_on_cycle_in_ancestors():
    """Step mode sees the cycle the step sits in and blocks."""
    dag = DAG(steps=[
        StepDef(name="A", inputs=[PortBinding(step="C", port="z")],
                outputs=[PortDef("x", PortType.STRING)], command=["echo", "{}"]),
        StepDef(name="C", inputs=[PortBinding(step="A", port="x")],
                outputs=[PortDef("z", PortType.STRING)], command=["echo", "{}"]),
    ])
    evidence = WorkflowRunner(dag).run_single_step("A", {})
    assert not evidence.ok and evidence.blocked is True
    assert any("cycle" in e.lower() for e in evidence.errors), evidence.errors


def test_individual_step_blocks_on_port_type_mismatch():
    """Step mode applies the same port-type assignability rule."""
    dag = DAG(steps=[
        StepDef(name="src", inputs=[], outputs=[PortDef("val", PortType.STRING)], command=["echo", "{}"]),
        StepDef(name="dst", inputs=[PortBinding(step="src", port="val", type=PortType.INT)],
                outputs=[PortDef("out", PortType.INT)], command=["echo", "{}"]),
    ])
    evidence = WorkflowRunner(dag).run_single_step("dst", {"val": "7"})
    assert not evidence.ok and evidence.blocked is True
    assert any("type" in e.lower() for e in evidence.errors), evidence.errors


# ---------------------------------------------------------------------------
# T10: reachability and join shape
# ---------------------------------------------------------------------------
def test_reachability_flags_unreachable_step():
    """A step that no entry step can reach is reported."""
    from leviath_workflow.checks import check_reachability

    dag = DAG(steps=[
        StepDef(name="entry", inputs=[], outputs=[PortDef("o", PortType.STRING)], command=["echo", "{}"]),
        StepDef(name="orphan", inputs=[PortBinding(step="entry", port="o")],
                outputs=[PortDef("p", PortType.STRING)], command=["echo", "{}"]),
        StepDef(name="island", inputs=[], outputs=[PortDef("q", PortType.STRING)], command=["echo", "{}"]),
    ])
    result = check_reachability(dag)
    # every step is an entry candidate here, so nothing is unreachable; a *dangling*
    # island is detected by the endpoint rule instead.
    assert result.ok is True

    # now make `orphan` unreachable by pointing it at a cyclic-only subgraph
    cyc = DAG(steps=[
        StepDef(name="entry", inputs=[], outputs=[PortDef("o", PortType.STRING)], command=["echo", "{}"]),
        StepDef(name="x", inputs=[PortBinding(step="y", port="p")],
                outputs=[PortDef("o2", PortType.STRING)], command=["echo", "{}"]),
        StepDef(name="y", inputs=[PortBinding(step="x", port="o2")],
                outputs=[PortDef("p", PortType.STRING)], command=["echo", "{}"]),
    ])
    result = check_reachability(cyc)
    assert result.unreachable == ["x", "y"], result.unreachable


def test_branch_and_join_shape_is_checked():
    """A join that binds the same source port twice is reported as ambiguous."""
    from leviath_workflow.checks import check_branches_joins

    dag = DAG(steps=[
        StepDef(name="start", inputs=[], outputs=[PortDef("r", PortType.STRING)], command=["echo", "{}"]),
        StepDef(name="dup_join",
                inputs=[PortBinding(step="start", port="r", as_name="a"),
                        PortBinding(step="start", port="r", as_name="b")],
                outputs=[PortDef("c", PortType.STRING)], command=["echo", "{}"]),
    ])
    result = check_branches_joins(dag)
    assert any("ambiguous" in e.lower() for e in result.errors), result.errors

    # and the real branch/join DAG is accepted by the same check
    assert check_branches_joins(_branch_join_dag()).ok is True
