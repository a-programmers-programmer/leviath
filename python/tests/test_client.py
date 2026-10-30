"""Tests for the Leviath async Python client.

Tests use a fake `lev` binary (Python script) that records calls and returns
controlled JSON, so we exercise the polling/backoff/timeout logic without
a real daemon.
"""

import asyncio
import json
import os
import stat
import tempfile
from pathlib import Path

import pytest

from leviath import (
    LeviathClient,
    LeviathError,
    LeviathSpawnError,
    LeviathTimeoutError,
    RunResult,
    RunStatus,
    TerminalStatus,
)
from leviath.models import TERMINAL_LABELS


# ── helpers ────────────────────────────────────────────────────────────────


class FakeLev:
    """A fake `lev` binary that returns canned JSON responses.

    The fake is a Python script written to a temp directory. Each call
    records the command line and returns the next queued response.
    """

    def __init__(self, tmp_path: Path):
        self._dir = tmp_path
        self._responses: list[tuple[str, str, int]] = []
        self._calls_file = self._dir / "calls.json"
        self._calls_file.write_text("[]")

    def queue_response(self, stdout: str = "", stderr: str = "", rc: int = 0):
        """Queue a response: (stdout, stderr, returncode)."""
        self._responses.append((stdout, stderr, rc))

    def calls(self) -> list[list[str]]:
        """Read the recorded calls."""
        if self._calls_file.exists():
            try:
                return json.loads(self._calls_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def build(self) -> str:
        """Write the fake Python script and return its path."""
        script = self._dir / "lev"
        resp_file = self._dir / "responses.json"
        resp_file.write_text(json.dumps(self._responses))

        script_content = f'''#!/usr/bin/env python3
"""Fake lev — reads queued responses and records calls."""
import json, sys, os

RESP_FILE = {json.dumps(str(resp_file))}
CALLS_FILE = {json.dumps(str(self._calls_file))}

# Record this call
try:
    with open(CALLS_FILE) as f:
        calls = json.load(f)
except Exception:
    calls = []
calls.append(sys.argv[1:])
with open(CALLS_FILE, "w") as f:
    json.dump(calls, f)

# Read next response
try:
    with open(RESP_FILE) as f:
        responses = json.load(f)
except Exception:
    responses = []

if responses:
    resp = responses.pop(0)
    with open(RESP_FILE, "w") as f:
        json.dump(responses, f)
    stdout, stderr, rc = resp
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    sys.exit(rc)

# Default: success with empty output
sys.exit(0)
'''
        script.write_text(script_content)
        script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR | stat.S_IXUSR)
        return str(script)


def make_ps_entry(
    run_id: str = "run-001",
    status: str = "complete",
    stage: str = "main",
    iteration: int = 5,
    has_final_output: bool = True,
    live: bool = True,
    error: str | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "status": status,
        "stage": stage,
        "iteration": iteration,
        "has_final_output": has_final_output,
        "live": live,
        "error": error,
        "started_at": 1700000000,
        "title": None,
        "prompt_tokens": 100,
        "completion_tokens": 200,
    }


def make_ps_json(entries: list[dict]) -> str:
    return json.dumps({"runs": entries, "finished": [], "not_running": []})


def make_run_json(run_id: str = "run-001") -> str:
    return json.dumps({"run_id": run_id, "status": "starting"})


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_lev_dir(tmp_path):
    return tmp_path


# ── tests: models ───────────────────────────────────────────────────────────


class TestTerminalStatus:
    def test_from_wire_parses_all_terminals(self):
        assert TerminalStatus.from_wire("complete") == TerminalStatus.COMPLETE
        assert TerminalStatus.from_wire("Complete") == TerminalStatus.COMPLETE
        assert TerminalStatus.from_wire("COMPLETE") == TerminalStatus.COMPLETE
        assert TerminalStatus.from_wire("error") == TerminalStatus.ERROR
        assert TerminalStatus.from_wire("cancelled") == TerminalStatus.CANCELLED

    def test_from_wire_returns_none_for_non_terminal(self):
        assert TerminalStatus.from_wire("active") is None
        assert TerminalStatus.from_wire("running") is None
        assert TerminalStatus.from_wire("") is None


