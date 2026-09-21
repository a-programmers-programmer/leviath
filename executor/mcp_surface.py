"""MCP surface for the executor.

The MCP tools call ExecutorCore directly, exactly like the GraphQL resolvers
and the CLI verbs. One core, three surfaces, no duplicated auth or lifecycle.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .auth import AuthContext
from .core import BackendError, ExecutorCore, SpawnRequest
from .identity import AuthError
from .lifecycle import LifecycleError

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "leviath-executor", "version": "1.0.0"}

TOOLS: List[dict] = [
    {
        "name": "executor_spawn",
        "description": "Spawn a leviath run and return its job record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "agent": {"type": "string"},
                "task": {"type": "string"},
                "workdir": {"type": "string"},
                "yolo": {"type": "boolean"},
                "request_id": {"type": "string"},
            },
            "required": ["agent", "task"],
        },
    },
    {
        "name": "executor_status",
        "description": "Read one job's status and artifacts.",
        "inputSchema": {
            "type": "object",
            "properties": {"token": {"type": "string"}, "job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "executor_cancel",
        "description": "Cancel a job. Idempotent on a terminal job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "job_id": {"type": "string"},
                "force": {"type": "boolean"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "executor_artifact",
        "description": "Fetch one artifact by name, bytes as base64.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {"type": "string"},
                "job_id": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["job_id", "name"],
        },
    },
]


class ExecutorMCP:
    """MCP request handler over the shared core."""

    def __init__(self, core: ExecutorCore, auth: Optional[AuthContext] = None):
        self.core = core
        self.auth = auth or core.auth

    def handle(self, message: dict) -> Optional[dict]:
        """Handle one JSON-RPC message. Returns None for notifications."""
        req_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "initialize":
            return self._ok(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            })
        if method in ("notifications/initialized", "initialized"):
            return None  # notification: no response
        if method == "ping":
            return self._ok(req_id, {})
        if method == "tools/list":
            return self._ok(req_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._tool_call(req_id, params)
        return self._err(req_id, -32601, f"Method not found: {method}")

    def _ok(self, req_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _err(self, req_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": code, "message": message}}

    def _tool_call(self, req_id, params: dict) -> dict:
        name = params.get("name")
        args: Dict[str, Any] = params.get("arguments") or {}
        try:
            payload = self.dispatch_tool(name, args)
            return self._ok(req_id, {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": not payload.get("ok", False),
            })
        except (AuthError, BackendError, LifecycleError) as exc:
            payload = {"ok": False, "error": exc.to_dict()}
            return self._ok(req_id, {
                "content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": True,
            })

    def dispatch_tool(self, name: Optional[str], args: Dict[str, Any]) -> dict:
        """Run one tool by name. Shared by tools/call and direct tests."""
        principal = self.auth.authenticate(args.get("token"))
        if name == "executor_spawn":
            job = self.core.spawn(principal, SpawnRequest(
                agent=args.get("agent"), task=args.get("task"),
                workdir=args.get("workdir"), yolo=bool(args.get("yolo", True)),
                request_id=args.get("request_id")))
            return {"ok": job.error is None, "job": job.to_dict(), "error": job.error}
        if name == "executor_status":
            return {"ok": True, "job": self.core.status(principal, args["job_id"]).to_dict()}
        if name == "executor_cancel":
            return {"ok": True, "job": self.core.cancel(
                principal, args["job_id"], force=bool(args.get("force", False))).to_dict()}
        if name == "executor_artifact":
            return {"ok": True,
                    "artifact": self.core.artifact(principal, args["job_id"], args["name"])}
        raise BackendError("unknown_tool", f"unknown tool: {name}")
