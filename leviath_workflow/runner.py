"""GQL-07 — Workflow runner: executes DAG steps via subprocess with typed ports.

Two run modes share ONE check pipeline:

  * ``run()``             — the whole branch/join DAG, steps in topological order;
  * ``run_single_step()`` — one named step, put through the same checks first.

Checks happen before any subprocess starts, so a bad graph never runs a step.
ERROR-level findings (cycle, missing endpoint, incompatible port, unreachable
step, unapproved sensitive flow) block the run. A sensitive step covered by a
signed waiver proceeds and the waiver's identity and reason land in evidence.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from collections import defaultdict, deque

from .model import DAG, StepDef, StepResult, PortBinding, PortDef, Waiver, WARN, ERROR
from .checks import check_dag, DagCheckResult
from .evidence import RunEvidence, WaiverRecord

STEP_TIMEOUT_SECONDS = 30


@dataclass
class WorkflowRunner:
    dag: DAG
    waivers: List[Waiver] = field(default_factory=list)
    waive_sensitive: bool = False
    waiver_identity: str = "workflow-runner"

    # -- approval bookkeeping -------------------------------------------------

    def _approved_steps(self) -> Set[str]:
        """Steps covered by a waiver that names a principal and a reason."""
        return {w.step for w in self.waivers if w.approved}

    def _waiver_for(self, step_name: str) -> Optional[Waiver]:
        for w in self.waivers:
            if w.step == step_name and w.approved:
                return w
        return None

    # -- shared preflight -----------------------------------------------------

    def _preflight(self) -> DagCheckResult:
        return check_dag(self.dag, approved_steps=self._approved_steps())

    def _record_check(self, evidence: RunEvidence, check: DagCheckResult) -> None:
        evidence.dag_check = {
            "ok": check.ok,
            "errors": check.errors,
            "warnings": check.warnings,
            "unreachable": check.unreachable,
        }
        evidence.errors.extend(check.errors)
        if check.errors:
            evidence.blocked = True
            evidence.ok = False

    # -- workflow mode --------------------------------------------------------

    def run(self) -> RunEvidence:
        """Execute the whole DAG: check, topo-sort, run each step, capture evidence."""
        evidence = RunEvidence(ok=False, mode="workflow")

        # 1. Structural checks (block before running anything)
        check = self._preflight()
        self._record_check(evidence, check)
        if evidence.blocked:
            return evidence

        # 2. Topological sort (Kahn's algorithm)
        order = self._topo_sort()
        if order is None:
            evidence.errors.append("cycle detected during topological sort")
            evidence.blocked = True
            return evidence

        # 3. Execute steps in order
        outputs: Dict[str, Dict[str, str]] = {}
        results: Dict[str, StepResult] = {}

        for step_name in order:
            step = self.dag.step_by_name(step_name)
            if step is None:
                continue

            result = self._run_step(step, outputs)
            results[step_name] = result

            if not result.ok:
                evidence.errors.append(f"step '{step_name}' failed: {result.error}")
                evidence.results = dict(results)
                return evidence

            outputs[step_name] = dict(result.outputs)
            for finding in result.findings:
                if finding.startswith("ERROR:"):
                    evidence.errors.append(f"step '{step_name}' ERROR: {finding}")

        # 4. Approved sensitive flows become WARN waivers (identity + reason kept)
        self._record_waivers(evidence)

        # 5. All done
        evidence.ok = len(evidence.errors) == 0
        evidence.results = dict(results)
        return evidence

    # -- step mode ------------------------------------------------------------

    def run_single_step(
        self, step_name: str, upstream_outputs: Optional[Dict[str, str]] = None
    ) -> RunEvidence:
        """Run ONE step through the same checks as a whole workflow.

        `upstream_outputs` supplies the values the step's input ports read, so a
        step can be exercised on its own without running its ancestors.
        """
        evidence = RunEvidence(ok=False, mode="step", step=step_name)
        upstream = dict(upstream_outputs or {})

        if self.dag.step_by_name(step_name) is None:
            evidence.errors.append(f"step '{step_name}' not found in DAG")
            evidence.blocked = True
            return evidence

        # 1. The SAME structural checks the whole-DAG run applies.
        check = self._preflight()
        self._record_check(evidence, check)
        if evidence.blocked:
            return evidence

        # 2. Run only the named step.
        step = self.dag.step_by_name(step_name)
        result = self._run_step(step, self._bind_upstream(step, upstream))
        evidence.results = {step_name: result}
        if not result.ok:
            evidence.errors.append(f"step '{step_name}' failed: {result.error}")
            return evidence

        # 3. Approved sensitive flows become WARN waivers here too.
        self._record_waivers(evidence)

        evidence.ok = len(evidence.errors) == 0
        return evidence

    def _bind_upstream(self, step: StepDef, upstream: Dict[str, str]) -> Dict[str, Dict[str, str]]:
        """Shape flat upstream values into the {step: {port: value}} the runner reads."""
        shaped: Dict[str, Dict[str, str]] = {}
        for inp in step.inputs:
            shaped.setdefault(inp.step, {})
            if inp.as_name and inp.as_name in upstream:
                shaped[inp.step][inp.port] = upstream[inp.as_name]
            elif inp.port in upstream:
                shaped[inp.step][inp.port] = upstream[inp.port]
        return shaped

    # -- shared execution -----------------------------------------------------

    def _record_waivers(self, evidence: RunEvidence) -> None:
        for step_def in self.dag.steps:
            if not step_def.sensitive:
                continue
            if evidence.mode == "step" and step_def.name != evidence.step:
                continue
            waiver = self._waiver_for(step_def.name)
            if waiver is None:
                continue
            evidence.waivers.append(
                WaiverRecord(
                    step=step_def.name,
                    identity=waiver.identity,
                    reason=waiver.reason,
                    severity=WARN,
                )
            )

    def _topo_sort(self) -> Optional[List[str]]:
        """Kahn's algorithm. Returns None if cycle detected.

        Ready steps are queued in DECLARATION order, not set order, so two runs
        of the same DAG execute independent steps in the same sequence.
        """
        in_degree: Dict[str, int] = defaultdict(int)
        adj: Dict[str, List[str]] = defaultdict(list)
        all_nodes: List[str] = list(dict.fromkeys(s.name for s in self.dag.steps))
        node_set = set(all_nodes)

        for s in self.dag.steps:
            in_degree.setdefault(s.name, 0)
            for inp in s.inputs:
                if inp.step not in node_set:
                    continue  # caught by the endpoint check before this runs
                adj[inp.step].append(s.name)
                in_degree[s.name] += 1

        queue: deque[str] = deque(n for n in all_nodes if in_degree[n] == 0)
        result: List[str] = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(all_nodes):
            return None  # cycle
        return result

    def _run_step(self, step: StepDef, upstream_outputs: Dict[str, Dict[str, str]]) -> StepResult:
        """Run one step as a subprocess."""
        result = StepResult(step=step.name)

        # Build input JSON from upstream outputs
        input_data: Dict[str, str] = {}
        for inp in step.inputs:
            upstream = upstream_outputs.get(inp.step, {})
            value = upstream.get(inp.port, "")
            name = inp.as_name if inp.as_name else inp.port
            input_data[name] = value

        input_json = json.dumps(input_data)

        try:
            proc = subprocess.run(
                step.command,
                input=input_json,
                capture_output=True,
                text=True,
                timeout=STEP_TIMEOUT_SECONDS,
            )
            result.exit_code = proc.returncode
            result.stdout = proc.stdout
            result.stderr = proc.stderr

            if proc.returncode != 0:
                result.ok = False
                result.error = f"exit code {proc.returncode}: {proc.stderr.strip()}"
                return result

            # Parse stdout as JSON outputs
            try:
                out_data = json.loads(proc.stdout.strip() or "{}")
                if isinstance(out_data, dict):
                    for out_def in step.outputs:
                        if out_def.name in out_data:
                            result.outputs[out_def.name] = str(out_data[out_def.name])
            except json.JSONDecodeError as e:
                result.ok = False
                result.error = f"invalid JSON output: {e}"
                return result

        except subprocess.TimeoutExpired:
            result.ok = False
            result.error = f"step timed out after {STEP_TIMEOUT_SECONDS}s"
            return result
        except FileNotFoundError as e:
            result.ok = False
            result.error = f"command not found: {e}"
            return result

        result.ok = True
        return result


def _load_dag(path: str) -> DAG:
    with open(path) as f:
        dag_data = json.load(f)
    steps = []
    for s in dag_data.get("steps", []):
        steps.append(StepDef(
            name=s["name"],
            inputs=[PortBinding(**i) for i in s.get("inputs", [])],
            outputs=[PortDef(**o) for o in s.get("outputs", [])],
            command=s.get("command", []),
            sensitive=s.get("sensitive", False),
        ))
    return DAG(steps=steps)


def run_dag_cli(argv: Optional[List[str]] = None) -> int:
    """CLI entry:
        python -m leviath_workflow run  --dag <path>
        python -m leviath_workflow step --dag <path> --step <name>
    """
    import argparse

    parser = argparse.ArgumentParser(description="GQL-07 Workflow Runner")
    parser.add_argument("mode", nargs="?", default="run", choices=["run", "step"])
    parser.add_argument("--dag", required=True, help="Path to DAG JSON file")
    parser.add_argument("--step", help="Step name (required for step mode)")
    parser.add_argument("--waive-step", action="append", default=[],
                        help="Step covered by an approved waiver (repeatable)")
    parser.add_argument("--waiver-identity", default="workflow-runner")
    parser.add_argument("--waiver-reason", default="approved out of band")
    parser.add_argument("--input", action="append", default=[],
                        help="Upstream value as name=value (repeatable, step mode)")
    args = parser.parse_args(argv or sys.argv[1:])

    dag = _load_dag(args.dag)
    waivers = [
        Waiver(step=s, identity=args.waiver_identity, reason=args.waiver_reason)
        for s in args.waive_step
    ]
    runner = WorkflowRunner(dag, waivers=waivers)

    if args.mode == "step":
        if not args.step:
            parser.error("--step is required in step mode")
        upstream = {}
        for pair in args.input:
            name, _, value = pair.partition("=")
            upstream[name] = value
        evidence = runner.run_single_step(args.step, upstream_outputs=upstream)
    else:
        evidence = runner.run()

    print(evidence.to_json())
    return 0 if evidence.ok else 1


if __name__ == "__main__":
    sys.exit(run_dag_cli())
