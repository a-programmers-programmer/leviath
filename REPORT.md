# GQL-24-A: typed adapter over the real daemon

Status: COMPLETE. Run id R01M2NCFR0TT971HMFJ6835396Y.

## Deliverable

A typed Python adapter over the real Leviath daemon. Package: `levdaemon/`.
It covers runs, blueprints, stages, artifacts (blobs) and commands.

Done check (contract):
`test -s REPORT.md && test -s EVIDENCE.json && json.load(EVIDENCE.json) && pytest -q tests/test_daemon_adapter.py`

Result: 23 passed, 0 skipped, in 0.4s. Live tests ran, they did not skip.

## Why the types are real, not guessed

I captured live daemon responses into `fixtures/` first. Then I wrote types.
Two transports wrote the captures:

- control socket: `/data/.leviath/control.sock`, newline-delimited JSON.
- `lev` CLI (`lev 0.5.9`, inner build `lev-0.6.0-36025f69-v2`) with `--json`.

`fixtures/MANIFEST.json` records each capture, its run id, its type and its key set.

## Types and the surface each came from

| Surface | Fixture | Type |
|---|---|---|
| control `list` | `fixtures/control_list.json` | `RunList`, `Run`, `Health` |
| `lev ps --json` | `fixtures/cli_ps.json` | `RunList`, `Run` (adds `age_secs`, `working_secs`) |
| control `authenticate` hello | `fixtures/control_welcome.json` | `Welcome`, `DaemonInfo` |
| control `status` | `fixtures/control_status.json` | `StatusReply` |
| control `list_interactions` | `fixtures/control_interactions.json` | `InteractionsReply` |
| `lev stages --json` | `fixtures/cli_stages_live.json` | `Stage`, `StageVisit`, `RegionTokens` |
| `lev timeline --json` | `fixtures/cli_timeline_live.json` | `Timeline`, `StageCall`, `Totals` |
| `lev blobs --json` | `fixtures/blobs_sample.json` | `Blob` |
| `lev list --json` | `fixtures/cli_list.json` | `BlueprintList`, `Blueprint` |
| `lev --help` | `fixtures/cli_commands.json` | `CommandCatalogue`, `Command` |
| `lev result --json` | `fixtures/result.json` | `FinalOutput` |

Every type subclasses `Record`. `Record.from_dict` rejects an unknown key,
a missing required key and a wrong type. So daemon drift raises `SchemaError`.
It never silently drops a field. This is the validation mechanism.

## Real findings from the capture

1. There is no `lev commands` subcommand. `lev commands --json` exits rc=2.
   The only machine-readable command catalogue is the grouped `lev --help` text.
   `levdaemon/commands.py` parses that text into `CommandCatalogue`.
   Groups are "Setup and configuration", "Blueprints", "Running agents",
   "Inspecting runs", "Servers" (5 groups, 36 commands).
2. The CLI and the control socket send different run shapes. The CLI adds
   `age_secs` and `working_secs`; the control socket adds `result`. One `Run`
   type parses both shapes, because the extra fields are optional.
3. `region_tokens` is a flat map of region name to token count. The region set
   differs per run. A fixed field list would be a guess, so `RegionTokens`
   keeps the map and validates each value is a non-negative int.
4. `lev result` exits rc=1 when a run has no final output yet. The adapter
   returns `None` for that case instead of raising.
5. `lev blobs` returns an empty list for many live runs. An empty list is valid.
6. control `status` returns `null` for a run id the daemon does not know.
7. `read_paths` was absent on some live runs, so `Run.read_paths` is optional.
8. `health.inference` reports the same model twice under two spellings
   (`deepseek-v4.1-flash` and `deepseek/deepseek-v4.1-flash`).
9. Blueprint `read_paths` is a text line, not a number.
   `Blueprint.declared_read_paths` parses `read_paths: 3 declared, 3 granted`.

## Fleet bug found

`harness.py precheck` and `declare-artifact` cannot run for this task.
The stored check for this task is a plain string, not a typed dict.
`checks.run_check` calls `check.get("type")` and raises
`AttributeError: 'str' object has no attribute 'get'`.
So I verified the contract check by hand with the shell command instead.
Traceback is recorded in `EVIDENCE.json` under `harness_bug`.

## Files

- `levdaemon/record.py` - strict `Record` base, `SchemaError`.
- `levdaemon/models_runs.py` - runs, health, blueprints, commands, blobs, result.
- `levdaemon/models_stages.py` - stages, visits, region tokens, timeline.
- `levdaemon/control.py` - control-socket transport (auth, list, status, interactions).
- `levdaemon/cli.py` - `lev --json` transport.
- `levdaemon/commands.py` - `lev --help` to `CommandCatalogue`.
- `levdaemon/__init__.py` - `DaemonAdapter`, `Capabilities`, re-exports.
- `tests/test_daemon_adapter.py` - 19 offline type tests, 4 live tests.
- `fixtures/` - real captured daemon responses.
- `tmp/` - capture scripts and probe logs (scratch).

## Evidence

See `EVIDENCE.json` for checkpoints, the done check output and fixture paths.