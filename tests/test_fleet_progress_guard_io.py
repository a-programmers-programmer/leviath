import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "fleet_progress_guard_io.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("fleet_progress_guard_io", SCRIPT)
io = importlib.util.module_from_spec(spec)
spec.loader.exec_module(io)


def policy():
    return {"run_id": "target", "task_id": "task", "scope_id": "scope", "actor": "operator",
            "started_at": 100., "hard_deadline": 200., "first_checkpoint": 190.,
            "window_seconds": 20., "run_cap_usd": 5., "scope_cap_usd": 8., "prior_spend_usd": 1.}


def setup(tmp_path):
    p = policy()
    runs = tmp_path / "runs"
    meta_dir = runs / p["run_id"]
    meta_dir.mkdir(parents=True)
    (meta_dir / "meta.json").write_text(json.dumps({"status": "running", "cost_usd": 1.25,
        "active": {"banked_secs": 4, "since": 100}, "current_stage": "work", "children": [], "unpriced_calls": 0}))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"core": io.initial_state(p), "cancel_attempts": 0,
        "last_cancel_at": None, "unresolved_cancel": False,
        "observation": {"known": False, "status": "unknown", "live": None, "cost_usd": None, "error": None}}))
    receipt = tmp_path / "receipt.json"
    return p, runs, state, receipt


def ps(runs=None, finished=None, health=None):
    return json.dumps({"runs": runs or [], "finished": finished or [],
                       "health": health or {"redrive_secs": 30, "dead_cycles": 0, "tools_busy": 0}})


def result(stdout, rc=0):
    return subprocess.CompletedProcess([], rc, stdout, "")


def native_runner(stdout, calls=None):
    calls = [] if calls is None else calls
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1:] == ["ps", "--json"]:
            return result(stdout)
        return result("")
    return run, calls


def test_observe_uses_exact_native_argv_and_filters_target(tmp_path):
    p, runs, _, _ = setup(tmp_path)
    runner, calls = native_runner(ps([{"run_id": "other", "status": "running", "active": {"banked_secs": 2, "since": 1}},
                                      {"run_id": "target", "status": "Running", "active": {"banked_secs": 2, "since": 1}}]))
    obs, summary = io.observe(p, "lev-bin", runs, runner)
    assert obs == {"status": "running", "live": True, "cost_usd": 1.25}
    assert summary["known"] and calls[0][0] == ["lev-bin", "ps", "--json"]
    assert calls[0][1]["timeout"] == 10


def test_native_live_status_must_match_meta_running(tmp_path):
    p, runs, _, _ = setup(tmp_path)
    runner, _ = native_runner(ps([{"run_id": "target", "status": "Complete", "active": {}}]))
    assert io.observe(p, "lev", runs, runner)[0] is None


def test_unknown_daemon_and_mismatched_meta_fail_closed(tmp_path):
    p, runs, _, _ = setup(tmp_path)
    def down(*args, **kwargs): return result("", 2)
    assert io.observe(p, "lev", runs, down)[0] is None
    runner, _ = native_runner(ps([], []))
    assert io.observe(p, "lev", runs, runner)[0] is None
    (runs / "target" / "meta.json").write_text(json.dumps({"status": "running", "cost_usd": 1,
        "children": [], "unpriced_calls": 0}))
    assert io.observe(p, "lev", runs, runner)[0] is None


@pytest.mark.parametrize("meta_change", [{"cost_usd": None}, {"unpriced_calls": 1}, {"children": ["child"]}])
def test_missing_or_unsafe_cost_and_children_fail_closed(tmp_path, meta_change):
    p, runs, _, _ = setup(tmp_path)
    meta = json.loads((runs / "target" / "meta.json").read_text())
    meta.update(meta_change)
    (runs / "target" / "meta.json").write_text(json.dumps(meta))
    runner, _ = native_runner(ps([{"run_id": "target", "status": "running", "active": {}}]))
    assert io.observe(p, "lev", runs, runner)[0] is None


