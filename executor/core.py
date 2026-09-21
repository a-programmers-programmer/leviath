"""The shared executor core: the one place jobs are spawned, watched and stopped.

Both the GraphQL surface and the MCP surface and the CLI all reach the daemon
through this class. They differ only in how they pass args in and how they
render the result out. Auth, identity and lifecycle checks happen here exactly
once, so no surface can skip a check the others enforce.

The daemon is reached through a Backend. `LevDaemonBackend` shells out to the
real `lev` CLI (the same binary the CLI surface uses). A test backend records
the command plan without running it.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from .auth import AuthContext
from .identity import (
    SCOPE_ARTIFACT, SCOPE_CANCEL, SCOPE_SPAWN, SCOPE_STATUS, AuthError, Principal,
)
from . import lifecycle as lc
from .lifecycle import LifecycleError, RunState


class BackendError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class Backend(Protocol):
    """What the core needs from a daemon."""

    def run_command(self, argv: List[str], timeout: float = 30.0) -> Dict[str, Any]:
        """Run one daemon command. Returns {ok, stdout, stderr, code}."""

    def read_run(self, run_id: str) -> Optional[dict]:
        """Read a run record. Returns None when the run is unknown."""

    def read_artifact(self, run_id: str, name: str) -> Optional[bytes]:
        """Read one artifact's bytes. Returns None when it is absent."""


@dataclass
class SpawnRequest:
    agent: str
    task: str
    workdir: Optional[str] = None
    yolo: bool = True
    request_id: Optional[str] = None  # caller-supplied key for idempotent spawn


@dataclass
class Job:
    """A spawned job, as every surface sees it."""

    job_id: str
    principal_id: str
    agent: str
    task: str
    state: RunState
    workdir: Optional[str] = None
    artifacts: List[dict] = field(default_factory=list)
    events: List[dict] = field(default_factory=list)
    error: Optional[dict] = None
    request_id: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "principal_id": self.principal_id,
            "agent": self.agent,
            "task": self.task,
            "state": self.state.value,
            "workdir": self.workdir,
            "artifacts": list(self.artifacts),
            "events": list(self.events),
            "error": self.error,
            "request_id": self.request_id,
            "created_at": round(self.created_at, 3),
            "updated_at": round(self.updated_at, 3),
        }


def _summarize_artifacts(record: dict) -> List[dict]:
    """Turn a daemon run record into the artifact list every surface shows."""
    flags = record.get("flags") or {}
    out: List[dict] = []
    for name in flags.get("modified_files") or []:
        out.append({"name": str(name), "kind": "modified_file"})
    if record.get("final_output"):
        out.append({"name": "final_output", "kind": "final_output"})
    return out


