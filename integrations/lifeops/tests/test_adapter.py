import asyncio
import json
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from lifeops_mcp.client import ApiClient
from lifeops_mcp.receipts import Receipts
from lifeops_mcp.server import create_server


def make_server(settings, handler):
    transport = httpx.MockTransport(handler)
    desk = ApiClient("Desk", settings.desk_url, "X-Xelor-Sync-Token", settings.desk_token, transport=transport)
    lev = ApiClient("Leviath", settings.leviath_url, "Authorization", "Bearer " + settings.leviath_token, transport=transport)
    return create_server(settings, desk=desk, leviath=lev, principal_provider=lambda: "operator-sub")


async def test_real_mcp_dispatch_replay_and_result(settings):
    calls = []
    def handler(request):
        calls.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/agents"
            assert request.headers["Authorization"] == "Bearer " + settings.leviath_token
            assert "X-Xelor-Sync-Token" not in request.headers
            payload = json.loads(request.content)
            assert payload["blueprint"] == "qwen-worker"
            assert payload["workdir"] == "/data/work/leviath"
            assert payload["yolo"] is False
            assert "model" not in payload and "allow" not in payload
            return httpx.Response(200, json={"run_id": "qwen-worker-1", "agent_id": "qwen-worker-1"})
        return httpx.Response(200, json={"status": "error", "final_output": {"content": "partial"}, "error": "failed check"})
    server = make_server(settings, handler)
    args = {"request_id": "request-1", "project": "leviath", "task": "Run the agreed checks"}
    async with Client(server) as client:
        first = await client.call_tool("leviath_run", args)
        again = await client.call_tool("leviath_run", args)
        assert first.data == again.data
        assert len(calls) == 1
        failed = await client.call_tool("leviath_result", {"run_id": "qwen-worker-1"})
        assert failed.data["status"] == "error" and failed.data["error"] == "failed check"
        conflict = await client.call_tool("leviath_run", {**args, "task": "different"}, raise_on_error=False)
        assert conflict.is_error and "IDEMPOTENCY_CONFLICT" in conflict.content[0].text
    # A server restart retains the receipt and does not repeat the remote spawn.
    async with Client(make_server(settings, handler)) as client:
        assert (await client.call_tool("leviath_run", args)).data == first.data
    assert len(calls) == 2


async def test_timeout_never_repeats_spawn(settings):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("may contain secret data", request=request)
    async with Client(make_server(settings, handler)) as client:
        args = {"request_id": "ambiguous", "project": "leviath", "task": "Work"}
        failure = await client.call_tool("leviath_run", args, raise_on_error=False)
        assert failure.is_error and "secret data" not in failure.content[0].text
        again = await client.call_tool("leviath_run", args, raise_on_error=False)
        assert "OUTCOME_UNKNOWN" in again.content[0].text
        receipt = await client.call_tool("lifeops_request_status", {"request_id": "ambiguous"})
        assert receipt.data["state"] == "pending"
    assert len(calls) == 1


async def test_tools_cannot_choose_arbitrary_paths_or_blueprints(settings):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})
    async with Client(make_server(settings, handler)) as client:
        for name, args in [
            ("leviath_run", {"request_id": "x", "project": "/etc", "task": "Work"}),
            ("leviath_run", {"request_id": "x", "project": "leviath", "task": "Work", "blueprint": "../../admin"}),
            ("leviath_result", {"run_id": "../config"}),
            ("lifeops_desk_thread", {"slip_id": "d-1?token=secret"}),
        ]:
            assert (await client.call_tool(name, args, raise_on_error=False)).is_error
    assert not calls


async def test_desk_routes_headers_and_cancel_not_delete_record(settings):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "slips": []})
    async with Client(make_server(settings, handler)) as client:
        await client.call_tool("lifeops_desk_list", {"show_all": True})
        await client.call_tool("lifeops_desk_thread", {"slip_id": "d-1"})
        await client.call_tool("lifeops_queue_stats", {})
        await client.call_tool("leviath_control", {"request_id": "cancel-1", "run_id": "run-1", "action": "cancel"})
        await client.call_tool("leviath_ps", {"cursor": "next-page", "limit": 10})
    assert [r.url.path for r in calls] == ["/api/desk", "/api/desk/d-1/thread", "/queue/stats", "/api/agents/run-1", "/api/runs"]
    assert calls[0].url.params["show"] == "all"
    assert calls[-1].url.params["cursor"] == "next-page"
    assert calls[3].method == "DELETE"
    for request in calls[:3]:
        assert request.headers["X-Xelor-Sync-Token"] == settings.desk_token
        assert "Authorization" not in request.headers


async def test_inspection_catalog_does_not_expose_mutations(settings):
    async with Client(make_server(replace(settings, writes_enabled=False), lambda r: None)) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}
        assert "leviath_run" not in tools and "leviath_control" not in tools
        assert "lifeops_desk_file" not in tools
        assert "leviath_result" in tools
        assert tools["lifeops_queue_stats"].annotations.readOnlyHint is False
        assert not any("done" in name or "report" in name or "claim" in name for name in tools)


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "https://evil.example/capture"}),
    httpx.Response(200, text="<html>Google sign in</html>", headers={"Content-Type": "text/html"}),
    httpx.Response(401, json={"error": "secret"}),
    httpx.Response(500, json={"error": "secret"}),
    httpx.Response(200, json={"ok": False, "error": "secret"}),
    httpx.Response(200, content=b"invalid", headers={"Content-Type": "application/json"}),
    httpx.Response(200, json=["unexpected array"]),
])
async def test_error_responses_do_not_follow_redirect_or_leak_body(response):
    calls = []
    def handler(request):
        calls.append(request)
        return response
    api = ApiClient("Desk", "https://desk.test", "X-Xelor-Sync-Token", "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(ToolError) as exc:
        await api.request("GET", "/api/desk")
    assert "secret" not in str(exc.value)
    assert len(calls) == 1


async def test_response_size_limit():
    api = ApiClient("Desk", "https://desk.test", "Token", "secret", max_bytes=10,
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"long": "x" * 100})))
    with pytest.raises(ToolError, match="byte limit"):
        await api.request("GET", "/api/desk")


def test_concurrent_receipt_reservation_has_one_winner(tmp_path):
    receipts = Receipts(tmp_path / "requests.sqlite3")
    def reserve(_):
        try:
            return receipts.begin("one", "same-key", "spawn", {"task": "x"}) is None
        except ToolError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(16))) == 1
    assert receipts.status("other", "same-key")["state"] == "not_found"


async def test_missing_run_id_remains_uncertain(settings):
    async with Client(make_server(settings, lambda r: httpx.Response(200, json={"ok": True}))) as client:
        result = await client.call_tool("leviath_run", {"request_id": "no-id", "project": "leviath", "task": "Work"}, raise_on_error=False)
        assert result.is_error
        assert (await client.call_tool("lifeops_request_status", {"request_id": "no-id"})).data["state"] == "pending"