def test_tick_cancels_unknown_and_never_trusts_receipt_to_clear_latch(tmp_path):
    p, runs, state, receipt = setup(tmp_path)
    receipt.write_text(json.dumps({"approved_by": "operator", "seq": 1, "approved_at": 120,
        "run_id": "target", "task_id": "task", "scope_id": "scope",
        "evidence": {"kind": "artifact", "reference": "x", "summary": "x", "sha256": "a"*64}}))
    calls = []
    def unknown(argv, **kwargs):
        calls.append(argv)
        return result("bad json")
    assert io.tick(p, state, receipt, "lev", runs, now=120, run=unknown) == 1
    saved = json.loads(state.read_text())
    assert saved["core"]["cancel_reason"] == "invalid_observation"
    assert calls == [["lev", "ps", "--json"], ["lev", "cancel", "target"]]


def test_cancel_attempt_persists_before_native_command_and_retries_after_restart(tmp_path):
    p, runs, state, receipt = setup(tmp_path)
    saved = json.loads(state.read_text())
    saved["core"]["cancel_reason"] = "hard_deadline"
    state.write_text(json.dumps(saved))
    live = ps([{"run_id": "target", "status": "running", "active": {}}])
    observed_inside = []
    def failing(argv, **kwargs):
        if argv[1] == "cancel":
            saved = json.loads(state.read_text())
            observed_inside.append((saved["cancel_attempts"], saved["last_cancel_at"]))
            return result("", 1)
        return result(live)
    assert io.tick(p, state, receipt, "lev", runs, now=120, run=failing) == 1
    assert observed_inside == [(1, 120)]
    assert json.loads(state.read_text())["observation"]["cancel_error"] == "exit_1"
    assert io.tick(p, state, receipt, "lev", runs, now=130, run=failing) == 1
    assert json.loads(state.read_text())["cancel_attempts"] == 1
    assert io.tick(p, state, receipt, "lev", runs, now=150, run=failing) == 1
    assert json.loads(state.read_text())["cancel_attempts"] == 2


def test_terminal_native_proof_causes_no_cancel(tmp_path):
    p, runs, state, receipt = setup(tmp_path)
    (runs / "target" / "meta.json").write_text(json.dumps({"status": "Complete", "cost_usd": 1,
        "active": {}, "current_stage": "done", "children": [], "unpriced_calls": 0}))
    runner, calls = native_runner(ps([], [{"run_id": "target", "status": "Complete"}]))
    assert io.tick(p, state, receipt, "lev", runs, now=120, run=runner) == 0
    assert [call[0] for call in calls] == [["lev", "ps", "--json"]]


def test_missing_and_corrupt_state_fail_closed_and_preserve_corrupt_bytes(tmp_path):
    p, runs, state, receipt = setup(tmp_path)
    state.unlink()
    runner, calls = native_runner(ps([{"run_id": "target", "status": "running", "active": {}}]))
    assert io.tick(p, state, receipt, "lev", runs, now=120, run=runner) == 1
    assert json.loads(state.read_text())["core"]["cancel_reason"] == "missing_state"
    raw = b'{broken state exactly\n'
    state.write_bytes(raw)
    calls.clear()
    assert io.tick(p, state, receipt, "lev", runs, now=121, run=runner) == 1
    assert raw in [f.read_bytes() for f in tmp_path.glob("state.json.corrupt.*")]
    assert json.loads(state.read_text())["core"]["cancel_reason"] == "corrupt_state"


def test_observe_accepts_documented_live_statuses(tmp_path):
    for status in ("waiting", "paused", "idle"):
        p, runs, _, _ = setup(tmp_path / status)
        (runs / "target" / "meta.json").write_text(json.dumps({"status": status, "cost_usd": 1.25,
            "children": [], "unpriced_calls": 0}))
        runner, _ = native_runner(ps([{"run_id": "target", "status": status}]))
        observation, summary = io.observe(p, "lev", runs, runner)
        assert observation == {"status": status, "live": True, "cost_usd": 1.25}
        assert summary["known"]


