import importlib.util
import json
import math
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "fleet_progress_guard.py"
spec = importlib.util.spec_from_file_location("fleet_progress_guard", MODULE_PATH)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def policy(**changes):
    value = {
        "run_id": "run-a", "task_id": "task-a", "scope_id": "scope-a",
        "actor": "operator", "started_at": 100.0, "hard_deadline": 200.0,
        "first_checkpoint": 110.0, "window_seconds": 10.0,
        "run_cap_usd": 5.0, "scope_cap_usd": 8.0, "prior_spend_usd": 1.0,
    }
    value.update(changes)
    return value


def observation(status="running", live=True, cost=0.0):
    return {"status": status, "live": live, "cost_usd": cost}


def receipt(p=None, seq=1, approved_at=110.0, **changes):
    p = p or policy()
    value = {
        "run_id": p["run_id"], "task_id": p["task_id"], "scope_id": p["scope_id"],
        "approved_by": p["actor"], "seq": seq, "approved_at": approved_at,
        "evidence": {"kind": "implementation", "reference": "commit:abc",
                     "summary": "Reviewed progress", "sha256": "a" * 64},
    }
    value.update(changes)
    return value


def decide(p, s, now, rec=None, obs=None):
    return guard.decide(p, s, obs or observation(), rec, now)


def test_first_and_second_missed_checkpoint_cancel():
    p = policy(); s = guard.initial_state(p)
    result = decide(p, s, 110)
    assert result["action"] == "wait" and result["state"]["misses"] == 1
    result = decide(p, result["state"], 120)
    assert result["action"] == "cancel" and result["state"]["misses"] == 2
    assert result["state"]["cancel_reason"]


def test_exact_boundary_receipt_wins_and_resets_miss():
    p = policy(); result = decide(p, guard.initial_state(p), 110, receipt(p, approved_at=110))
    assert result["action"] == "wait"
    assert result["state"]["misses"] == 0
    assert result["state"]["last_receipt_seq"] == 1
    assert result["state"]["next_checkpoint"] == 120


def test_serialized_restart_counts_all_elapsed_windows():
    p = policy(); s = json.loads(json.dumps(guard.initial_state(p)))
    result = decide(p, s, 145)
    assert result["action"] == "cancel" and result["state"]["misses"] >= 2
    assert result["state"]["next_checkpoint"] == 130.0


def test_receipt_after_one_miss_restores_progress():
    p = policy(); s = decide(p, guard.initial_state(p), 110)["state"]
    result = decide(p, s, 115, receipt(p, approved_at=115))
    assert result["action"] == "wait"
    assert result["state"]["misses"] == 0
    assert result["state"]["last_receipt_seq"] == 1
    assert result["state"]["next_checkpoint"] == 125


def test_late_receipt_cannot_rescue_two_misses():
    p = policy(); result = decide(p, guard.initial_state(p), 130, receipt(p, approved_at=130))
    assert result["action"] == "cancel"
    assert result["state"]["last_receipt_seq"] == 0


def test_duplicate_wrong_id_stale_future_and_bad_evidence_do_not_renew():
    p = policy(); s = decide(p, guard.initial_state(p), 110)["state"]
    valid = receipt(p, approved_at=111)
    s = decide(p, s, 111, valid)["state"]
    invalids = [valid, receipt(p, seq=2, approved_at=112, task_id="other"),
                receipt(p, seq=2, approved_at=90), receipt(p, seq=2, approved_at=120),
                receipt(p, seq=2, approved_at=112, evidence={"kind": "heartbeat", "reference": "x", "summary": "x", "sha256": "a" * 64}),
                receipt(p, seq=2, approved_at=112, evidence={"kind": "artifact", "reference": "x", "summary": "x", "sha256": "bad"})]
    for item in invalids:
        result = decide(p, s, 112, item)
        assert result["state"]["last_receipt_seq"] == 1
        assert result["state"]["next_checkpoint"] == 121


