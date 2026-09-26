"""Deterministic core for operator-approved progress leases.

API: ``initial_state(policy) -> state`` and
``decide(policy, state, observation, receipt, now) -> {action, state}``.

The immutable JSON policy has run_id, task_id, scope_id, actor, started_at,
hard_deadline, first_checkpoint, window_seconds, run_cap_usd, scope_cap_usd,
and prior_spend_usd. State uses schema 1 and binds a canonical sorted-JSON
SHA256 policy digest. Observation is adapter-normalized status/live/cost_usd.
Receipt is explicit operator attestation, bound to all three IDs and actor,
with positive sequence, approved_at, and evidence kind/reference/summary/SHA256.
No IO, worker-proof inference, dispatcher, or external dependencies belong here.
"""
import hashlib
import json
import math
import re

_TERMINAL = {"complete", "completed", "failed", "cancelled", "canceled", "stopped", "error"}
_POLICY_FIELDS = {
    "run_id", "task_id", "scope_id", "actor", "started_at", "hard_deadline",
    "first_checkpoint", "window_seconds", "run_cap_usd", "scope_cap_usd", "prior_spend_usd",
}
_STATE_FIELDS = {
    "schema", "policy_sha256", "next_checkpoint", "misses", "last_receipt_seq",
    "last_now", "max_cost_usd", "cancel_reason", "terminal_verified",
}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _policy_error(policy):
    if not isinstance(policy, dict) or set(policy) != _POLICY_FIELDS:
        return "invalid_policy"
    for field in ("run_id", "task_id", "scope_id", "actor"):
        if not isinstance(policy[field], str) or not policy[field].strip():
            return "invalid_policy"
    for field in _POLICY_FIELDS - {"run_id", "task_id", "scope_id", "actor"}:
        if not _number(policy[field]):
            return "invalid_policy"
    if (policy["window_seconds"] <= 0 or policy["run_cap_usd"] <= 0 or
            policy["scope_cap_usd"] <= 0 or policy["prior_spend_usd"] < 0 or
            policy["run_cap_usd"] > 10 or
            not policy["started_at"] < policy["first_checkpoint"] <= policy["hard_deadline"]):
        return "invalid_policy"
    return None


def _digest(policy):
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def initial_state(policy):
    error = _policy_error(policy)
    if error:
        raise ValueError(error)
    return {
        "schema": 1, "policy_sha256": _digest(policy),
        "next_checkpoint": policy["first_checkpoint"], "misses": 0,
        "last_receipt_seq": 0, "last_now": policy["started_at"],
        "max_cost_usd": 0.0, "cancel_reason": None, "terminal_verified": False,
    }


def _state_error(policy, state):
    if _policy_error(policy):
        return "invalid_policy"
    if not isinstance(state, dict) or set(state) != _STATE_FIELDS:
        return "corrupt_state"
    try:
        if state["schema"] != 1 or state["policy_sha256"] != _digest(policy):
            return "policy_changed"
    except (TypeError, ValueError):
        return "corrupt_state"
    if (not _number(state["next_checkpoint"]) or not _number(state["last_now"]) or
            not _number(state["max_cost_usd"]) or state["max_cost_usd"] < 0 or
            not isinstance(state["misses"], int) or isinstance(state["misses"], bool) or state["misses"] < 0 or
            not isinstance(state["last_receipt_seq"], int) or isinstance(state["last_receipt_seq"], bool) or state["last_receipt_seq"] < 0 or
            (state["cancel_reason"] is not None and (not isinstance(state["cancel_reason"], str) or not state["cancel_reason"])) or
            not isinstance(state["terminal_verified"], bool)):
        return "corrupt_state"
    return None