@pytest.mark.parametrize("cost", [float("nan"), float("inf"), -float("inf")])
def test_tick_nonfinite_cost_persists_cancel_before_native_cancel(tmp_path, cost):
    p, runs, state, receipt = setup(tmp_path)
    (runs / "target" / "meta.json").write_text(json.dumps({"status": "running", "cost_usd": cost,
        "children": [], "unpriced_calls": 0}))
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "cancel":
            saved = json.loads(state.read_text())
            assert saved["core"]["cancel_reason"] == "invalid_observation"
            assert saved["observation"]["cost_usd"] is None
        return result(ps([{"run_id": "target", "status": "running"}]))
    assert io.tick(p, state, receipt, "lev", runs, now=120, run=runner) == 1
    assert calls == [["lev", "ps", "--json"], ["lev", "cancel", "target"]]


@pytest.mark.parametrize("invalid", ["not-a-time", float("nan"), float("inf"), -float("inf")])
def test_invalid_last_cancel_at_preserves_state_and_cancels(tmp_path, invalid):
    p, runs, state, receipt = setup(tmp_path)
    wrapper = json.loads(state.read_text())
    wrapper["core"]["cancel_reason"] = "hard_deadline"
    wrapper["last_cancel_at"] = invalid
    raw = json.dumps(wrapper, allow_nan=True).encode()
    state.write_bytes(raw)
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "cancel":
            assert raw in [f.read_bytes() for f in tmp_path.glob("state.json.corrupt.*")]
            assert json.loads(state.read_text())["core"]["cancel_reason"] == "corrupt_state"
        return result(ps([{"run_id": "target", "status": "running"}]))
    assert io.tick(p, state, receipt, "lev", runs, now=120, run=runner) == 1
    assert calls == [["lev", "ps", "--json"], ["lev", "cancel", "target"]]


def test_main_normal_mode_polls_until_fresh_terminal_proof(tmp_path, monkeypatch):
    p, runs, state, receipt = setup(tmp_path)
    p["first_checkpoint"] = 110.0
    p["window_seconds"] = 10.0
    wrapper = json.loads(state.read_text())
    wrapper["core"] = io.initial_state(p)
    state.write_text(json.dumps(wrapper))
    policyfile = tmp_path / "policy.json"
    policyfile.write_text(json.dumps(p))
    (runs / "target" / "meta.json").write_text(json.dumps({"status": "running", "cost_usd": 1.25,
        "children": [], "unpriced_calls": 0}))
    polls = []
    cancels = []
    sleeps = []
    clock = [109.0]
    def runner(argv, **kwargs):
        if argv[1] == "ps":
            polls.append(argv)
            if len(polls) >= 5 and cancels:
                (runs / "target" / "meta.json").write_text(json.dumps({"status": "Complete", "cost_usd": 1.25,
                    "children": [], "unpriced_calls": 0}))
                return result(ps([], [{"run_id": "target", "status": "Complete"}]))
            return result(ps([{"run_id": "target", "status": "running"}]))
        cancels.append(argv)
        saved = json.loads(state.read_text())
        assert saved["core"]["cancel_reason"] == "missed_checkpoints"
        assert saved["cancel_attempts"] == 1
        return result("")
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += 10
        if len(sleeps) > 8:
            pytest.fail("normal mode exceeded eight polls")
    assert io.main(["--policy", str(policyfile), "--state", str(state), "--receipt", str(receipt),
                    "--native-lev", "lev", "--runs-dir", str(runs), "--poll-seconds", "10"],
                   run=runner, clock=lambda: clock[0], sleep=sleep) == 0
    assert len(polls) >= 5 and len(sleeps) >= 4
    assert cancels == [["lev", "cancel", "target"]]
    assert json.loads(state.read_text())["core"]["terminal_verified"] is True


