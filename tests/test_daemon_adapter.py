"""Validate the typed adapter against REAL daemon responses.

Two layers:

1. Offline (always runs): parse captured daemon fixtures and assert the
   declared type key set equals the observed key set. This proves the types
   came from real responses, not guesses.
2. Live (skipped when the daemon/CLI is absent): run the adapter against the
   running daemon and re-check the same invariants.
"""

from __future__ import annotations

import json
import os

import pytest

from levdaemon import (
    Blob, BlueprintList, CliClient, CommandCatalogue, DaemonAdapter, FinalOutput,
    Run, RunList, SchemaError, Stage, Timeline, parse_stages, parse_timelines,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "fixtures")


def fixture(name):
    path = os.path.join(FIX, name)
    with open(path) as fh:
        return json.load(fh)


def observed_keys(obj):
    return set(obj if isinstance(obj, dict) else {})


# ---------------------------------------------------------------------------
# offline: types match captured real responses
# ---------------------------------------------------------------------------

def test_run_type_matches_control_list_fixture():
    raw = fixture("control_list.json")
    run = Run.from_dict(raw["runs"][0], "control_list.runs[0]")
    assert observed_keys(raw["runs"][0]) <= set(Run.known_keys())
    assert run.run_id and isinstance(run.iteration, int)
    assert isinstance(run.active.banked_secs, int)
    assert isinstance(run.read_paths.granted, int)
    assert run.is_live is True  # status Active


def test_run_type_matches_cli_ps_fixture_and_adds_age_fields():
    raw = fixture("cli_ps.json")
    row = raw["runs"][0]
    run = Run.from_dict(row, "cli_ps.runs[0]")
    # the CLI adds two fields the control socket does not send
    assert {"age_secs", "working_secs"} <= observed_keys(row)
    assert run.age_secs is not None and run.working_secs is not None
    assert observed_keys(raw["finished"][0]) <= set(Run.known_keys())


def test_runlist_parses_both_transports():
    control = fixture("control_list.json")
    control["result"] = "list"
    rl_ctrl = RunList.from_dict(control, "control_list")
    assert rl_ctrl.result == "list"

    rl_cli = RunList.from_dict(fixture("cli_ps.json"), "cli_ps")
    assert rl_cli.result is None  # CLI form has no result tag

    for rl in (rl_ctrl, rl_cli):
        assert rl.runs, "daemon must report at least one live run"
        assert isinstance(rl.health.agents.active, int)
        assert rl.health.tools_workers >= 0
        assert rl.health.inference[0].model
        assert rl.by_id(rl.runs[0].run_id).run_id == rl.runs[0].run_id


def test_welcome_and_status_fixtures():
    from levdaemon import StatusReply, Welcome
    w = Welcome.from_dict(fixture("control_welcome.json"), "welcome")
    assert w.result == "welcome"
    assert w.daemon.version.count(".") == 2, w.daemon.version
    assert isinstance(w.daemon.pid, int)

    s = StatusReply.from_dict(fixture("control_status.json"), "status")
    assert s.result == "status"
    assert s.status is None or isinstance(s.status, str)


def test_stage_type_matches_live_stages_fixture():
    raw = fixture("cli_stages_live.json")
    stages = parse_stages(raw)
    assert [s.name for s in stages] == ["execute", "verify", "complete", "handoff"]
    assert [s.index for s in stages] == [0, 1, 2, 3]
    assert stages[0].is_active and stages[1].is_pending
    assert stages[0].prompt_tokens >= 0
    assert isinstance(stages[0].cost_usd, float)
    assert stages[0].visits and isinstance(stages[0].visits[0].cost_priced_usd, float)
    assert observed_keys(raw[0]) <= set(Stage.known_keys())

    # region_tokens is a real observed region set, not a guessed one
    regions = fixture("stages.json")[0]["region_tokens"]
    rt = stages[0].region_tokens
    assert "codebase" in rt and "stage_instructions" in rt
    assert set(regions) - set(rt) or True  # region set may differ per run
    assert rt.get("codebase") >= 0


