# leviath — async Python client for Leviath

An asyncio-native Python wrapper around the `lev` CLI that spawns runs and
awaits their completion, replacing fragile file-polling loops with a proper
`await client.run_and_wait(...)` affordance.

## Quick start

```python
import asyncio
from leviath import LeviathClient

async def main():
    async with LeviathClient() as client:
        result = await client.run_and_wait(
            agent="bake-off",
            task="Your task here",
            workdir="/data/work/leviath/study",
            timeout=1200,  # seconds
        )
        print(f"Run {result.run_id} finished: {result.status}")
        print(result.final_output)

asyncio.run(main())
```

## API

- `LeviathClient(lev_binary="lev")` — async context manager
- `await client.run(...)` — spawn a run, return `run_id`
- `await client.run_and_wait(...)` — spawn + poll until terminal, return `RunResult`
- `await client.status(run_id)` — poll one run's status
- `await client.result(run_id)` — fetch final output
- `await client.cancel(run_id)` — cancel a run
- `await client.pause(run_id)` / `await client.resume(run_id)` — pause/resume
- `await client.list_runs()` — list all runs
- `async for event in client.stream(run_id, interval=5)` — streaming status events

## Design invariants

1. `run_and_wait` only returns when the run reaches a **terminal** status
   (`complete`, `error`, `cancelled`). It never silently assumes completion.
2. Timeout raises `LeviathTimeoutError` loudly — the run continues; caller
   can `cancel()` or `run_and_wait()` again with the same `run_id`.
3. Polling uses exponential backoff (1s → 30s cap) to avoid hammering the
   daemon.