#!/usr/bin/env python3
"""Trusted, stdlib-only workspace kernel for the bundled Oracle agent."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

MAX_PACKET = 1_400
MAX_DOSSIER = 4_400
MAX_REQUEST = 524_288
MAX_PATCH = 262_144
MAX_OUTPUT = 65_536
MAX_VISITS = 8
MAX_WAVES = 8
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class KernelError(Exception):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def bound(value: Any, limit: int, label: str) -> None:
    size = len(canonical(value))
    if size > limit:
        raise KernelError(f"{label} exceeds {limit} UTF-8 bytes ({size})")


def git(repo: Path, *args: str, check: bool = True) -> bytes:
    proc = subprocess.run(["git", *args], cwd=repo, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=20)
    if check and proc.returncode:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:500]
        raise KernelError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def repo_root() -> Path:
    try:
        return Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip()).resolve()
    except (OSError, subprocess.TimeoutExpired, KernelError) as exc:
        raise KernelError(f"workspace is not a usable git repository: {exc}")


def store_root(repo: Path) -> Path:
    path = Path(git(repo, "rev-parse", "--git-path", "leviath-oracle").decode().strip())
    if not path.is_absolute():
        path = repo / path
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def atomic_json(path: Path, value: Any, immutable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(value) + b"\n"
    if immutable:
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise KernelError(f"immutable record already exists: {path.name}")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def read_json(path: Path) -> Any:
    try:
        with path.open("rb") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise KernelError(f"cannot read trusted state {path.name}: {exc}")


@contextlib.contextmanager
def run_lock(run_dir: Path, timeout: float = 10.0) -> Iterator[None]:
    lock = run_dir / ".lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock.mkdir(mode=0o700)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 120:
                    lock.rmdir()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise KernelError("workspace ledger is busy")
            time.sleep(0.025)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock.rmdir()


def validate_run_id(value: Any) -> str:
    if not isinstance(value, str):
        raise KernelError("run_id is required")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise KernelError("invalid run_id")
    if str(parsed) != value.lower():
        raise KernelError("run_id must be a canonical UUID")
    return value.lower()


def run_dir_for(repo: Path, value: Any) -> Path:
    path = store_root(repo) / validate_run_id(value)
    if not path.is_dir():
        raise KernelError("unknown run_id")
    return path


def validate_id(value: Any, label: str = "id") -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise KernelError(f"invalid {label}")
    return value


def relative_path(repo: Path, value: Any, existing: bool = False) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise KernelError("path must be a non-empty repository-relative POSIX path")
    if value == ".":
        return "."
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise KernelError(f"unsafe path: {value}")
    if pure.parts[0] == ".git":
        raise KernelError("git internal paths are protected")
    candidate = repo.joinpath(*pure.parts)
    cursor = repo
    parts = pure.parts if existing else pure.parts[:-1]
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise KernelError(f"symlink traversal refused: {value}")
    if existing and not candidate.exists():
        raise KernelError(f"path does not exist: {value}")
    try:
        candidate.resolve(strict=False).relative_to(repo)
    except ValueError:
        raise KernelError(f"path escapes repository: {value}")
    if candidate.is_symlink():
        raise KernelError(f"symlink target refused: {value}")
    return pure.as_posix()


def file_sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise KernelError(f"not a regular file: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(131072), b""):
            digest.update(chunk)
    return digest.hexdigest()


def listed_paths(repo: Path) -> Tuple[List[str], Dict[str, str]]:
    stage = git(repo, "ls-files", "-s", "-z")
    modes: Dict[str, str] = {}
    for entry in stage.split(b"\0"):
        if not entry:
            continue
        head, raw_path = entry.split(b"\t", 1)
        mode = head.split(b" ", 1)[0].decode("ascii", "replace")
        name = raw_path.decode("utf-8", "surrogateescape")
        modes[name] = mode
        if mode == "160000":
            raise KernelError(f"submodule cannot be bound honestly: {name}")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    names = list(modes)
    names.extend(x.decode("utf-8", "surrogateescape") for x in untracked.split(b"\0") if x)
    return sorted(set(names)), modes


def workspace_snapshot(repo: Path, include_map: bool = False) -> Dict[str, Any]:
    names, modes = listed_paths(repo)
    entries: Dict[str, str] = {}
    digest = hashlib.sha256()
    head = git(repo, "rev-parse", "--verify", "HEAD", check=False).decode().strip() or "UNBORN"
    index = git(repo, "ls-files", "-s", "-z")
    digest.update(b"head\0" + head.encode() + b"\0index\0" + hashlib.sha256(index).digest())
    for name in names:
        rel = relative_path(repo, name)
        target = repo / rel
        if target.is_symlink():
            raise KernelError(f"symlink cannot be bound honestly: {name}")
        if not target.exists():
            marker, working_mode = "deleted", "missing"
        elif not target.is_file():
            raise KernelError(f"non-regular workspace path refused: {name}")
        else:
            marker = file_sha(target)
            working_mode = oct(target.stat().st_mode & 0o777)
        entries[rel] = f"{working_mode}:{marker}"
        digest.update(rel.encode("utf-8", "surrogateescape") + b"\0" +
                      modes.get(name, "untracked").encode() + b"\0" + working_mode.encode() +
                      b"\0" + marker.encode() + b"\0")
    env = {"platform": platform.platform(), "python": platform.python_version()}
    digest.update(canonical(env))
    result: Dict[str, Any] = {"id": digest.hexdigest(), "head": head,
                              "environment": env, "files_count": len(entries)}
    if include_map:
        result["files"] = entries
    return result


def bound_snapshot(repo: Path, state: Mapping[str, Any]) -> Dict[str, Any]:
    """Bind workspace bytes, environment, and exact approved command identities."""
    base = workspace_snapshot(repo)
    commands = []
    for oid, meta in sorted(state.get("orders", {}).items()):
        if meta["role"] != "coder":
            continue
        for check in meta.get("checks", []):
            commands.append({"order_id": oid, "id": check["id"], "argv": check["argv"],
                             "cwd": check["cwd"], "expect_tests": check["expect_tests"]})
    workspace_id = base["id"]
    base["workspace_id"] = workspace_id
    base["approved_commands_sha256"] = hashlib.sha256(canonical(commands)).hexdigest()
    base["id"] = hashlib.sha256(canonical({"workspace": workspace_id,
                                            "approved_commands": commands})).hexdigest()
    return base


def state_load(run_dir: Path) -> Dict[str, Any]:
    return read_json(run_dir / "ledger.json")


def state_save(run_dir: Path, state: Dict[str, Any]) -> None:
    state["updated_at"] = int(time.time())
    atomic_json(run_dir / "ledger.json", state)


def order_load(run_dir: Path, oid: str) -> Dict[str, Any]:
    return read_json(run_dir / "orders" / f"{validate_id(oid, 'order_id')}.json")


def write_order(run_dir: Path, state: Dict[str, Any], order: Dict[str, Any]) -> None:
    oid = validate_id(order["id"], "order id")
    if oid in state["orders"]:
        raise KernelError(f"duplicate order id: {oid}")
    atomic_json(run_dir / "orders" / f"{oid}.json", order, immutable=True)
    state["orders"][oid] = {"role": order["role"], "status": "pending", "receipts": [],
                            "depends_on": order.get("depends_on", []),
                            "checks": order.get("checks", []), "decision": order.get("decision"),
                            "remediation_group": order.get("remediation_group"),
                            "supersedes": order.get("supersedes", [])}


def receipt_write(run_dir: Path, state: Dict[str, Any], oid: str,
                  action: str, result: Dict[str, Any]) -> Dict[str, Any]:
    rid = f"r-{len(state['receipt_index']) + 1:05d}-{uuid.uuid4().hex[:10]}"
    record = {"receipt_id": rid, "run_id": state["run_id"], "order_id": oid,
              "action": action, "result": result, "created_at": int(time.time())}
    digest = hashlib.sha256(canonical(record)).hexdigest()
    atomic_json(run_dir / "receipts" / f"{rid}.json", record, immutable=True)
    state["receipt_index"][rid] = {"order_id": oid, "sha256": digest, "action": action}
    state["orders"][oid]["receipts"].append(rid)
    return record


def attempt_signature(action: str, request: Mapping[str, Any]) -> str:
    identity = {"action": action}
    for key in ("path", "check_id", "kind"):
        if key in request:
            identity[key] = request[key]
    return hashlib.sha256(canonical(identity)).hexdigest()


def verified_receipts(run_dir: Path, state: Mapping[str, Any], oid: str) -> List[Dict[str, Any]]:
    result = []
    for rid in state["orders"][oid]["receipts"]:
        meta = state["receipt_index"].get(rid)
        if not meta or meta.get("order_id") != oid:
            raise KernelError("receipt ledger inconsistency")
        record = read_json(run_dir / "receipts" / f"{rid}.json")
        if hashlib.sha256(canonical(record)).hexdigest() != meta.get("sha256"):
            raise KernelError(f"receipt authentication failed: {rid}")
        result.append(record)
    return result


def artifact_write(run_dir: Path, data: bytes) -> Dict[str, Any]:
    digest = hashlib.sha256(data).hexdigest()
    path = run_dir / "artifacts" / digest
    if not path.exists():
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        except FileExistsError:
            pass
    return {"sha256": digest, "bytes": len(data)}


def envelope(state: Mapping[str, Any], route: str, items: List[Dict[str, Any]],
             dossier: Dict[str, Any], report: str) -> Dict[str, Any]:
    bound(dossier, MAX_DOSSIER, "dossier")
    return {"ok": True, "run_id": state["run_id"], "route": route,
            "items": items, "dossier": dossier, "report": report}


def accepted_report(state: Mapping[str, Any], snapshot_id: str) -> str:
    completed = [oid for oid, meta in state["orders"].items()
                 if meta["role"] == "coder" and meta["status"] in ("approved", "superseded")]
    check_count = sum(len(meta.get("checks", [])) for meta in state["orders"].values()
                      if meta["role"] == "coder" and meta["status"] == "approved")
    return (f"Accepted snapshot {snapshot_id}. Completed orders: " + ", ".join(completed) +
            f". Independent approved checks: {check_count}.")


def dispatch(state: Mapping[str, Any], ids: Sequence[str]) -> List[Dict[str, Any]]:
    return [{"id": oid, "context": {"run_id": state["run_id"], "order_id": oid,
                                      "role": state["orders"][oid]["role"]}} for oid in ids]


def bootstrap(repo: Path, request: Mapping[str, Any]) -> Dict[str, Any]:
    task = request.get("task", request.get("objective"))
    if not isinstance(task, str) or not task.strip() or len(task.encode()) > 800:
        raise KernelError("bootstrap requires a bounded non-empty task")
    run_id = str(uuid.uuid4())
    run_dir = store_root(repo) / run_id
    run_dir.mkdir(mode=0o700)
    for name in ("orders", "receipts", "artifacts"):
        (run_dir / name).mkdir(mode=0o700)
    snapshot = workspace_snapshot(repo)
    state = {"version": 1, "run_id": run_id, "task": task.strip(), "status": "active",
             "route": "readers", "visits": 0, "wave": 0, "orders": {}, "receipt_index": {},
             "snapshot": snapshot, "accepted_snapshot": None, "unresolved": [],
             "resolved_unresolved": [], "resolution_history": [],
             "created_at": int(time.time()), "updated_at": int(time.time())}
    order = {"id": "read-001", "role": "reader", "objective": task.strip(), "paths": ["."],
             "depends_on": [], "checks": [], "created_snapshot": snapshot["id"]}
    bound(order, 800, "initial work order")
    write_order(run_dir, state, order)
    state["snapshot"] = bound_snapshot(repo, state)
    snapshot = state["snapshot"]
    atomic_json(run_dir / "run.json", {"run_id": run_id, "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
                                        "created_at": state["created_at"]}, immutable=True)
    state_save(run_dir, state)
    dossier = {"snapshot_id": snapshot["id"], "objective": task.strip(),
               "requested": ["bounded inventory", "exact-line evidence", "negative evidence/conflicts"]}
    return envelope(state, "readers", dispatch(state, ["read-001"]), dossier,
                    "Initial evidence order is ready.")


def normalize_paths(repo: Path, raw: Any) -> List[str]:
    if not isinstance(raw, list) or not raw or len(raw) > 32:
        raise KernelError("order paths must contain 1..32 exact paths")
    paths = [relative_path(repo, value) for value in raw]
    if len(set(paths)) != len(paths):
        raise KernelError("duplicate order path")
    return paths


def normalize_checks(repo: Path, raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 16:
        raise KernelError("checks must be a list of at most 16 commands")
    checks, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise KernelError("invalid check")
        cid = validate_id(item.get("id"), "check id")
        if cid in seen:
            raise KernelError(f"duplicate check id: {cid}")
        seen.add(cid)
        argv = item.get("argv")
        if (not isinstance(argv, list) or not argv or len(argv) > 32 or
                any(not isinstance(arg, str) or not arg or "\x00" in arg or len(arg) > 1000
                    for arg in argv)):
            raise KernelError(f"check {cid} has invalid argv")
        cwd = item.get("cwd", ".")
        if cwd != ".":
            cwd = relative_path(repo, cwd, existing=True)
            if not (repo / cwd).is_dir():
                raise KernelError(f"check {cid} cwd is not a directory")
        checks.append({"id": cid, "argv": argv, "cwd": cwd,
                       "expect_tests": bool(item.get("expect_tests", False))})
    return checks


def graph_valid(state: Mapping[str, Any], orders: Sequence[Mapping[str, Any]]) -> None:
    known = set(state["orders"])
    incoming = {str(order["id"]): list(order["depends_on"]) for order in orders}
    all_ids = known | set(incoming)
    for oid, deps in incoming.items():
        if oid in deps:
            raise KernelError(f"order {oid} depends on itself")
        missing = [dep for dep in deps if dep not in all_ids]
        if missing:
            raise KernelError(f"order {oid} has unknown dependencies: {', '.join(missing)}")
    visiting, visited = set(), set()

    def visit(node: str) -> None:
        if node in known or node in visited:
            return
        if node in visiting:
            raise KernelError("order dependency graph contains a cycle")
        visiting.add(node)
        for dependency in incoming.get(node, []):
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in incoming:
        visit(node)


def ready_coders(state: Mapping[str, Any]) -> List[str]:
    ready = []
    for oid, meta in state["orders"].items():
        if meta["role"] != "coder" or meta["status"] != "pending":
            continue
        allowed = ("approved", "sealed", "superseded")
        if meta.get("decision") == "REMEDIATE":
            allowed += ("needs_remediation", "failed")
        if all(state["orders"].get(dep, {}).get("status") in allowed for dep in meta["depends_on"]):
            ready.append(oid)
    return ready


def control(repo: Path, request: Mapping[str, Any]) -> Dict[str, Any]:
    run_dir = run_dir_for(repo, request.get("run_id"))
    decision = request.get("decision")
    if not isinstance(decision, dict):
        raise KernelError("control requires decision object")
    kind = str(decision.get("action", "")).upper()
    with run_lock(run_dir):
        state = state_load(run_dir)
        if state["status"] != "active":
            raise KernelError(f"run is already {state['status']}")
        state["visits"] += 1
        if state["visits"] > MAX_VISITS:
            state["status"] = "stopped"
            state["unresolved"].append("Oracle visit limit exceeded")
            state_save(run_dir, state)
            return envelope(state, "report", [], dossier_for(run_dir, state),
                            "Stopped after eight Oracle decisions.")
        current = bound_snapshot(repo, state)
        if kind in ("AUTHORIZE", "REMEDIATE", "REQUEST_EVIDENCE"):
            if decision.get("snapshot_id") != current["id"] or current["id"] != state["snapshot"]["id"]:
                raise KernelError(f"{kind} snapshot_id is stale or missing")
        if kind in ("AUTHORIZE", "REMEDIATE"):
            if state["wave"] >= MAX_WAVES:
                raise KernelError("coding wave limit exceeded")
            raw_orders = decision.get("orders")
            if not isinstance(raw_orders, list) or not raw_orders or len(raw_orders) > 32:
                raise KernelError(f"{kind} requires 1..32 orders")
            supersedes: List[str] = []
            remediation_reason: Optional[str] = None
            remediation_group: Optional[str] = None
            if kind == "REMEDIATE":
                raw_supersedes = decision.get("supersedes")
                remediation_reason = decision.get("reason")
                if (not isinstance(raw_supersedes, list) or not raw_supersedes or
                        len(raw_supersedes) > 16 or not isinstance(remediation_reason, str) or
                        not remediation_reason.strip() or len(remediation_reason.encode()) > 600):
                    raise KernelError("REMEDIATE requires bounded supersedes and reason")
                supersedes = [validate_id(value, "superseded order id") for value in raw_supersedes]
                if len(set(supersedes)) != len(supersedes):
                    raise KernelError("duplicate superseded order id")
                invalid = [oid for oid in supersedes if state["orders"].get(oid, {}).get("role") != "coder" or
                           state["orders"].get(oid, {}).get("status") not in ("needs_remediation", "failed")]
                if invalid:
                    raise KernelError("REMEDIATE supersedes only failed coding orders: " + ", ".join(invalid))
                remediation_group = f"remediation-{uuid.uuid4().hex[:12]}"
            normalized, ids = [], set(state["orders"])
            for raw in raw_orders:
                if not isinstance(raw, dict):
                    raise KernelError("invalid order")
                oid = validate_id(raw.get("id"), "order id")
                if oid in ids:
                    raise KernelError(f"duplicate order id: {oid}")
                ids.add(oid)
                objective = raw.get("objective")
                if not isinstance(objective, str) or not objective.strip() or len(objective.encode()) > 600:
                    raise KernelError(f"order {oid} has invalid objective")
                dependencies = raw.get("depends_on", [])
                if not isinstance(dependencies, list) or len(dependencies) > 32:
                    raise KernelError(f"order {oid} has invalid dependencies")
                dependencies = [validate_id(x, "dependency id") for x in dependencies]
                checks = normalize_checks(repo, raw.get("checks", []))
                if not checks:
                    raise KernelError(f"order {oid} requires at least one independent check")
                normalized.append({"id": oid, "role": "coder", "objective": objective.strip(),
                                   "paths": normalize_paths(repo, raw.get("paths")),
                                   "depends_on": dependencies,
                                   "checks": checks,
                                   "created_snapshot": current["id"], "decision": kind,
                                   "remediation_group": remediation_group,
                                   "supersedes": supersedes,
                                   "remediation_reason": remediation_reason.strip() if remediation_reason else None})
            graph_valid(state, normalized)
            for order in normalized:
                bound(order, 800, f"work order {order['id']}")
                write_order(run_dir, state, order)
            state["snapshot"] = bound_snapshot(repo, state)
            state["wave"] += 1
            ready = ready_coders(state)[:1]
            route = "coders" if ready else "oracle"
            state["route"] = route
            state_save(run_dir, state)
            return envelope(state, route, dispatch(state, ready), dossier_for(run_dir, state),
                            "Authorized exact-path coding orders." if ready else
                            "No coding order is dependency-ready.")
        if kind == "REQUEST_EVIDENCE":
            raw_orders = decision.get("orders")
            if raw_orders is None:
                raw_orders = [{"id": decision.get("id", f"read-{len(state['orders']) + 1:03d}"),
                               "objective": decision.get("objective"), "paths": decision.get("paths")}]
            if not isinstance(raw_orders, list) or not raw_orders or len(raw_orders) > 4:
                raise KernelError("REQUEST_EVIDENCE requires 1..4 orders")
            ids, normalized_evidence, seen = [], [], set(state["orders"])
            for raw in raw_orders:
                if not isinstance(raw, dict):
                    raise KernelError("invalid evidence order")
                oid = validate_id(raw.get("id"), "order id")
                if oid in seen:
                    raise KernelError(f"duplicate order id: {oid}")
                seen.add(oid)
                objective = raw.get("objective")
                if (not isinstance(objective, str) or not objective.strip() or
                        len(objective.encode()) > 600):
                    raise KernelError(f"order {oid} has invalid objective")
                order = {"id": oid, "role": "reader", "objective": objective.strip(),
                         "paths": normalize_paths(repo, raw.get("paths")), "depends_on": [], "checks": [],
                         "created_snapshot": current["id"]}
                bound(order, 800, f"work order {oid}")
                normalized_evidence.append(order)
                ids.append(oid)
            for order in normalized_evidence:
                write_order(run_dir, state, order)
            state["route"] = "readers"
            state_save(run_dir, state)
            return envelope(state, "readers", dispatch(state, ids), dossier_for(run_dir, state),
                            "Additional evidence orders are ready.")
        if kind == "ACCEPT":
            current = bound_snapshot(repo, state)
            if decision.get("snapshot_id") != current["id"] or current["id"] != state["snapshot"]["id"]:
                raise KernelError("ACCEPT snapshot_id is stale or missing")
            code = [oid for oid, meta in state["orders"].items() if meta["role"] == "coder"]
            if not code:
                raise KernelError("cannot ACCEPT without an authorized coding order")
            current_workspace = current["workspace_id"]
            incomplete = [oid for oid in code
                          if state["orders"][oid]["status"] not in ("approved", "superseded") or
                          (state["orders"][oid]["status"] == "approved" and
                           state["orders"][oid].get("verified_workspace_id") != current_workspace)]
            if incomplete:
                raise KernelError("cannot ACCEPT: unapproved orders: " + ", ".join(incomplete))
            dossier = dossier_for(run_dir, state)
            if dossier["unresolved"] or dossier["coverage_gaps"]:
                raise KernelError("cannot ACCEPT with unresolved execution failures or coverage gaps")
            affected = {value.split(":", 1)[0] for value in
                        dossier["contradictions"] + dossier["unread"]}
            raw_dispositions = decision.get("dispositions", [])
            if not isinstance(raw_dispositions, list) or len(raw_dispositions) > 16:
                raise KernelError("ACCEPT dispositions must be a list of at most 16 entries")
            dispositions, disposed = [], set()
            for item in raw_dispositions:
                if not isinstance(item, dict):
                    raise KernelError("invalid ACCEPT disposition")
                oid = validate_id(item.get("order_id"), "disposition order_id")
                reason = item.get("reason")
                if (oid in disposed or oid not in affected or not isinstance(reason, str) or
                        not reason.strip() or len(reason.encode()) > 500):
                    raise KernelError("disposition must uniquely address an order with unread or contradictory evidence and give a bounded reason")
                if state["orders"].get(oid, {}).get("status") in ("pending", "working"):
                    raise KernelError(f"cannot disposition nonterminal order: {oid}")
                disposed.add(oid)
                dispositions.append({"order_id": oid, "reason": reason.strip()})
            missing_dispositions = sorted(affected - disposed)
            if missing_dispositions:
                raise KernelError("ACCEPT requires dispositions for: " + ", ".join(missing_dispositions))
            if dispositions:
                state["resolution_history"].append({"type": "oracle_disposition",
                                                    "snapshot_id": current["id"],
                                                    "dispositions": dispositions})
            state["status"], state["route"] = "accepted", "report"
            state["accepted_snapshot"] = current["id"]
            state["snapshot"] = current
            state_save(run_dir, state)
            return envelope(state, "report", [], dossier_for(run_dir, state),
                            accepted_report(state, current["id"]))
        if kind in ("ASK_USER", "STOP"):
            reason = decision.get("reason", decision.get("question", "unresolved by Oracle"))
            if not isinstance(reason, str):
                reason = "unresolved by Oracle"
            state["unresolved"].append(reason[:2000])
            state["status"] = "ask_user" if kind == "ASK_USER" else "stopped"
            state["route"] = "report"
            state_save(run_dir, state)
            return envelope(state, "report", [], dossier_for(run_dir, state), reason[:2000])
        raise KernelError("decision.action must be REQUEST_EVIDENCE, AUTHORIZE, REMEDIATE, ACCEPT, ASK_USER, or STOP")


def allowed_path(repo: Path, order: Mapping[str, Any], value: Any) -> str:
    rel = relative_path(repo, value)
    allowed = order.get("paths", [])
    if allowed != ["."] and rel not in allowed:
        raise KernelError(f"path is not exactly authorized by order: {rel}")
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel], cwd=repo,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    ignored = subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=repo,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not tracked and ignored:
        raise KernelError(f"ignored untracked path is outside the bound workspace: {rel}")
    return rel


def inspect_action(repo: Path, order: Mapping[str, Any], action: Mapping[str, Any]) -> Dict[str, Any]:
    kind = action.get("kind", action.get("type", "inventory"))
    if kind == "order":
        return {"work_order": order}
    if kind == "file":
        rel = allowed_path(repo, order, action.get("path"))
        target = repo / rel
        data = target.read_bytes() if target.exists() else b""
        if len(data) > 32_768:
            raise KernelError("file inspection exceeds 32768-byte bound")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            raise KernelError(f"file inspection requires UTF-8 text: {rel}")
        return {"kind": "file", "path": rel, "content": content,
                "file_sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    if kind == "diff":
        raw = git(repo, "status", "--short", "--untracked-files=all")
        text = raw[:700].decode("utf-8", "replace")
        return {"kind": "diff", "work_order": order, "status": text,
                "truncated": len(raw) > 700}
    names, _ = listed_paths(repo)
    if order.get("paths") != ["."]:
        names = [path for path in names if path in order.get("paths", [])]
    return {"kind": "inventory", "work_order": order, "paths": names[:20], "total": len(names),
            "truncated": len(names) > 20}


def read_action(repo: Path, order: Mapping[str, Any], action: Mapping[str, Any]) -> Dict[str, Any]:
    rel = allowed_path(repo, order, action.get("path"))
    start, end = action.get("start"), action.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end - start >= 80:
        raise KernelError("read requires an inclusive line range of at most 80 lines")
    data = (repo / rel).read_bytes()
    if len(data) > 8_000_000:
        raise KernelError("file is too large for evidence read")
    lines = data.decode("utf-8", "replace").splitlines()
    if start > len(lines):
        raise KernelError(f"read start {start} is beyond end of file ({len(lines)} lines)")
    quote = "\n".join(f"{start + i}: {line}" for i, line in enumerate(lines[start - 1:end]))
    if len(quote.encode()) > 320:
        raise KernelError("citation exceeds conservative 80-token/320-byte cap; narrow the range")
    return {"path": rel, "start": start, "end": min(end, len(lines)), "quote": quote,
            "file_sha256": hashlib.sha256(data).hexdigest()}


def patch_action(repo: Path, order: Mapping[str, Any], action: Mapping[str, Any]) -> Dict[str, Any]:
    rel = allowed_path(repo, order, action.get("path"))
    before, content = action.get("before_sha256"), action.get("content")
    if not isinstance(before, str) or not SHA_RE.fullmatch(before):
        raise KernelError("patch requires before_sha256")
    if not isinstance(content, str) or len(content.encode()) > MAX_PATCH:
        raise KernelError("patch content must be bounded UTF-8 text")
    target, data = repo / rel, content.encode()
    preserved_mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    actual = file_sha(target) if target.exists() else hashlib.sha256(b"").hexdigest()
    if actual != before:
        raise KernelError(f"CAS mismatch for {rel}: expected {before}, found {actual}")
    target.parent.mkdir(parents=True, exist_ok=True)
    relative_path(repo, rel)
    fd, temporary = tempfile.mkstemp(prefix=".oracle-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, preserved_mode)
        os.replace(temporary, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
    return {"path": rel, "before_sha256": before,
            "after_sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def parse_test_count(output: str) -> Optional[int]:
    patterns = [r"(?m)(\d+)\s+passed(?:,|\s|$)",
                r"(?m)test result: .*?\b(\d+) passed",
                r"(?m)^\s*(?:Tests|tests)\s*[: ]\s*(\d+)\s+passed",
                r"(?m)^# tests\s+(\d+)\s*$",
                r"(?m)^Ran\s+(\d+)\s+tests?\b"]
    values: List[int] = []
    for pattern in patterns:
        values.extend(int(value) for value in re.findall(pattern, output))
    return max(values) if values else None


def changed_outside(before: Mapping[str, Any], after: Mapping[str, Any],
                    allowed: Iterable[str]) -> List[str]:
    permitted = set(allowed)
    left, right = before.get("files", {}), after.get("files", {})
    return sorted(path for path in set(left) | set(right)
                  if left.get(path) != right.get(path) and path not in permitted)


def changed_paths(before: Mapping[str, Any], after: Mapping[str, Any]) -> List[str]:
    left, right = before.get("files", {}), after.get("files", {})
    return sorted(path for path in set(left) | set(right) if left.get(path) != right.get(path))


def check_action(repo: Path, run_dir: Path, order: Mapping[str, Any],
                 action: Mapping[str, Any]) -> Dict[str, Any]:
    cid = validate_id(action.get("check_id"), "check_id")
    matches = [item for item in order.get("checks", []) if item["id"] == cid]
    if len(matches) != 1:
        raise KernelError("check_id is not approved for this verifier")
    check = matches[0]
    before = workspace_snapshot(repo, include_map=True)
    cwd = repo if check["cwd"] == "." else repo / check["cwd"]
    try:
        proc = subprocess.run(check["argv"], cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=60, env=os.environ.copy())
        raw, timed_out, exit_code = proc.stdout, False, proc.returncode
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or b"") + (exc.stderr or b"")
        timed_out, exit_code = True, None
    except OSError as exc:
        raw, timed_out, exit_code = str(exc).encode(), False, None
    artifact = artifact_write(run_dir, raw)
    after = workspace_snapshot(repo, include_map=True)
    changed = changed_paths(before, after)
    outside = changed_outside(before, after, order.get("paths", []))
    text = raw[:MAX_OUTPUT].decode("utf-8", "replace")
    tests = parse_test_count(text)
    incomplete = timed_out or exit_code is None or exit_code != 0 or bool(changed)
    if check["expect_tests"] and (tests is None or tests == 0):
        incomplete = True
    return {"check_id": cid,
            "argv_sha256": hashlib.sha256(canonical(check["argv"])).hexdigest(),
            "exit_code": exit_code, "timed_out": timed_out, "tests_collected": tests,
            "expect_tests": check["expect_tests"], "incomplete": incomplete,
            "changed_worktree": changed, "changed_outside_approved": outside,
            "output_artifact": artifact,
            "output_preview": text[:300], "output_truncated": len(raw) > MAX_OUTPUT,
            "before_snapshot_id": before["id"], "after_snapshot_id": after["id"]}


def seal_action(order: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]],
                action: Mapping[str, Any]) -> Dict[str, Any]:
    summary = action.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary.encode()) > 1000:
        raise KernelError("seal requires a bounded non-empty summary")
    result: Dict[str, Any] = {"summary": summary.strip()}
    for key in ("negative", "contradictions", "unread"):
        value = action.get(key, [])
        if (not isinstance(value, list) or len(value) > 16 or
                any(not isinstance(item, str) or len(item.encode()) > 400 for item in value)):
            raise KernelError(f"seal {key} must be a bounded string list")
        result[key] = value
    actions = [receipt["action"] for receipt in receipts]
    if order["id"] == "read-001" and not any(
            receipt["action"] == "inspect" and receipt["result"].get("kind") == "inventory"
            for receipt in receipts):
        raise KernelError("initial reader must inspect the repository inventory before sealing")
    if order["role"] == "reader" and not any(value in actions for value in ("read", "inspect")):
        raise KernelError("reader cannot seal without authenticated evidence")
    if order["role"] == "coder" and "patch" not in actions:
        raise KernelError("coder cannot seal without an authenticated patch")
    if order["role"] == "verifier":
        expected = {check["id"] for check in order.get("checks", [])}
        seen = {receipt["result"].get("check_id") for receipt in receipts
                if receipt["action"] == "check"}
        if seen != expected:
            raise KernelError("verifier cannot seal before every approved check ran")
    return result


def worker(repo: Path, request: Mapping[str, Any]) -> Dict[str, Any]:
    run_dir = run_dir_for(repo, request.get("run_id"))
    oid = validate_id(request.get("order_id"), "order_id")
    name = request.get("action")
    if not isinstance(name, str):
        raise KernelError("worker requires a string action")
    name = name.lower()
    action = dict(request)
    with run_lock(run_dir):
        state = state_load(run_dir)
        if state["status"] != "active":
            raise KernelError(f"run is {state['status']}")
        if oid not in state["orders"]:
            raise KernelError("unknown order_id")
        meta = state["orders"][oid]
        if meta["status"] == "sealed":
            raise KernelError("order is already sealed")
        if meta["status"] not in ("pending", "working"):
            raise KernelError(f"order cannot accept actions in status {meta['status']}")
        order = order_load(run_dir, oid)
        permitted = {"reader": {"inspect", "read", "seal"},
                     "coder": {"inspect", "read", "patch", "seal"},
                     "verifier": {"inspect", "read", "check", "seal"}}[order["role"]]
        if name not in permitted:
            raise KernelError(f"{order['role']} may not perform {name or 'empty action'}")
        receipts = verified_receipts(run_dir, state, oid)
        signature = attempt_signature(name, action)
        try:
            if name == "inspect":
                result = inspect_action(repo, order, action)
            elif name == "read":
                result = read_action(repo, order, action)
            elif name == "patch":
                result = patch_action(repo, order, action)
            elif name == "check":
                result = check_action(repo, run_dir, order, action)
            else:
                result = seal_action(order, receipts, action)
            result_limit = 36_000 if name == "inspect" and action.get("kind") == "file" else 1_000
            bound(result, result_limit, "action result")
        except (KernelError, OSError, subprocess.TimeoutExpired) as exc:
            failed = {"attempted_action": name, "signature": signature,
                      "error": str(exc)[:500], "incomplete": True}
            record = receipt_write(run_dir, state, oid, f"{name}_error", failed)
            message = f"{oid}:{record['receipt_id']}: {name} failed: {str(exc)[:300]}"
            state["unresolved"].append(message)
            state.setdefault("failed_attempts", {})[record["receipt_id"]] = {
                "signature": signature, "message": message, "resolved": False}
            meta["status"] = "working"
            state["snapshot"] = bound_snapshot(repo, state)
            state_save(run_dir, state)
            raise
        record = receipt_write(run_dir, state, oid, name, result)
        for failure in state.get("failed_attempts", {}).values():
            if failure["signature"] == signature and not failure["resolved"]:
                failure["resolved"] = True
                state["resolved_unresolved"].append(failure["message"])
        meta["status"] = "sealed" if name == "seal" else "working"
        state["snapshot"] = bound_snapshot(repo, state)
        state_save(run_dir, state)
        response = {"ok": True, "run_id": state["run_id"], "order_id": oid,
                    "receipt_id": record["receipt_id"], "result": result,
                    "report": f"Authenticated {name} receipt recorded."}
        response_limit = 40_000 if name == "inspect" and action.get("kind") == "file" else MAX_PACKET
        bound(response, response_limit, "worker packet")
        return response


def make_verifiers(repo: Path, run_dir: Path, state: Dict[str, Any]) -> List[str]:
    made = []
    current_workspace = workspace_snapshot(repo)["id"]
    active = {meta.get("source_order") for meta in state["orders"].values()
              if meta["role"] == "verifier" and meta["status"] in ("pending", "working", "sealed")}
    for oid, meta in list(state["orders"].items()):
        needs_check = meta["status"] == "sealed" or (
            meta["status"] == "approved" and meta.get("verified_workspace_id") != current_workspace)
        if meta["role"] != "coder" or not needs_check or oid in active:
            continue
        order = order_load(run_dir, oid)
        if not order.get("checks"):
            meta["status"] = "needs_remediation"
            state["unresolved"].append(f"{oid}: no independent checks authorized")
            continue
        attempt = 1 + sum(1 for value in state["orders"].values()
                          if value.get("source_order") == oid)
        vid = f"verify-{oid}-{attempt}"
        if len(vid) > 64 or not ID_RE.fullmatch(vid):
            vid = f"verify-{hashlib.sha256(f'{oid}:{attempt}'.encode()).hexdigest()[:16]}"
        verifier = {"id": vid, "role": "verifier", "objective": f"Independently verify {oid}",
                    "paths": order["paths"], "depends_on": [oid], "checks": order["checks"],
                    "created_snapshot": workspace_snapshot(repo)["id"], "source_order": oid}
        bound(verifier, 800, f"work order {vid}")
        write_order(run_dir, state, verifier)
        state["orders"][vid]["source_order"] = oid
        made.append(vid)
    return made


def assess_verifiers(repo: Path, run_dir: Path, state: Dict[str, Any]) -> None:
    current_workspace = workspace_snapshot(repo)["id"]
    for vid, meta in state["orders"].items():
        if meta["role"] != "verifier" or meta["status"] != "sealed" or meta.get("assessed"):
            continue
        receipts = verified_receipts(run_dir, state, vid)
        checks = [receipt["result"] for receipt in receipts if receipt["action"] == "check"]
        source = meta.get("source_order")
        passed = bool(checks) and all(not check.get("incomplete", True) and
                                      check.get("before_snapshot_id") == current_workspace and
                                      check.get("after_snapshot_id") == current_workspace
                                      for check in checks)
        meta["status"] = "approved" if passed else "failed"
        meta["assessed"] = True
        if source in state["orders"]:
            state["orders"][source]["status"] = "approved" if passed else "needs_remediation"
            if passed:
                state["orders"][source]["verified_workspace_id"] = current_workspace
        if not passed:
            state["unresolved"].append(f"{source}: independent verification failed or was incomplete")
    groups = {meta.get("remediation_group") for meta in state["orders"].values()
              if meta.get("remediation_group")}
    resolved_groups = {entry.get("group") for entry in state.get("resolution_history", [])
                       if entry.get("group")}
    for group in groups - resolved_groups:
        replacements = [oid for oid, meta in state["orders"].items()
                        if meta.get("remediation_group") == group]
        if not replacements or not all(state["orders"][oid]["status"] == "approved" and
                                       state["orders"][oid].get("verified_workspace_id") == current_workspace
                                       for oid in replacements):
            continue
        first = order_load(run_dir, replacements[0])
        supersedes = first.get("supersedes", [])
        for old in supersedes:
            state["orders"][old]["status"] = "superseded"
            for item in state["unresolved"]:
                if item.startswith(f"{old}:") and item not in state["resolved_unresolved"]:
                    state["resolved_unresolved"].append(item)
        state["resolution_history"].append({"group": group, "reason": first["remediation_reason"],
                                            "replacements": replacements,
                                            "superseded": supersedes,
                                            "workspace_id": current_workspace})


def dossier_for(run_dir: Path, state: Mapping[str, Any]) -> Dict[str, Any]:
    orders, negative, contradictions, unread, failures, coverage = [], [], [], [], [], []
    citation_budget = 800
    for oid, meta in state["orders"].items():
        entry: Dict[str, Any] = {"id": oid, "role": meta["role"], "status": meta["status"],
                                 "depends_on": meta.get("depends_on", [])}
        receipts = verified_receipts(run_dir, state, oid)
        seal = next((r["result"] for r in reversed(receipts) if r["action"] == "seal"), None)
        if seal:
            entry["summary"] = seal["summary"]
            for key, destination in (("negative", negative), ("contradictions", contradictions),
                                     ("unread", unread)):
                destination.extend(f"{oid}: {value}" for value in seal.get(key, []))
        checks = [r["result"] for r in receipts if r["action"] == "check"]
        if checks:
            entry["checks"] = [{key: check.get(key) for key in
                                ("check_id", "exit_code", "tests_collected", "incomplete",
                                 "changed_worktree", "changed_outside_approved",
                                 "output_artifact")} for check in checks]
        for receipt in receipts:
            if receipt["action"].endswith("_error"):
                failures.append({"order_id": oid, "receipt_id": receipt["receipt_id"],
                                 "action": receipt["result"]["attempted_action"],
                                 "error": receipt["result"]["error"],
                                 "resolved": state.get("failed_attempts", {}).get(
                                     receipt["receipt_id"], {}).get("resolved", False)})
            if receipt["action"] == "inspect":
                inspected = receipt["result"]
                coverage.append({"order_id": oid, "kind": inspected.get("kind"),
                                 "total": inspected.get("total"),
                                 "truncated": inspected.get("truncated", False)})
        citations = []
        omitted = []
        for receipt in (r for r in receipts if r["action"] == "read"):
            citation = receipt["result"]
            size = len(canonical(citation))
            if size <= citation_budget:
                citations.append(citation)
                citation_budget -= size
            else:
                omitted.append(citation)
        if citations:
            entry["citations"] = citations
        if omitted:
            entry["citations_omitted"] = {"count": len(omitted),
                                           "artifact": artifact_write(run_dir, canonical(omitted))}
        orders.append(entry)
    coverage_gaps = []
    if "read-001" in state["orders"] and not any(
            item["order_id"] == "read-001" and item["kind"] == "inventory" for item in coverage):
        coverage_gaps.append("read-001: bounded repository inventory is missing")
    dossier = {"task": state["task"], "status": state["status"],
               "snapshot_id": state["snapshot"]["id"], "orders": orders,
               "negative_evidence": negative, "contradictions": contradictions,
               "unread": unread, "failed_actions": failures, "coverage": coverage,
               "coverage_gaps": coverage_gaps,
               "unresolved": [value for value in state["unresolved"]
                              if value not in state.get("resolved_unresolved", [])],
               "resolution_history": list(state.get("resolution_history", [])), "wave": state["wave"]}
    bound(dossier, MAX_DOSSIER, "dossier")
    return dossier


def runtime_signal(request: Mapping[str, Any]) -> Optional[str]:
    value = request.get("runtime_report", request.get("report"))
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    lowered = text.lower()
    failed_count = re.search(r"\b([1-9][0-9]*)\s+failed\b", lowered)
    failed_word = re.search(r"\bfailed\b", re.sub(r"\b0\s+failed\b", "", lowered))
    explicit_failure = re.search(r"\b(?:failure|error|timeout)\b", lowered)
    if failed_count or failed_word or explicit_failure or "truncat" in lowered:
        return text[:600]
    return None


def collect(repo: Path, request: Mapping[str, Any]) -> Dict[str, Any]:
    run_dir = run_dir_for(repo, request.get("run_id"))
    with run_lock(run_dir):
        state = state_load(run_dir)
        if state["status"] != "active":
            return envelope(state, "report", [], dossier_for(run_dir, state),
                            f"Run is {state['status']}.")
        signal = runtime_signal(request)
        if signal:
            state["unresolved"].append("runtime fan-out reported failure/truncation: " + signal)
        for oid in state["orders"]:
            verified_receipts(run_dir, state, oid)
        for message in state["unresolved"]:
            prefix = "orders remain incomplete: "
            if message.startswith(prefix):
                ids = [value.strip() for value in message[len(prefix):].split(",")]
                if all(state["orders"].get(oid, {}).get("status") not in ("pending", "working")
                       for oid in ids):
                    state["resolved_unresolved"].append(message)
        assess_verifiers(repo, run_dir, state)
        made = make_verifiers(repo, run_dir, state)
        if made:
            route, ids, report = "verifiers", made[:1], "Independent checks are ready."
        else:
            active = [oid for oid, meta in state["orders"].items()
                      if meta["role"] == "verifier" and meta["status"] in ("pending", "working")]
            if active:
                route, ids, report = "verifiers", active[:1], "Independent checks remain incomplete."
            else:
                ready = ready_coders(state)[:1]
                if ready:
                    if state["wave"] >= MAX_WAVES:
                        raise KernelError("coding wave limit exceeded")
                    state["wave"] += 1
                    route, ids, report = "coders", ready, "Next dependency-ready coding order is ready."
                else:
                    pending = [oid for oid, meta in state["orders"].items()
                               if meta["status"] in ("pending", "working")]
                    if pending:
                        state["unresolved"].append("orders remain incomplete: " + ", ".join(pending))
                    route, ids, report = "oracle", [], "Authenticated evidence is ready for Oracle review."
        state["route"] = route
        state["snapshot"] = bound_snapshot(repo, state)
        state_save(run_dir, state)
        return envelope(state, route, dispatch(state, ids), dossier_for(run_dir, state), report)


def finalize(repo: Path, request: Mapping[str, Any]) -> Dict[str, Any]:
    run_dir = run_dir_for(repo, request.get("run_id"))
    with run_lock(run_dir):
        state = state_load(run_dir)
        current = bound_snapshot(repo, state)
        if state["status"] == "accepted" and current["id"] != state.get("accepted_snapshot"):
            raise KernelError("accepted snapshot is stale")
        dossier = dossier_for(run_dir, state)
        if state["status"] == "accepted":
            report = accepted_report(state, current["id"])
        else:
            unresolved = [value for value in state["unresolved"]
                          if value not in state.get("resolved_unresolved", [])]
            report = "Unresolved: " + ("; ".join(unresolved) or state["status"])
        return envelope(state, "report", [], dossier, report)


def handle(request: Any) -> Dict[str, Any]:
    if not isinstance(request, dict):
        raise KernelError("request must be a JSON object")
    bound(request, MAX_REQUEST, "request")
    op = request.get("op")
    operations = {"bootstrap": bootstrap, "control": control, "collect": collect,
                  "worker": worker, "finalize": finalize}
    if op not in operations:
        raise KernelError("op must be bootstrap, control, collect, worker, or finalize")
    return operations[op](repo_root(), request)


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise KernelError("expected one JSON request argument")
        try:
            request = json.loads(sys.argv[1])
        except (ValueError, UnicodeError) as exc:
            raise KernelError(f"invalid JSON request: {exc}")
        response = handle(request)
    except (KernelError, OSError, subprocess.TimeoutExpired) as exc:
        response = {"ok": False, "error": str(exc)}
    except Exception as exc:
        response = {"ok": False, "error": f"internal workspace error: {type(exc).__name__}: {exc}"}
    sys.stdout.buffer.write(canonical(response) + b"\n")
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