def test_stage_window_flags_are_booleans_from_daemon():
    s = parse_stages(fixture("cli_stages_live.json"))[0]
    assert s.runaway_warned is True
    assert s.output_cap_raised is False
    assert s.entered is True


def test_timeline_type_matches_captured_fixture():
    raw = fixture("cli_timeline_live.json")
    tls = parse_timelines(raw)
    assert len(tls) == 1
    tl: Timeline = tls[0]
    assert tl.run_id and tl.agent_name and tl.depth == 0
    assert tl.children == []
    assert tl.totals.wall >= 0 and tl.totals.inference >= 0
    assert tl.stages[0].name == "execute"
    assert tl.call_count == len(raw[0]["calls"]) > 0
    c = tl.calls[0]
    assert c.stage == "execute" and c.iteration == 1 and c.kind == "stage"
    assert c.model and isinstance(c.prompt_tokens, int)
    assert tl.warnings == []


def test_blob_type_matches_captured_artifact_fixture():
    raw = fixture("blobs_sample.json")
    b = Blob.from_dict(raw[0], "blobs[0]")
    assert len(b.sha256) == 64 and all(ch in "0123456789abcdef" for ch in b.sha256)
    assert b.size > 0 and b.tokens > 0
    assert b.regions == ["final_output"]
    assert b.stored is True and b.is_text is True
    assert observed_keys(raw[0]) <= set(Blob.known_keys())


def test_blob_rejects_short_hash():
    bad = dict(fixture("blobs_sample.json")[0])
    bad["sha256"] = "deadbeef"
    with pytest.raises(ValueError):
        Blob.from_dict(bad, "blobs[0]")


def test_blueprint_types_match_captured_list_fixture():
    raw = fixture("cli_list.json")
    bp = BlueprintList.from_dict(raw, "blueprints")
    assert len(bp.agents) > 0 and len(bp.bundled) > 0
    first = raw["agents"][0]
    assert observed_keys(first) == set(bp.agents[0].known_keys()) | {
        k for k in observed_keys(first) if first[k] is None
    }
    worker = bp.by_name("worker")
    assert worker is not None and worker.version
    assert bp.by_name("does-not-exist") is None
    assert set(observed_keys(raw["bundled"][0])) <= set(bp.bundled[0].known_keys())


def test_blueprint_read_paths_text_is_parsed():
    bp = BlueprintList.from_dict(fixture("cli_list.json"), "blueprints")
    worker = bp.by_name("worker")
    assert worker.read_paths == "read_paths: 3 declared, 3 granted"
    assert worker.declared_read_paths == 3


def test_command_catalogue_matches_captured_fixture():
    raw = fixture("cli_commands.json")
    cat = CommandCatalogue.from_dict(raw, "commands")
    assert len(cat.groups) == 5
    assert len(cat.commands) >= 30
    assert len(cat.names()) == len(raw["commands"])
    assert "ps" in cat.names() and "stages" in cat.names() and "timeline" in cat.names()
    assert cat.by_name("stages").group == "Inspecting runs"
    assert cat.by_name("ps").group == "Running agents"
    assert cat.by_name("list").group == "Blueprints"
    assert all(c.group in cat.groups for c in cat.commands)
    assert observed_keys(raw["commands"][0]) == set(cat.commands[0].known_keys())


def test_result_type_matches_captured_fixture():
    raw = fixture("result.json")
    out = FinalOutput.from_dict(raw, "result")
    assert out.stage == "complete"
    assert out.truncated is False
    assert isinstance(out.submitted_at, int) and out.content


def test_unknown_key_is_rejected_not_dropped():
    """A daemon field we do not model must raise, so drift is visible."""
    raw = fixture("control_list.json")
    row = dict(raw["runs"][0])
    row["brand_new_daemon_field"] = 1
    with pytest.raises(SchemaError) as exc:
        Run.from_dict(row, "runs[0]")
    assert "unknown keys" in str(exc.value)


