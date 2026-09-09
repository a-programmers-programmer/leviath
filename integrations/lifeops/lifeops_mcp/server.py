"""Streamable HTTP MCP; no imports of Desk internals or changes to its auth guard."""

import hashlib
import re
from typing import Annotated, Literal
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from pydantic import Field
from starlette.responses import JSONResponse

from .auth import LifeOpsGoogleProvider
from .client import ApiClient
from .config import Settings
from .receipts import Receipts

Short = Annotated[str, Field(min_length=1, max_length=200)]
Text = Annotated[str, Field(min_length=1, max_length=32000)]
RequestId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}


def identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value):
        raise ToolError("Invalid resource identifier")
    return value


def create_server(settings: Settings, *, auth=None, desk=None, leviath=None, principal_provider=None):
    # Alternate providers/clients are dependency injection for offline tests only.
    mcp = FastMCP("LifeOps", auth=auth if auth is not None else LifeOpsGoogleProvider(settings),
                  instructions="Inspect Desk and supervise Leviath. A run result is evidence, not task acceptance. Never infer run death from a failed listing. Writes use request IDs; reconcile uncertain outcomes before retrying.",
                  mask_error_details=True)
    desk = desk or ApiClient("Desk", settings.desk_url, "X-Xelor-Sync-Token", settings.desk_token)
    leviath = leviath or ApiClient("Leviath", settings.leviath_url, "Authorization", "Bearer " + settings.leviath_token)
    receipts = Receipts(settings.state_dir / "requests.sqlite3")

    def principal():
        if principal_provider:
            return principal_provider()
        token = get_access_token()
        if token is None or not token.claims.get("sub"):
            raise ToolError("Authenticated Google identity required")
        return hashlib.sha256(str(token.claims["sub"]).encode()).hexdigest()

    async def write(operation, request_id, payload, call, validate=None):
        if not settings.writes_enabled:
            raise ToolError("Writes are disabled by the operator")
        who = principal()
        prior = receipts.begin(who, request_id, operation, payload)
        if prior is not None:
            return prior
        # Any exception leaves a durable pending receipt. Never auto-repeat a POST.
        result = await call()
        if validate:
            validate(result)
        receipts.complete(who, request_id, result)
        return result

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request):
        return JSONResponse({"status": "ok", "service": "lifeops-mcp"})

    @mcp.tool(annotations=READ)
    async def lifeops_desk_list(show_all: bool = False, proposal_id: str = "") -> dict:
        """Read the existing machine Desk API. A default view can omit blocked/withdrawn slips."""
        params = {"show": "all"} if show_all else {}
        if proposal_id:
            params["proposal_id"] = identifier(proposal_id)
        return await desk.request("GET", "/api/desk", params=params)

    @mcp.tool(annotations=READ)
    async def lifeops_desk_thread(slip_id: Short) -> dict:
        """Read a Desk thread, including its existing evidence and reports."""
        return await desk.request("GET", f"/api/desk/{identifier(slip_id)}/thread")

    @mcp.tool(annotations=WRITE)
    async def lifeops_queue_stats() -> dict:
        """Inspect queue counts. The upstream GET also performs its normal expiry sweep."""
        return await desk.request("GET", "/queue/stats")

    @mcp.tool(annotations=READ)
    async def lifeops_request_status(request_id: RequestId) -> dict:
        """Inspect this caller's dispatch receipt. Pending is an uncertain outcome, not a failed spawn."""
        return receipts.status(principal(), request_id)

    @mcp.tool(annotations=READ)
    async def leviath_ps(limit: Annotated[int, Field(ge=1, le=100)] = 50, cursor: str = "") -> dict:
        """Read one durable runs page. Follow next_cursor; absence from one page proves nothing."""
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        return await leviath.request("GET", "/api/runs", params=params)

    @mcp.tool(annotations=READ)
    async def leviath_status(run_id: Short) -> dict:
        """Get the authoritative run snapshot from Leviath."""
        return await leviath.request("GET", f"/api/agents/{identifier(run_id)}")

    @mcp.tool(annotations=READ)
    async def leviath_result(run_id: Short) -> dict:
        """Read the exact result envelope; failure and partial output remain failure."""
        return await leviath.request("GET", f"/api/agents/{identifier(run_id)}/result")

    if settings.writes_enabled:
        @mcp.tool(annotations=WRITE)
        async def lifeops_desk_file(request_id: RequestId, title: Short, note: Text,
                                   owner: Literal["josh", "andy", "both"] = "both",
                                   product: Literal["platform", "anw", "parlour"] = "platform") -> dict:
            """File a task with an explicit request ID. Desk chooses its normal routing; this is not a guaranteed worker dispatch."""
            payload = {"title": title, "note": note, "owner": owner, "product": product,
                       "kind": "task", "origin": {"kind": "mcp", "ref": request_id},
                       "filed_by": "lifeops-mcp"}
            return await write("desk_file", request_id, payload,
                               lambda: desk.request("POST", "/api/desk/file", body=payload))

        @mcp.tool(annotations=WRITE)
        async def leviath_run(request_id: RequestId, project: Short, task: Text,
                              blueprint: Short = "qwen-worker") -> dict:
            """Start an allowlisted worker asynchronously; preserve request_id on retries. Returns run_id, not task completion."""
            if project not in settings.projects or blueprint not in settings.blueprints:
                raise ToolError("Project or blueprint is not allowlisted")
            payload = {"blueprint": blueprint, "task": task,
                       "workdir": settings.projects[project], "yolo": settings.unattended,
                       "metadata": {"lifeops_request_id": request_id,
                                    "lifeops_principal": principal(), "lifeops_project": project}}
            def validate(result):
                run_id = result.get("run_id")
                if not isinstance(run_id, str) or not run_id:
                    raise ToolError("Leviath spawn response is missing run_id; reconcile before retrying")
            return await write("leviath_run", request_id, payload,
                               lambda: leviath.request("POST", "/api/agents", body=payload), validate)

        @mcp.tool(annotations=WRITE)
        async def leviath_message(request_id: RequestId, run_id: Short, message: Text) -> dict:
            """Steer an existing run. This can change worker behavior and cause code edits."""
            path = f"/api/agents/{identifier(run_id)}/message"
            return await write("leviath_message", request_id, {"run_id": run_id, "message": message},
                               lambda: leviath.request("POST", path, body={"message": message}))

        @mcp.tool(annotations={**WRITE, "destructiveHint": True})
        async def leviath_control(request_id: RequestId, run_id: Short,
                                   action: Literal["pause", "resume", "cancel"]) -> dict:
            """Pause, resume or cancel a run; cancellation preserves the run record and is not proof that all effects stopped."""
            path = f"/api/agents/{identifier(run_id)}"
            method = "DELETE" if action == "cancel" else "POST"
            if action != "cancel":
                path += "/" + action
            return await write("leviath_control", request_id, {"run_id": run_id, "action": action},
                               lambda: leviath.request(method, path))
    return mcp


def main():
    import logging
    import os
    # The service is never allowed to have an unauthenticated startup mode.
    os.umask(0o077)
    # Google tokeninfo uses a query parameter. Do not enable HTTP URL logging.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    create_server(Settings.from_env()).run(transport="http", host="0.0.0.0",
                                          port=int(os.environ.get("PORT", "8000")),
                                          show_banner=False)


if __name__ == "__main__":
    main()