class TestRunStatus:
    def test_from_wire_parses_all_known(self):
        assert RunStatus.from_wire("complete") == RunStatus.COMPLETE
        assert RunStatus.from_wire("error") == RunStatus.ERROR
        assert RunStatus.from_wire("cancelled") == RunStatus.CANCELLED
        assert RunStatus.from_wire("active") == RunStatus.ACTIVE
        assert RunStatus.from_wire("idle") == RunStatus.IDLE
        assert RunStatus.from_wire("paused") == RunStatus.PAUSED
        assert RunStatus.from_wire("waiting") == RunStatus.WAITING
        assert RunStatus.from_wire("starting") == RunStatus.STARTING

    def test_from_wire_unknown_becomes_unknown(self):
        assert RunStatus.from_wire("flurb") == RunStatus.UNKNOWN
        assert RunStatus.from_wire("") == RunStatus.UNKNOWN

    def test_is_terminal(self):
        assert RunStatus.COMPLETE.is_terminal
        assert RunStatus.ERROR.is_terminal
        assert RunStatus.CANCELLED.is_terminal
        assert not RunStatus.ACTIVE.is_terminal
        assert not RunStatus.IDLE.is_terminal
        assert not RunStatus.PAUSED.is_terminal
        assert not RunStatus.WAITING.is_terminal
        assert not RunStatus.STARTING.is_terminal
        assert not RunStatus.UNKNOWN.is_terminal

    def test_terminal_labels_set(self):
        assert "complete" in TERMINAL_LABELS
        assert "error" in TERMINAL_LABELS
        assert "cancelled" in TERMINAL_LABELS
        assert "active" not in TERMINAL_LABELS
        assert len(TERMINAL_LABELS) == 3


# ── tests: errors ────────────────────────────────────────────────────────────


class TestErrors:
    def test_timeout_error_contains_run_id_and_status(self):
        err = LeviathTimeoutError("run-42", 60.0, "active")
        assert "run-42" in str(err)
        assert "60" in str(err)
        assert "active" in str(err)
        assert err.run_id == "run-42"
        assert err.timeout_secs == 60.0
        assert err.last_status == "active"

    def test_spawn_error(self):
        err = LeviathSpawnError("boom")
        assert "boom" in str(err)


# ── tests: client.run_and_wait ──────────────────────────────────────────────


class TestRunAndWaitTerminal:
    """run_and_wait returns after the run reaches a terminal status."""

    @pytest.mark.asyncio
    async def test_returns_when_complete(self, fake_lev_dir):
        """Spawn returns a run_id; status polls active then complete."""
        fake = FakeLev(fake_lev_dir)

        # Response 1: `lev run --json` → run_id
        fake.queue_response(stdout=make_run_json("run-001"))

        # Response 2: `lev ps --json --all` → active
        fake.queue_response(
            stdout=make_ps_json([make_ps_entry("run-001", status="active")])
        )

        # Response 3: `lev ps --json --all` → complete with final_output
        fake.queue_response(
            stdout=make_ps_json(
                [make_ps_entry("run-001", status="complete", has_final_output=True)]
            )
        )

        # Response 4: `lev result run-001 --raw` → final output
        fake.queue_response(stdout="the answer")

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.run_and_wait(
                agent="test-agent",
                task="hello",
                poll_interval=0.01,
            )

        assert result.run_id == "run-001"
        assert result.status == RunStatus.COMPLETE
        assert result.final_output == "the answer"

        # Verify the calls made
        calls = fake.calls()
        assert len(calls) >= 3  # run + ps + ps + result
        assert calls[0][:2] == ["run", "test-agent"]

    @pytest.mark.asyncio
    async def test_returns_when_error(self, fake_lev_dir):
        """Terminal status 'error' also returns."""
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout=make_run_json("run-err"))
        fake.queue_response(
            stdout=make_ps_json(
                [make_ps_entry("run-err", status="error", error="something broke")]
            )
        )

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.run_and_wait(
                agent="test-agent",
                poll_interval=0.01,
            )

        assert result.status == RunStatus.ERROR
        assert result.error == "something broke"

    @pytest.mark.asyncio
    async def test_returns_when_cancelled(self, fake_lev_dir):
        """Terminal status 'cancelled' also returns."""
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout=make_run_json("run-cancel"))
        fake.queue_response(
            stdout=make_ps_json(
                [make_ps_entry("run-cancel", status="cancelled")]
            )
        )

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.run_and_wait(
                agent="test-agent",
                poll_interval=0.01,
            )

        assert result.status == RunStatus.CANCELLED