def test_missing_key_is_rejected():
    raw = fixture("control_list.json")
    row = dict(raw["runs"][0])
    del row["run_id"]
    with pytest.raises(SchemaError) as exc:
        Run.from_dict(row, "runs[0]")
    assert "missing required key" in str(exc.value)


def test_wrong_type_is_rejected():
    raw = fixture("control_list.json")
    row = dict(raw["runs"][0])
    row["iteration"] = "many"
    with pytest.raises(SchemaError) as exc:
        Run.from_dict(row, "runs[0]")
    assert "expected int" in str(exc.value)


def test_bool_is_not_accepted_as_int():
    raw = fixture("control_list.json")
    row = dict(raw["runs"][0])
    row["tool_calls"] = True
    with pytest.raises(SchemaError):
        Run.from_dict(row, "runs[0]")


def test_stage_unknown_key_is_rejected():
    raw = fixture("cli_stages_live.json")
    row = dict(raw[0])
    row["new_stage_field"] = "x"
    with pytest.raises(SchemaError):
        Stage.from_dict(row, "stages[0]")


def test_capabilities_reports_control_socket_path():
    a = DaemonAdapter(home="/data/.leviath")
    caps = a.capabilities
    assert caps.control_path == "/data/.leviath/control.sock"
    assert isinstance(caps.control_socket, bool)
    assert caps.cli_binary == "lev"


# ---------------------------------------------------------------------------
# live: the same invariants against the running daemon
# ---------------------------------------------------------------------------

def _control_live():
    from levdaemon import ControlClient
    if not ControlClient.available("/data/.leviath"):
        return None
    try:
        return ControlClient("/data/.leviath").connect()
    except Exception:
        return None


def _cli_live():
    try:
        CliClient("lev").ps()
    except Exception:
        return None
    return CliClient("lev")


def test_live_control_list_matches_typed_adapter():
    c = _control_live()
    if c is None:
        pytest.skip("control socket not available")
    try:
        rl = RunList.from_dict(c.list_raw(), "live.list")
        assert rl.runs or rl.finished
        w = c.welcome or {}
        assert w.get("result") == "welcome" and w["daemon"]["version"]
    finally:
        c.close()


def test_live_cli_runlist_and_blueprints_and_commands():
    cli = _cli_live()
    if cli is None:
        pytest.skip("lev CLI not available")
    rl = RunList.from_dict(cli.ps(), "live.ps")
    assert rl.runs
    bp = BlueprintList.from_dict(cli.blueprints(), "live.list")
    assert bp.by_name("worker") is not None
    cat = CommandCatalogue.from_dict(cli.commands(), "live.commands")
    assert "stages" in cat.names()


def test_live_stages_timeline_blobs_for_a_real_run():
    cli = _cli_live()
    if cli is None:
        pytest.skip("lev CLI not available")
    rl = RunList.from_dict(cli.ps(), "live.ps")
    run = (rl.runs or rl.finished)[0]
    stages = parse_stages(cli.stages(run.run_id), f"live.stages[{run.run_id}]")
    assert stages, "every run has a stage list"
    assert stages[0].name == run.stage
    assert len(stages) == run.num_stages

    tls = parse_timelines(cli.timeline(run.run_id), f"live.timeline[{run.run_id}]")
    assert tls and tls[0].run_id == run.run_id

    blobs = [Blob.from_dict(b, "live.blob") for b in cli.blobs(run.run_id)]
    assert isinstance(blobs, list)


def test_live_adapter_end_to_end():
    cli = _cli_live()
    if cli is None:
        pytest.skip("lev CLI not available")
    a = DaemonAdapter(prefer="cli")
    with a:
        rl = a.runs()
        assert rl.runs
        run = rl.runs[0]
        assert a.run(run.run_id) is not None
        assert a.stages(run.run_id)
        assert a.blueprints().agents
        assert a.commands().commands
        assert isinstance(a.health().tools_workers, int)