class LevDaemonBackend:
    """Backend over the real daemon, reached the same way the CLI reaches it."""

    def __init__(self, lev_bin: Optional[str] = None, runs_root: Optional[str] = None):
        self.lev_bin = lev_bin or os.environ.get("LEV_BIN") or "lev"
        self.runs_root = runs_root or os.environ.get("LEV_RUNS_ROOT") or os.path.join(
            os.path.expanduser("~"), ".leviath", "runs"
        )

    def run_command(self, argv: List[str], timeout: float = 30.0) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except FileNotFoundError:
            raise BackendError("daemon_unavailable", f"{self.lev_bin} not found")
        except subprocess.TimeoutExpired:
            raise BackendError("daemon_timeout", f"command timed out: {shlex.join(argv)}")
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "argv": list(argv),
        }

    def read_run(self, run_id: str) -> Optional[dict]:
        path = os.path.join(self.runs_root, run_id, "meta.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def read_artifact(self, run_id: str, name: str) -> Optional[bytes]:
        run_dir = os.path.join(self.runs_root, run_id)
        if name == "final_output":
            path = os.path.join(run_dir, "final_output")
        else:
            path = os.path.join(run_dir, name)
            if not os.path.exists(path):
                path = os.path.join(run_dir, "blobs", name)
        if not os.path.exists(path) or os.path.isdir(path):
            return None
        with open(path, "rb") as handle:
            return handle.read()


class ExecutorCore:
    """Spawn, status, cancel and artifact. One implementation, all surfaces."""

    def __init__(self, auth: AuthContext, backend: Optional[Backend] = None,
                 clock=time.time):
        self.auth = auth
        self.backend: Backend = backend or LevDaemonBackend()
        self.clock = clock
        self._jobs: Dict[str, Job] = {}
        self._by_request: Dict[str, str] = {}

    # -- helpers ---------------------------------------------------------
    def _now(self) -> float:
        return float(self.clock())

    def _record(self, job: Job, event: dict) -> Job:
        job.events.append(event)
        job.updated_at = self._now()
        return job

    # -- spawn -----------------------------------------------------------
    def spawn(self, principal: Principal, req: SpawnRequest) -> Job:
        """Start a run. Same identity, auth and lifecycle checks on every surface."""
        self.auth.authorize(principal, SCOPE_SPAWN, req.agent)
        if not req.agent:
            raise BackendError("invalid_request", "agent is required")
        if not req.task:
            raise BackendError("invalid_request", "task is required")

        if req.request_id:
            existing = self._by_request.get(req.request_id)
            if existing:
                job = self._jobs[existing]
                self._record(job, lc.event("spawn_deduplicated", job.state, job.state,
                                           request_id=req.request_id))
                return job

        job_id = f"job_{uuid.uuid4().hex[:16]}"
        job = Job(
            job_id=job_id,
            principal_id=principal.principal_id,
            agent=req.agent,
            task=req.task,
            state=RunState.QUEUED,
            workdir=req.workdir,
            request_id=req.request_id,
            created_at=self._now(),
            updated_at=self._now(),
        )
        self._jobs[job_id] = job
        if req.request_id:
            self._by_request[req.request_id] = job_id
        self._record(job, lc.event("spawn_requested", None, RunState.QUEUED,
                                   agent=req.agent, principal=principal.principal_id))

        argv = [getattr(self.backend, "lev_bin", "lev"), "run", req.agent, req.task]
        if req.workdir:
            argv += ["--workdir", req.workdir]
        if not req.yolo:
            argv += ["--no-yolo"]
        result = self.backend.run_command(argv)

        if not result.get("ok"):
            job.state = lc.assert_transition(job.state, RunState.FAILED)
            job.error = {
                "code": "spawn_failed",
                "message": (result.get("stderr") or result.get("stdout") or "").strip()[:500],
            }
            self._record(job, lc.event("spawn_failed", RunState.QUEUED, RunState.FAILED,
                                       code=job.error["code"]))
            return job

        job.state = lc.assert_transition(job.state, RunState.RUNNING)
        self._record(job, lc.event("spawned", RunState.QUEUED, RunState.RUNNING,
                                   argv=result.get("argv")))
        return job

    # -- status ----------------------------------------------------------
    def status(self, principal: Principal, job_id: str) -> Job:
        """Read one job, refreshing state from the daemon when it is live."""
        self.auth.authorize(principal, SCOPE_STATUS, job_id)
        job = self._jobs.get(job_id)
        if job is None:
            raise BackendError("not_found", f"no such job {job_id}")
        self._refresh(job)
        return job

    def list_jobs(self, principal: Principal) -> List[Job]:
        self.auth.authorize(principal, SCOPE_STATUS, "*")
        for job in self._jobs.values():
            self._refresh(job)
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def _refresh(self, job: Job) -> Job:
        record = self.backend.read_run(job.job_id)
        if record is None:
            return job
        status = str(record.get("status") or "").lower()
        mapping = {
            "running": RunState.RUNNING,
            "complete": RunState.SUCCEEDED,
            "completed": RunState.SUCCEEDED,
            "failed": RunState.FAILED,
            "error": RunState.FAILED,
            "cancelled": RunState.CANCELLED,
            "canceled": RunState.CANCELLED,
        }
        target = mapping.get(status)
        if target and lc.can_transition(job.state, target):
            previous = job.state
            job.state = target
            self._record(job, lc.event("state_observed", previous, target))
        job.artifacts = _summarize_artifacts(record)
        return job

    # -- cancel ----------------------------------------------------------
    def cancel(self, principal: Principal, job_id: str, force: bool = False) -> Job:
        """Stop a run. Idempotent: cancelling a terminal job is a no-op."""
        self.auth.authorize(principal, SCOPE_CANCEL, job_id)
        job = self._jobs.get(job_id)
        if job is None:
            raise BackendError("not_found", f"no such job {job_id}")
        self.auth.authorize(principal, SCOPE_CANCEL, job_id, owner=job.principal_id)

        previous = job.state
        if lc.is_terminal(job.state):
            self._record(job, lc.event("cancel_noop", previous, previous))
            return job

        argv = [getattr(self.backend, "lev_bin", "lev"), "cancel", job_id]
        if force:
            argv.append("--force")
        result = self.backend.run_command(argv)
        target = lc.cancel_state(job.state)
        if not result.get("ok") and force:
            # --force writes on-disk state directly, so a daemon error is survivable.
            target = lc.cancel_state(job.state)
        job.state = lc.assert_transition(job.state, target)
        if not result.get("ok"):
            job.error = {
                "code": "cancel_failed",
                "message": (result.get("stderr") or "").strip()[:500],
            }
        self._record(job, lc.event("cancelled" if result.get("ok") else "cancel_requested",
                                   previous, job.state, force=force))
        return job

    # -- artifact --------------------------------------------------------
    def artifact(self, principal: Principal, job_id: str, name: str) -> dict:
        """Fetch one artifact's bytes. Bytes are base64 so a graph can carry them."""
        import base64
        self.auth.authorize(principal, SCOPE_ARTIFACT, job_id, owner=None)
        job = self._jobs.get(job_id)
        if job is None:
            raise BackendError("not_found", f"no such job {job_id}")
        raw = self.backend.read_artifact(job_id, name)
        if raw is None:
            raise BackendError("artifact_not_found", f"no artifact {name!r} on {job_id}")
        return {
            "job_id": job_id,
            "name": name,
            "size": len(raw),
            "sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "content_base64": base64.b64encode(raw).decode("ascii"),
        }