def test_main_once_does_not_sleep_and_init_refuses_overwrite(tmp_path, monkeypatch):
    p, runs, state, receipt = setup(tmp_path)
    policyfile = tmp_path / "policy.json"
    policyfile.write_text(json.dumps(p))
    monkeypatch.setattr(io, "tick", lambda *args, **kwargs: 1)
    monkeypatch.setattr(io.time, "sleep", lambda _: pytest.fail("once slept"))
    assert io.main(["--policy", str(policyfile), "--state", str(state), "--receipt", str(receipt), "--once"]) == 1
    # This existing state makes explicit enrollment refuse overwrite.
    # The existing enrollment state makes init return a visible refusal.
    assert io.main(["--policy", str(policyfile), "--state", str(state), "--receipt", str(receipt), "--init"]) == 2


def test_lock_contention_and_persistence_errors_surface(tmp_path, monkeypatch):
    p, runs, state, receipt = setup(tmp_path)
    policyfile = tmp_path / "policy.json"
    policyfile.write_text(json.dumps(p))
    lockfd = os.open(str(state) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    import fcntl
    fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert io.main(["--policy", str(policyfile), "--state", str(state), "--receipt", str(receipt)]) == 2
    os.close(lockfd)
    def broken(*args, **kwargs): raise OSError("disk full")
    monkeypatch.setattr(io, "_atomic_json", broken)
    runner, _ = native_runner(ps([{"run_id": "target", "status": "running", "active": {}}]))
    with pytest.raises(OSError):
        io.tick(p, state, receipt, "lev", runs, now=120, run=runner)


def test_main_supervises_absent_waiting_input_until_two_misses_then_terminal(tmp_path):
    p, runs, state, receipt = setup(tmp_path)
    p["first_checkpoint"] = 110.0
    p["window_seconds"] = 10.0
    wrapper = json.loads(state.read_text())
    wrapper["core"] = io.initial_state(p)
    state.write_text(json.dumps(wrapper))
    (runs / "target" / "meta.json").write_text(json.dumps({
        "status": "waiting_input", "cost_usd": 0, "children": [], "unpriced_calls": 0,
    }))
    policyfile = tmp_path / "policy.json"
    policyfile.write_text(json.dumps(p))
    polls = []
    cancels = []
    sleeps = []
    clock = [109.0]

    def runner(argv, **kwargs):
        if argv[1] == "ps":
            polls.append(argv)
            if len(polls) == 1:
                observation, summary = io.observe(p, "lev", runs,
                    lambda *args, **kw: result(ps([], [], {"healthy": True})))
                assert observation == {"status": "waiting_input", "live": False, "cost_usd": 0.0}
                assert summary["known"]
            if len(polls) >= 4 and cancels:
                (runs / "target" / "meta.json").write_text(json.dumps({
                    "status": "complete", "cost_usd": 0, "children": [], "unpriced_calls": 0,
                }))
                return result(ps([], [], {"healthy": True}))
            return result(ps([], [], {"healthy": True}))
        cancels.append(argv)
        saved = json.loads(state.read_text())
        assert saved["core"]["cancel_reason"] == "missed_checkpoints"
        assert saved["core"]["misses"] == 2
        assert saved["cancel_attempts"] == 1
        return result("")

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += 10

    assert io.main(["--policy", str(policyfile), "--state", str(state), "--receipt", str(receipt),
                    "--native-lev", "lev", "--runs-dir", str(runs), "--poll-seconds", "10"],
                   run=runner, clock=lambda: clock[0], sleep=sleep) == 0
    assert len(polls) == 4
    assert cancels == [["lev", "cancel", "target"]]
    assert len(sleeps) == 3
    saved = json.loads(state.read_text())
    assert saved["core"]["terminal_verified"] is True
    assert saved["cancel_attempts"] == 1
