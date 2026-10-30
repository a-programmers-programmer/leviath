# HANDOFF-NEXT — leviath Python async client

## What this is
A proper asyncio-native Python wrapper around the Leviath agent runtime (`lev` CLI).
Replaces fragile file-polling loops with `await client.run_and_wait(...)`.

## Files
- `/data/work/leviath/python/pyproject.toml` — package metadata
- `/data/work/leviath/python/README.md` — API docs
- `/data/work/leviath/python/leviath/__init__.py` — public exports
- `/data/work/leviath/python/leviath/client.py` — async client (LeviathClient)
- `/data/work/leviath/python/leviath/models.py` — data models (RunResult, RunStatus, TerminalStatus)
- `/data/work/leviath/python/leviath/errors.py` — exception types
- `/data/work/leviath/python/tests/test_client.py` — 26 passing tests (fake lev binary)
- `/data/work/leviath/study/run_bakeoff3.py` — refactored bake-off runner using the wrapper

## Key design invariants (enforced in code)
1. `run_and_wait` only returns when the run reaches a terminal status (complete/error/cancelled).
2. Timeout raises `LeviathTimeoutError` loudly — the run continues; caller can cancel() or wait() again.
3. Polling uses exponential backoff (1s → 30s cap).

## Verified against real daemon
- Spawn: ✓ (run_id returned from `lev run --json`)
- Status polling: ✓ (active → cancelled transition observed)
- Wait on already-completed run: ✓ (returns immediately with final_output)
- Cancel: ✓ (run transitions to cancelled)
- Result: ✓ (fetches final_output via `lev result --raw`)
- List runs: ✓ (parses `lev ps --json --all`)
- Stream: ✓ (yields status snapshots until terminal/timeout)

## Next owner tasks
1. The bake-off runner (`run_bakeoff3.py`) is ready but untested end-to-end (requires a full bake-off cycle ~20 min).
   Run: `cd /data/work/leviath/study && python3 run_bakeoff3.py`
2. Consider adding MCP stdio transport as an alternative backend (currently CLI-only).
3. Add `run_many_and_wait` for batched concurrent runs with count > 1.
4. Package for PyPI if desired (`pip install leviath`).

## Running tests
```
cd /data/work/leviath/python
pip install -e ".[dev]"
pytest tests/ -v
```