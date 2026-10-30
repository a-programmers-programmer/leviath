"""Async Leviath client — spawn runs and await their completion.

Talks to the `lev` binary via subprocess. The CLI is the stable interface:
`lev run --json` spawns, `lev ps --json` polls, `lev result` fetches output,
`lev cancel/pause/resume` for control.

Design invariants:
1. run_and_wait only returns when the run reaches a terminal status.
2. Timeout raises LeviathTimeoutError loudly; the run continues.
3. Polling uses exponential backoff (1s → 30s cap).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import AsyncIterator, Optional

from leviath.errors import (
    LeviathDaemonError,
    LeviathError,
    LeviathSpawnError,
    LeviathTimeoutError,
)
from leviath.models import (
    TERMINAL_LABELS,
    RunListEntry,
    RunResult,
    RunStatus,
    TerminalStatus,
)


class LeviathClient:
    """Async client for the Leviath agent runtime.

    Talks to the `lev` CLI binary via asyncio subprocess. Use as an async
    context manager for clean subprocess lifecycle:

        async with LeviathClient() as client:
            result = await client.run_and_wait(agent="bake-off", task="...")
    """

    def __init__(
        self,
        lev_binary: str = "lev",
        runs_dir: str | None = None,
        env: dict[str, str] | None = None,
    ):
        """Create a client.

        Args:
            lev_binary: Path or name of the `lev` binary.
            runs_dir: Override the runs directory (default: ~/.leviath/runs).
            env: Extra environment variables to pass to every `lev` invocation.
        """
        self._lev = lev_binary
        self._runs_dir = runs_dir or os.path.expanduser("~/.leviath/runs")
        self._env = dict(env) if env else {}
        self._semaphore: asyncio.Semaphore | None = None

    # ── context manager ──────────────────────────────────────────────────

    async def __aenter__(self) -> "LeviathClient":
        # Semaphore limits concurrent subprocess spawns to avoid thundering
        # the daemon. Default: 4 concurrent spawns.
        self._semaphore = asyncio.Semaphore(4)
        return self

    async def __aexit__(self, *args: object) -> None:
        self._semaphore = None

    # ── spawn ────────────────────────────────────────────────────────────

    async def run(
        self,
        agent: str,
        *,
        task: str | None = None,
        workdir: str | None = None,
        model: str | None = None,
        yolo: bool = True,
        allow: list[str] | None = None,
        max_depth: int | None = None,
        no_seed_commands: bool = False,
        regions: dict[str, str] | None = None,
        output_format: str | None = None,
        output_instructions: str | None = None,
        output_schema: dict | None = None,
        count: int = 1,
        timeout: float = 30.0,
    ) -> str | list[str]:
        """Spawn a run (or runs) and return the run_id(s).

        Args:
            agent: Agent name, path, or directory.
            task: The task string.
            workdir: Absolute working directory.
            model: Model override.
            yolo: Run unattended (default True).
            allow: Tools to allow outright.
            max_depth: Cap on sub-agent tree depth.
            no_seed_commands: Refuse blueprint command seeds.
            regions: Named caller-input regions.
            output_format: Request a specific output shape.
            output_instructions: Extra guidance about the shape.
            output_schema: JSON Schema the output must satisfy.
            count: Number of runs to spawn (default 1).
            timeout: Seconds to wait for the spawn subprocess.

        Returns:
            A single run_id string (count=1) or a list of run_id strings.

        Raises:
            LeviathSpawnError: The spawn command failed.
        """
        cmd = [self._lev, "run", agent, "--json"]
        if task is not None:
            cmd.extend(["--task", task])
        if workdir is not None:
            cmd.extend(["--workdir", workdir])
        if model is not None:
            cmd.extend(["--model", model])
        if yolo:
            cmd.append("--yolo")
        for tool in allow or []:
            cmd.extend(["--allow", tool])
        if max_depth is not None:
            cmd.extend(["--max-depth", str(max_depth)])
        if no_seed_commands:
            cmd.append("--no-seed-commands")
        if regions:
            for k, v in regions.items():
                cmd.extend(["--region", f"{k}={v}"])
        if output_format is not None:
            cmd.extend(["--output-format", output_format])
        if output_instructions is not None:
            cmd.extend(["--output-instructions", output_instructions])
        if output_schema is not None:
            cmd.extend(["--output-schema", json.dumps(output_schema)])
        if count > 1:
            cmd.extend(["--count", str(count)])

        env = {**os.environ, **self._env}

        sem = self._semaphore
        if sem is not None:
            async with sem:
                return await self._spawn_one(cmd, env, timeout, count)
        return await self._spawn_one(cmd, env, timeout, count)

    async def _spawn_one(
        self,
        cmd: list[str],
        env: dict[str, str],
        timeout: float,
        count: int,
    ) -> str | list[str]:
        """Execute `lev run --json` and parse the run_id(s)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            raise LeviathSpawnError(
                f"lev run timed out after {timeout}s"
            )
        except FileNotFoundError:
            raise LeviathSpawnError(
                f"lev binary not found: {self._lev}"
            )
        except Exception as e:
            raise LeviathSpawnError(f"lev run failed: {e}")

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            out_text = stdout.decode("utf-8", errors="replace").strip()
            raise LeviathSpawnError(
                f"lev run exited {proc.returncode}: {err_text or out_text}"
            )

        out_text = stdout.decode("utf-8", errors="replace").strip()
        if not out_text:
            raise LeviathSpawnError("lev run produced no output")

        try:
            parsed = json.loads(out_text)
        except json.JSONDecodeError:
            raise LeviathSpawnError(
                f"lev run produced invalid JSON: {out_text[:200]}"
            )

        return self._extract_run_ids(parsed, count)

    def _extract_run_ids(self, parsed: object, count: int) -> str | list[str]:
        """Pull run_id(s) from the JSON `lev run --json` printed."""
        if isinstance(parsed, list):
            ids = [entry["run_id"] for entry in parsed]
            if count == 1 and len(ids) == 1:
                return ids[0]
            return ids
        if isinstance(parsed, dict):
            run_id = parsed.get("run_id")
            if run_id:
                return run_id
        raise LeviathSpawnError(
            f"could not find run_id in lev run output: {str(parsed)[:200]}"
        )

    # ── status ───────────────────────────────────────────────────────────

    async def status(self, run_id: str) -> RunResult:
        """Poll the current status of one run.

        Reads `lev ps --json` to find the run. Falls back to reading
        meta.json from disk if the daemon doesn't know about it.
        """
        entries = await self._ps_json(all_runs=True)
        for entry in entries:
            if entry.get("run_id") == run_id:
                return RunResult.from_ps_entry(entry)

        # Not in daemon memory — try disk
        return await self._status_from_disk(run_id)

    async def _status_from_disk(self, run_id: str) -> RunResult:
        """Read a run's meta.json from disk."""
        meta_path = Path(self._runs_dir) / run_id / "meta.json"
        if not meta_path.exists():
            # Try to find it by prefix match
            runs_root = Path(self._runs_dir)
            if runs_root.exists():
                for child in runs_root.iterdir():
                    if child.name.startswith(run_id) or run_id in child.name:
                        meta_path = child / "meta.json"
                        if meta_path.exists():
                            break

        if not meta_path.exists():
            raise LeviathError(f"no run found for id '{run_id}'")

        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            raise LeviathError(f"failed to read meta.json for {run_id}: {e}")

        status = RunStatus.from_wire(meta.get("status", "unknown"))
        # Check for final_output
        final_output_path = meta_path.parent / "final_output"
        final_output = None
        if final_output_path.exists():
            try:
                final_output = final_output_path.read_text()
            except OSError:
                pass

        return RunResult(
            run_id=meta.get("run_id", run_id),
            status=status,
            stage=meta.get("current_stage"),
            iteration=meta.get("iteration", 0),
            final_output=final_output,
            error=meta.get("error"),
            prompt_tokens=meta.get("prompt_tokens", 0),
            completion_tokens=meta.get("completion_tokens", 0),
            raw=meta,
        )

    # ── result ───────────────────────────────────────────────────────────

    async def result(self, run_id: str, *, raw: bool = False) -> str:
        """Fetch the final output of a finished run via `lev result`.

        Args:
            run_id: The run to fetch output from.
            raw: If True, return only the answer text (--raw flag).

        Returns:
            The final output text.

        Raises:
            LeviathError: The run has no final output, or the command failed.
        """
        cmd = [self._lev, "result", run_id]
        if raw:
            cmd.append("--raw")

        env = {**os.environ, **self._env}
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            raise LeviathError(f"lev binary not found: {self._lev}")
        except Exception as e:
            raise LeviathError(f"lev result failed: {e}")

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            raise LeviathError(
                f"lev result failed for {run_id}: {err_text}"
            )

        return stdout.decode("utf-8", errors="replace")

    # ── run_and_wait — the main affordance ───────────────────────────────

    async def run_and_wait(
        self,
        agent: str,
        *,
        task: str | None = None,
        workdir: str | None = None,
        model: str | None = None,
        yolo: bool = True,
        allow: list[str] | None = None,
        max_depth: int | None = None,
        no_seed_commands: bool = False,
        regions: dict[str, str] | None = None,
        output_format: str | None = None,
        output_instructions: str | None = None,
        output_schema: dict | None = None,
        timeout: float = 0,
        poll_interval: float = 1.0,
        max_poll_interval: float = 30.0,
        backoff_factor: float = 1.5,
    ) -> RunResult:
        """Spawn a run and wait for it to reach a terminal status.

        Polls `status()` with exponential backoff. Returns the RunResult
        when the run is terminal (complete/error/cancelled).

        Args:
            agent: Agent name, path, or directory.
            task: The task string.
            workdir: Absolute working directory.
            model: Model override.
            yolo: Run unattended (default True).
            allow: Tools to allow outright.
            max_depth: Cap on sub-agent tree depth.
            no_seed_commands: Refuse blueprint command seeds.
            regions: Named caller-input regions.
            output_format: Request a specific output shape.
            output_instructions: Extra guidance about the shape.
            output_schema: JSON Schema the output must satisfy.
            timeout: Max seconds to wait (0 = no timeout). The run continues
                     after a timeout; call cancel() or run_and_wait() again.
            poll_interval: Initial poll interval in seconds.
            max_poll_interval: Maximum poll interval after backoff.
            backoff_factor: Multiplier for each poll interval.

        Returns:
            RunResult with final_output populated when available.

        Raises:
            LeviathTimeoutError: The run did not finish within the timeout.
            LeviathSpawnError: The spawn command failed.
        """
        run_id = await self.run(
            agent=agent,
            task=task,
            workdir=workdir,
            model=model,
            yolo=yolo,
            allow=allow,
            max_depth=max_depth,
            no_seed_commands=no_seed_commands,
            regions=regions,
            output_format=output_format,
            output_instructions=output_instructions,
            output_schema=output_schema,
        )
        if isinstance(run_id, list):
            raise LeviathError(
                "run_and_wait does not support count > 1; use run() directly"
            )
        return await self.wait(
            run_id,
            timeout=timeout,
            poll_interval=poll_interval,
            max_poll_interval=max_poll_interval,
            backoff_factor=backoff_factor,
        )

    async def wait(
        self,
        run_id: str,
        *,
        timeout: float = 0,
        poll_interval: float = 1.0,
        max_poll_interval: float = 30.0,
        backoff_factor: float = 1.5,
    ) -> RunResult:
        """Wait for an existing run to reach a terminal status.

        Args:
            run_id: The run to wait on.
            timeout: Max seconds to wait (0 = no timeout).
            poll_interval: Initial poll interval in seconds.
            max_poll_interval: Maximum poll interval after backoff.
            backoff_factor: Multiplier for each poll interval.

        Returns:
            RunResult with final_output populated when available.

        Raises:
            LeviathTimeoutError: The run did not finish within the timeout.
        """
        deadline = time.monotonic() + timeout if timeout > 0 else float("inf")
        interval = poll_interval
        last_status = "unknown"

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LeviathTimeoutError(run_id, timeout, last_status)

            try:
                result = await self.status(run_id)
            except LeviathError:
                # Run not found yet — it may still be starting
                await asyncio.sleep(min(interval, max(remaining, 0.5)))
                interval = min(interval * backoff_factor, max_poll_interval)
                continue

            last_status = result.status.value

            if result.status.is_terminal:
                # Fetch final output if available
                if result.final_output is None:
                    try:
                        result.final_output = await self.result(run_id, raw=True)
                    except LeviathError:
                        pass  # No final output is OK for some runs
                return result

            await asyncio.sleep(min(interval, max(remaining, 0.5)))
            interval = min(interval * backoff_factor, max_poll_interval)

    # ── stream ───────────────────────────────────────────────────────────

    async def stream(
        self,
        run_id: str,
        *,
        interval: float = 5.0,
        timeout: float = 0,
    ) -> AsyncIterator[RunResult]:
        """Yield status snapshots until the run is terminal.

        Args:
            run_id: The run to stream.
            interval: Seconds between polls.
            timeout: Max seconds (0 = no timeout).

        Yields:
            RunResult for each poll, including the terminal one.
        """
        deadline = time.monotonic() + timeout if timeout > 0 else float("inf")

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            try:
                result = await self.status(run_id)
            except LeviathError:
                await asyncio.sleep(min(interval, max(remaining, 0.5)))
                continue

            yield result

            if result.status.is_terminal:
                break

            await asyncio.sleep(min(interval, max(remaining, 0.5)))

    # ── control ──────────────────────────────────────────────────────────

    async def cancel(self, run_id: str, *, force: bool = False) -> bool:
        """Cancel a run.

        Args:
            run_id: The run to cancel.
            force: Use --force to write cancellation to disk directly
                   (for when the daemon is unreachable).

        Returns:
            True if the cancellation was applied.
        """
        cmd = [self._lev, "cancel", run_id]
        if force:
            cmd.append("--force")
        return await self._control_cmd(cmd, "cancel")

    async def pause(self, run_id: str) -> bool:
        """Pause a running agent."""
        return await self._control_cmd([self._lev, "pause", run_id], "pause")

    async def resume(self, run_id: str) -> bool:
        """Resume a paused agent."""
        return await self._control_cmd([self._lev, "resume", run_id], "resume")

    async def _control_cmd(self, cmd: list[str], action: str) -> bool:
        """Run a control command (cancel/pause/resume) and return success."""
        env = {**os.environ, **self._env}
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            raise LeviathError(f"lev binary not found: {self._lev}")
        except Exception as e:
            raise LeviathError(f"lev {action} failed: {e}")

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            out_text = stdout.decode("utf-8", errors="replace").strip()
            raise LeviathError(
                f"lev {action} failed for run: {err_text or out_text}"
            )
        return True

    # ── list ─────────────────────────────────────────────────────────────

    async def list_runs(
        self, *, limit: int = 20, all_runs: bool = True
    ) -> list[RunListEntry]:
        """List runs, newest first.

        Args:
            limit: Max entries to return.
            all_runs: Include finished runs on disk (--all flag).

        Returns:
            List of RunListEntry.
        """
        entries = await self._ps_json(all_runs=all_runs)
        entries = entries[:limit]
        return [
            RunListEntry(
                run_id=e.get("run_id", ""),
                status=RunStatus.from_wire(e.get("status", "unknown")),
                stage=e.get("stage"),
                title=e.get("title"),
                has_final_output=e.get("has_final_output", False),
                live=e.get("live", False),
                started_at=e.get("started_at"),
            )
            for e in entries
        ]

    async def _ps_json(self, all_runs: bool = True) -> list[dict]:
        """Run `lev ps --json [--all]` and return the merged run list."""
        cmd = [self._lev, "ps", "--json"]
        if all_runs:
            cmd.append("--all")

        env = {**os.environ, **self._env}
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            raise LeviathDaemonError(f"lev binary not found: {self._lev}")
        except Exception as e:
            raise LeviathDaemonError(f"lev ps failed: {e}")

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            raise LeviathDaemonError(f"lev ps failed: {err_text}")

        try:
            parsed = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            raise LeviathDaemonError("lev ps produced invalid JSON")

        runs = list(parsed.get("runs", []))
        finished = list(parsed.get("finished", []))
        not_running = list(parsed.get("not_running", []))
        return runs + finished + not_running