def _receipt_valid(policy, state, receipt, now):
    if not isinstance(receipt, dict):
        return False
    try:
        if any(receipt.get(key) != policy[key] for key in ("run_id", "task_id", "scope_id")):
            return False
        if receipt.get("approved_by") != policy["actor"]:
            return False
        seq = receipt.get("seq")
        approved_at = receipt.get("approved_at")
        if (not isinstance(seq, int) or isinstance(seq, bool) or seq <= state["last_receipt_seq"] or
                not _number(approved_at) or approved_at < policy["started_at"] or approved_at > now or
                now - approved_at > policy["window_seconds"]):
            return False
        evidence = receipt.get("evidence")
        if not isinstance(evidence, dict) or evidence.get("kind") not in {"implementation", "acceptance", "artifact", "uncertainty"}:
            return False
        if any(not isinstance(evidence.get(key), str) or not evidence[key].strip()
               for key in ("reference", "summary")):
            return False
        return isinstance(evidence.get("sha256"), str) and re.fullmatch(r"[0-9a-fA-F]{64}", evidence["sha256"]) is not None
    except (KeyError, TypeError):
        return False


def decide(policy, state, observation, receipt, now):
    """Return deterministic action and updated state; malformed input fails closed."""
    error = _state_error(policy, state)
    if error:
        fallback = dict(state) if isinstance(state, dict) else {}
        fallback["cancel_reason"] = error
        return {"action": "cancel", "state": fallback}
    updated = dict(state)
    if not _number(now):
        updated["cancel_reason"] = updated["cancel_reason"] or "invalid_now"
        return {"action": "cancel", "state": updated}
    now = max(now, state["last_now"])
    updated["last_now"] = now
    if not isinstance(observation, dict):
        updated["cancel_reason"] = updated["cancel_reason"] or "invalid_observation"
        return {"action": "cancel", "state": updated}
    status, live, cost = (observation.get("status"), observation.get("live"), observation.get("cost_usd"))
    if (not isinstance(status, str) or not status.strip() or not isinstance(live, bool) or
            not _number(cost) or cost < 0):
        updated["cancel_reason"] = updated["cancel_reason"] or "invalid_observation"
        return {"action": "cancel", "state": updated}
    updated["max_cost_usd"] = max(state["max_cost_usd"], float(cost))
    if status.lower() in _TERMINAL and live is False:
        updated["terminal_verified"] = True
        updated["cancel_reason"] = None
        return {"action": "terminal", "state": updated}
    if state["terminal_verified"]:
        updated["terminal_verified"] = False
        updated["cancel_reason"] = updated["cancel_reason"] or "terminal_reopened"
        return {"action": "cancel", "state": updated}
    if updated["cancel_reason"]:
        return {"action": "cancel", "state": updated}
    if now >= policy["hard_deadline"]:
        updated["cancel_reason"] = "hard_deadline"
    elif updated["max_cost_usd"] >= policy["run_cap_usd"]:
        updated["cancel_reason"] = "run_cost_cap"
    elif policy["prior_spend_usd"] + updated["max_cost_usd"] >= policy["scope_cap_usd"]:
        updated["cancel_reason"] = "scope_cost_cap"
    if updated["cancel_reason"]:
        return {"action": "cancel", "state": updated}

    checkpoint = state["next_checkpoint"]
    misses = state["misses"]
    accepted = _receipt_valid(policy, state, receipt, now)
    if accepted:
        approved_at = receipt["approved_at"]
        while checkpoint < approved_at:
            checkpoint += policy["window_seconds"]
            misses += 1
            if misses >= 2:
                updated["misses"] = misses
                updated["next_checkpoint"] = checkpoint
                updated["cancel_reason"] = "missed_checkpoints"
                return {"action": "cancel", "state": updated}
        misses = 0
        updated["last_receipt_seq"] = receipt["seq"]
        checkpoint = min(approved_at + policy["window_seconds"], policy["hard_deadline"])

    while checkpoint <= now:
        misses += 1
        checkpoint += policy["window_seconds"]
        if misses >= 2:
            updated["misses"] = misses
            updated["next_checkpoint"] = checkpoint
            updated["cancel_reason"] = "missed_checkpoints"
            return {"action": "cancel", "state": updated}
    updated["misses"] = misses
    updated["next_checkpoint"] = checkpoint
    return {"action": "wait", "state": updated}
