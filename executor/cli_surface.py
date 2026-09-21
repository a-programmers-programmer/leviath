"""CLI surface for the executor.

`lev executor <verb>` style commands. The CLI calls ExecutorCore directly:
it does not shell out for auth, and it does not re-implement lifecycle checks.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .auth import AuthContext
from .core import BackendError, ExecutorCore, SpawnRequest
from .identity import AuthError
from .lifecycle import LifecycleError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lev executor", description="Executor surface (CLI).")
    p.add_argument("--token", default=None, help="bearer token for this call")
    sub = p.add_subparsers(dest="verb", required=True)

    sp = sub.add_parser("spawn")
    sp.add_argument("agent")
    sp.add_argument("task")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--no-yolo", action="store_true")
    sp.add_argument("--request-id", default=None)

    st = sub.add_parser("status")
    st.add_argument("job_id")

    ls = sub.add_parser("list")

    cn = sub.add_parser("cancel")
    cn.add_argument("job_id")
    cn.add_argument("--force", action="store_true")

    ar = sub.add_parser("artifact")
    ar.add_argument("job_id")
    ar.add_argument("name")

    return p


def run(argv: Optional[List[str]] = None, core: Optional[ExecutorCore] = None,
        auth: Optional[AuthContext] = None,
        out=None) -> int:
    """Run one CLI call. Returns a process exit code."""
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    core = core or ExecutorCore(auth or AuthContext(secret="dev"))

    try:
        principal = core.auth.authenticate(args.token)
        if args.verb == "spawn":
            job = core.spawn(principal, SpawnRequest(
                agent=args.agent, task=args.task, workdir=args.workdir,
                yolo=not args.no_yolo, request_id=args.request_id))
            payload = {"ok": job.error is None, "job": job.to_dict(), "error": job.error}
        elif args.verb == "status":
            payload = {"ok": True, "job": core.status(principal, args.job_id).to_dict()}
        elif args.verb == "list":
            payload = {"ok": True, "jobs": [j.to_dict() for j in core.list_jobs(principal)]}
        elif args.verb == "cancel":
            payload = {"ok": True,
                       "job": core.cancel(principal, args.job_id, force=args.force).to_dict()}
        elif args.verb == "artifact":
            payload = {"ok": True,
                       "artifact": core.artifact(principal, args.job_id, args.name)}
        else:  # pragma: no cover - argparse enforces the verb set
            raise BackendError("invalid_request", f"unknown verb {args.verb}")
    except (AuthError, BackendError, LifecycleError) as exc:
        payload = {"ok": False, "error": exc.to_dict()}
        out.write(json.dumps(payload) + "\n")
        return 2 if isinstance(exc, AuthError) else 1

    out.write(json.dumps(payload) + "\n")
    return 0