class TestRunAndWaitTimeout:
    """Timeout raises loudly — the run continues."""

    @pytest.mark.asyncio
    async def test_timeout_raises_leaviath_timeout_error(self, fake_lev_dir):
        """A run that stays active past deadline raises LeviathTimeoutError."""
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout=make_run_json("run-stuck"))

        # Keep returning "active" — never terminal
        for _ in range(20):
            fake.queue_response(
                stdout=make_ps_json(
                    [make_ps_entry("run-stuck", status="active")]
                )
            )

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            with pytest.raises(LeviathTimeoutError) as exc_info:
                await client.run_and_wait(
                    agent="test-agent",
                    timeout=0.5,
                    poll_interval=0.05,
                )

        err = exc_info.value
        assert err.run_id == "run-stuck"
        assert err.last_status == "active"
        assert "run-stuck" in str(err)


class TestRunAndWaitNonTerminalStillWaits:
    """Non-terminal statuses keep the loop going."""

    @pytest.mark.asyncio
    async def test_keeps_polling_through_active_then_completes(self, fake_lev_dir):
        """Active → Active → Complete: returns complete, not active."""
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout=make_run_json("run-progress"))

        # Two "active" then "complete"
        fake.queue_response(
            stdout=make_ps_json([make_ps_entry("run-progress", status="active")])
        )
        fake.queue_response(
            stdout=make_ps_json([make_ps_entry("run-progress", status="active")])
        )
        fake.queue_response(
            stdout=make_ps_json(
                [make_ps_entry("run-progress", status="complete", has_final_output=True)]
            )
        )
        fake.queue_response(stdout="done!")

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.run_and_wait(
                agent="test-agent",
                poll_interval=0.01,
            )

        assert result.status == RunStatus.COMPLETE
        assert result.final_output == "done!"


# ── tests: client.wait (existing run) ───────────────────────────────────────


class TestWaitExistingRun:
    @pytest.mark.asyncio
    async def test_wait_existing_run_to_completion(self, fake_lev_dir):
        """wait() on an already-spawned run_id polls until terminal."""
        fake = FakeLev(fake_lev_dir)
        # No spawn call — we already have the run_id
        fake.queue_response(
            stdout=make_ps_json([make_ps_entry("existing-run", status="active")])
        )
        fake.queue_response(
            stdout=make_ps_json(
                [
                    make_ps_entry(
                        "existing-run", status="complete", has_final_output=True
                    )
                ]
            )
        )
        fake.queue_response(stdout="existing output")

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.wait("existing-run", poll_interval=0.01)

        assert result.run_id == "existing-run"
        assert result.status == RunStatus.COMPLETE
        assert result.final_output == "existing output"


# ── tests: client.stream ────────────────────────────────────────────────────


class TestStream:
    @pytest.mark.asyncio
    async def test_yields_status_snapshots(self, fake_lev_dir):
        """stream() yields each status until terminal."""
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(
            stdout=make_ps_json([make_ps_entry("stream-run", status="active")])
        )
        fake.queue_response(
            stdout=make_ps_json([make_ps_entry("stream-run", status="active")])
        )
        fake.queue_response(
            stdout=make_ps_json(
                [make_ps_entry("stream-run", status="complete", has_final_output=True)]
            )
        )

        lev_path = fake.build()

        snapshots = []
        async with LeviathClient(lev_binary=lev_path) as client:
            async for snap in client.stream("stream-run", interval=0.01):
                snapshots.append(snap)

        assert len(snapshots) == 3
        assert snapshots[0].status == RunStatus.ACTIVE
        assert snapshots[1].status == RunStatus.ACTIVE
        assert snapshots[2].status == RunStatus.COMPLETE


# ── tests: client.control ───────────────────────────────────────────────────


