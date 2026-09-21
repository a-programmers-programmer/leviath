"""GQL-07 — DAG structural checks: cycles, endpoints, port types, reachability,
branch/join shape and sensitive flows.

Severities follow the published rule contract
(`docs/workflow/edge-invariant-rules.json`):

  * ACYCLIC            ERROR  — a cycle is never waivable;
  * endpoint/port-type ERROR  — a missing endpoint or an incompatible port blocks;
  * sensitive flow     ERROR  — an UNAPPROVED sensitive flow blocks; an approved
                                one is a WARN waiver carrying identity + reason.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .model import DAG, StepDef, PortBinding, PortType, PORT_TYPE_COMPAT, Waiver, WARN, ERROR


@dataclass
class CycleBlocked:
    cycle: List[str]
    severity: str = ERROR


@dataclass
class MissingEndpoint:
    message: str
    severity: str = ERROR


@dataclass
class IncompatiblePorts:
    message: str
    severity: str = ERROR


@dataclass
class DagCheckResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    cycle: Optional[CycleBlocked] = None
    endpoint: Optional[MissingEndpoint] = None
    ports: Optional[IncompatiblePorts] = None
    warnings: List[str] = field(default_factory=list)
    unreachable: List[str] = field(default_factory=list)


def build_adjacency(dag: DAG) -> Dict[str, List[str]]:
    """Build adjacency list: step -> steps it depends on."""
    adj: Dict[str, List[str]] = {}
    for step in dag.steps:
        adj.setdefault(step.name, [])
        for inp in step.inputs:
            adj[step.name].append(inp.step)
    return adj


def build_forward_adjacency(dag: DAG) -> Dict[str, List[str]]:
    """Forward edges: step -> steps that depend on it."""
    adj: Dict[str, List[str]] = {s.name: [] for s in dag.steps}
    for step in dag.steps:
        for inp in step.inputs:
            adj.setdefault(inp.step, []).append(step.name)
    return adj


def entry_step_names(dag: DAG) -> List[str]:
    """Steps with no input bindings — the places a run may start."""
    return [s.name for s in dag.steps if not s.inputs]


def check_cycles(dag: DAG) -> DagCheckResult:
    """Detect cycles in the step dependency graph. Returns CycleBlocked if found."""
    adj = build_adjacency(dag)
    # Visit in DECLARATION order, never set order: set iteration varies with
    # Python hash randomization, which would make the reported cycle path differ
    # between two runs of the same graph. Error text must be reproducible.
    names = list(dict.fromkeys(dag.step_names))

    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {n: WHITE for n in names}
    parent: Dict[str, Optional[str]] = {n: None for n in names}

    def dfs(u: str) -> Optional[List[str]]:
        color[u] = GRAY
        for v in adj.get(u, []):
            if color.get(v) == GRAY:
                # Found a cycle. Reconstruct it.
                cycle = [v, u]
                cur = u
                while parent.get(cur) and parent[cur] != v:
                    cur = parent[cur]
                    cycle.append(cur)
                cycle.append(v)
                return cycle
            if color.get(v) == WHITE:
                parent[v] = u
                result = dfs(v)
                if result is not None:
                    return result
        color[u] = BLACK
        return None

    for name in names:
        if color.get(name, BLACK) == WHITE:
            cycle = dfs(name)
            if cycle:
                return DagCheckResult(
                    ok=False,
                    errors=[f"cycle detected: {' -> '.join(cycle)}"],
                    cycle=CycleBlocked(cycle=list(reversed(cycle))),
                )
    return DagCheckResult(ok=True)


def check_endpoints(dag: DAG) -> DagCheckResult:
    """Verify every input port binding references an existing step and port."""
    step_names = set(dag.step_names)
    errors: List[str] = []
    endpoint: Optional[MissingEndpoint] = None

    for step in dag.steps:
        for inp in step.inputs:
            if inp.step not in step_names:
                msg = f"step '{step.name}' references missing endpoint '{inp.step}.{inp.port}'"
                errors.append(msg)
                if endpoint is None:
                    endpoint = MissingEndpoint(message=msg)
                continue
            # Also verify the port exists as an output on the source step
            src_step = dag.step_by_name(inp.step)
            if src_step is not None:
                output_names = {o.name for o in src_step.outputs}
                if inp.port not in output_names:
                    msg = f"step '{step.name}' references missing port '{inp.step}.{inp.port}' (not an output)"
                    errors.append(msg)
                    if endpoint is None:
                        endpoint = MissingEndpoint(message=msg)

    return DagCheckResult(
        ok=len(errors) == 0,
        errors=errors,
        endpoint=endpoint,
    )


def check_port_types(dag: DAG) -> DagCheckResult:
    """Verify typed outputs are compatible with input port bindings."""
    # Build output port type map: step_name -> port_name -> PortType
    output_types: Dict[str, Dict[str, PortType]] = defaultdict(dict)
    for step in dag.steps:
        for out in step.outputs:
            output_types[step.name][out.name] = out.type

    errors: List[str] = []
    port_err: Optional[IncompatiblePorts] = None

    for step in dag.steps:
        for inp in step.inputs:
            src_type = output_types.get(inp.step, {}).get(inp.port)
            if src_type is None:
                continue  # Already caught by check_endpoints

            # If the binding declares an expected type, check compatibility
            if inp.type != PortType.ANY and inp.type != src_type:
                if not _compatible(src_type, inp.type):
                    msg = (
                        f"port type mismatch: {inp.step}.{inp.port} ({src_type.value}) "
                        f"-> {step.name} expects ({inp.type.value})"
                    )
                    errors.append(msg)
                    if port_err is None:
                        port_err = IncompatiblePorts(message=msg)

    return DagCheckResult(
        ok=len(errors) == 0,
        errors=errors,
        ports=port_err,
    )


def _compatible(source: PortType, target: PortType) -> bool:
    """True when source type can safely feed into target type."""
    return target in PORT_TYPE_COMPAT.get(source, ())


def check_reachability(dag: DAG) -> DagCheckResult:
    """Every step must be reachable from an entry step along the edge direction."""
    forward = build_forward_adjacency(dag)
    seen: Set[str] = set()
    queue: deque[str] = deque(entry_step_names(dag))
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        for nxt in forward.get(node, []):
            if nxt not in seen:
                queue.append(nxt)

    unreachable = [s.name for s in dag.steps if s.name not in seen]
    errors = [f"step '{n}' is unreachable from any entry step" for n in unreachable]
    return DagCheckResult(ok=not unreachable, errors=errors, unreachable=unreachable)


def check_branches_joins(dag: DAG) -> DagCheckResult:
    """Check branch/join shape: a step may not bind the same source port twice."""
    errors: List[str] = []
    for step in dag.steps:
        seen: Set[Tuple[str, str]] = set()
        for inp in step.inputs:
            key = (inp.step, inp.port)
            if key in seen:
                errors.append(
                    f"ambiguous join: step '{step.name}' binds '{inp.step}.{inp.port}' more than once"
                )
            seen.add(key)
    return DagCheckResult(ok=not errors, errors=errors)


def check_sensitive_flows(
    dag: DAG, approved_steps: Optional[Set[str]] = None
) -> DagCheckResult:
    """An UNAPPROVED sensitive flow blocks; an approved one is a WARN finding.

    `approved_steps` names the steps covered by a signed waiver (identity + reason).
    """
    approved = approved_steps or set()
    errors: List[str] = []
    warnings: List[str] = []
    for step in dag.steps:
        if not step.sensitive:
            continue
        if step.name in approved:
            warnings.append(
                f"WARN: step '{step.name}' is sensitive and is covered by an approved waiver"
            )
        else:
            errors.append(
                f"unapproved sensitive flow: step '{step.name}' is sensitive and has no approved waiver"
            )
    return DagCheckResult(ok=not errors, errors=errors, warnings=warnings)


def check_dag(dag: DAG, approved_steps: Optional[Set[str]] = None) -> DagCheckResult:
    """Full structural check: cycles, endpoints, ports, reachability, joins, sensitive flows."""
    results: List[DagCheckResult] = [
        check_cycles(dag),
        check_endpoints(dag),
        check_port_types(dag),
        check_reachability(dag),
        check_branches_joins(dag),
        check_sensitive_flows(dag, approved_steps),
    ]
    combined = DagCheckResult(ok=True)
    for r in results:
        if not r.ok:
            combined.ok = False
        combined.errors.extend(r.errors)
        combined.warnings.extend(r.warnings)
        combined.unreachable.extend(r.unreachable)
        if r.cycle:
            combined.cycle = r.cycle
        if r.endpoint:
            combined.endpoint = r.endpoint
        if r.ports:
            combined.ports = r.ports
    return combined