def test_hard_deadline_cancels_even_with_fresh_receipt():
    p = policy(); result = decide(p, guard.initial_state(p), 200, receipt(p, approved_at=199))
    assert result["action"] == "cancel"
    assert result["state"]["cancel_reason"] == "hard_deadline"
    assert result["state"]["last_receipt_seq"] == 0


def test_run_and_prior_scope_caps_cancel():
    p = policy(run_cap_usd=3, prior_spend_usd=1, scope_cap_usd=9)
    assert decide(p, guard.initial_state(p), 101, obs=observation(cost=3))["action"] == "cancel"
    p = policy(run_cap_usd=5, prior_spend_usd=7.5, scope_cap_usd=8)
    assert decide(p, guard.initial_state(p), 101, obs=observation(cost=.5))["action"] == "cancel"


def test_cost_high_water_does_not_allow_reset_to_hide_burn():
    p = policy(); first = decide(p, guard.initial_state(p), 101, obs=observation(cost=2))
    second = decide(p, first["state"], 102, obs=observation(cost=.25))
    assert second["action"] == "wait" and second["state"]["max_cost_usd"] == 2
    assert decide(p, second["state"], 103, obs=observation(cost=5))["action"] == "cancel"


def test_invalid_cost_and_observation_fail_closed():
    p = policy()
    for cost in (float("nan"), float("inf"), -1, None):
        result = decide(p, guard.initial_state(p), 101, obs=observation(cost=cost))
        assert result["action"] == "cancel" and result["state"]["cancel_reason"]
    for obs in ({"status": "running", "live": True}, {"status": "running", "live": 1, "cost_usd": 0},
                {"status": "", "live": False, "cost_usd": 0}):
        assert decide(p, guard.initial_state(p), 101, obs=obs)["action"] == "cancel"


def test_changed_policy_and_corrupt_state_fail_closed():
    p = policy(); s = guard.initial_state(p)
    assert decide(policy(run_id="other"), s, 101)["action"] == "cancel"
    corrupt = dict(s); corrupt["misses"] = "zero"
    assert decide(p, corrupt, 101)["action"] == "cancel"


def test_cancel_is_latched_until_verified_native_terminal():
    p = policy(); s = decide(p, guard.initial_state(p), 120)["state"]
    assert decide(p, s, 121, receipt(p, seq=1, approved_at=121))["action"] == "cancel"
    assert decide(p, s, 121, obs=observation("completed", False))["action"] == "terminal"
    assert decide(p, s, 121, obs=observation("running", False))["action"] == "cancel"
    assert decide(p, s, 121, obs=observation("complete", True))["action"] == "cancel"
    assert decide(p, s, 121, obs=observation("HELD", False))["action"] != "terminal"


def test_two_policies_cannot_renew_each_other_and_clock_rollback_clamps():
    p = policy(); other = policy(run_id="run-b", task_id="task-b")
    a = guard.initial_state(p); b = guard.initial_state(other)
    result_a = decide(p, a, 105)
    result_b = decide(other, b, 105, receipt(p, approved_at=105))
    assert result_b["state"]["last_receipt_seq"] == 0
    rollback = decide(p, result_a["state"], 90)
    assert rollback["state"]["last_now"] == 105
    assert rollback["action"] == "wait"


def test_terminal_requires_known_native_status_and_live_false():
    p = policy(); s = guard.initial_state(p)
    assert decide(p, s, 105, obs=observation("failed", False))["action"] == "terminal"
    assert decide(p, s, 105, obs=observation("failed", True))["action"] == "wait"
    assert decide(p, s, 105, obs=observation("HELD", False))["action"] == "wait"


def test_policy_rules_and_initial_checkpoint_are_deterministic():
    p = policy(); assert guard.initial_state(p)["next_checkpoint"] == 110
    for changes in ({"run_cap_usd": 11}, {"window_seconds": 0}, {"first_checkpoint": 100},
                    {"first_checkpoint": 201}, {"prior_spend_usd": float("nan")}):
        result = decide(policy(**changes), guard.initial_state(p), 101)
        assert result["action"] == "cancel"
    assert math.isfinite(guard.initial_state(p)["last_now"])