class TestControl:
    @pytest.mark.asyncio
    async def test_cancel(self, fake_lev_dir):
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout="", rc=0)  # lev cancel succeeds

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.cancel("run-to-cancel")
            assert result is True

        calls = fake.calls()
        assert calls[0][:2] == ["cancel", "run-to-cancel"]

    @pytest.mark.asyncio
    async def test_cancel_force(self, fake_lev_dir):
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout="", rc=0)

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.cancel("run-force", force=True)
            assert result is True

        assert "--force" in fake.calls()[0]

    @pytest.mark.asyncio
    async def test_cancel_failure_raises(self, fake_lev_dir):
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stderr="no such run", rc=1)

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            with pytest.raises(LeviathError, match="no such run"):
                await client.cancel("ghost")

    @pytest.mark.asyncio
    async def test_pause(self, fake_lev_dir):
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout="", rc=0)

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.pause("run-pause")
            assert result is True

        assert fake.calls()[0][:2] == ["pause", "run-pause"]

    @pytest.mark.asyncio
    async def test_resume(self, fake_lev_dir):
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout="", rc=0)

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.resume("run-resume")
            assert result is True

        assert fake.calls()[0][:2] == ["resume", "run-resume"]


# ── tests: client.list_runs ─────────────────────────────────────────────────


class TestListRuns:
    @pytest.mark.asyncio
    async def test_list_runs_parses_entries(self, fake_lev_dir):
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(
            stdout=make_ps_json(
                [
                    make_ps_entry("run-a", status="complete"),
                    make_ps_entry("run-b", status="active"),
                ]
            )
        )

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            runs = await client.list_runs()

        assert len(runs) == 2
        assert runs[0].run_id == "run-a"
        assert runs[0].status == RunStatus.COMPLETE
        assert runs[1].run_id == "run-b"
        assert runs[1].status == RunStatus.ACTIVE


# ── tests: client.result ────────────────────────────────────────────────────


class TestResult:
    @pytest.mark.asyncio
    async def test_result_returns_output(self, fake_lev_dir):
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout="the final answer\n")

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            output = await client.result("run-done")

        assert output == "the final answer\n"
        assert fake.calls()[0][:2] == ["result", "run-done"]

    @pytest.mark.asyncio
    async def test_result_raw_flag(self, fake_lev_dir):
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout="raw output")

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            output = await client.result("run-done", raw=True)

        assert "--raw" in fake.calls()[0]


# ── tests: spawn errors ─────────────────────────────────────────────────────


class TestSpawnErrors:
    @pytest.mark.asyncio
    async def test_spawn_nonzero_exit_raises(self, fake_lev_dir):
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stderr="agent not found", rc=1)

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            with pytest.raises(LeviathSpawnError, match="agent not found"):
                await client.run("nonexistent")

    @pytest.mark.asyncio
    async def test_missing_binary_raises(self, fake_lev_dir):
        async with LeviathClient(
            lev_binary="/nonexistent/path/lev"
        ) as client:
            with pytest.raises(LeviathSpawnError, match="not found"):
                await client.run("test-agent")


# ── tests: backoff ──────────────────────────────────────────────────────────


class TestBackoff:
    @pytest.mark.asyncio
    async def test_polling_uses_backoff(self, fake_lev_dir):
        """Verify that after many polls, interval grows (not a precise test,
        but it exercises the backoff code path)."""
        fake = FakeLev(fake_lev_dir)
        fake.queue_response(stdout=make_run_json("run-backoff"))

        # Many non-terminal responses, then terminal
        for _ in range(5):
            fake.queue_response(
                stdout=make_ps_json(
                    [make_ps_entry("run-backoff", status="active")]
                )
            )
        fake.queue_response(
            stdout=make_ps_json(
                [make_ps_entry("run-backoff", status="complete", has_final_output=True)]
            )
        )
        fake.queue_response(stdout="backoff-done")

        lev_path = fake.build()

        async with LeviathClient(lev_binary=lev_path) as client:
            result = await client.run_and_wait(
                agent="test-agent",
                poll_interval=0.01,
                backoff_factor=2.0,
                max_poll_interval=0.1,
            )

        assert result.status == RunStatus.COMPLETE
        # We made at least 6 ps calls (5 active + 1 complete)
        ps_calls = [c for c in fake.calls() if c[0] == "ps"]
        assert len(ps_calls) >= 6