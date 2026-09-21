"""Shared lifecycle for executor jobs.

The state machine lives here once. The GraphQL, CLI and MCP surfaces cannot
invent their own states or their own transitions; they call this module.
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Dict, List, Optional, Tuple


class RunState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED)
ACTIVE_STATES = (RunState.QUEUED, RunState.RUNNING)

# The only legal moves. Anything else raises LifecycleError.
ALLOWED_TRANSITIONS: Dict[RunState, Tuple[RunState, ...]] = {
    RunState.QUEUED: (RunState.RUNNING, RunState.FAILED, RunState.CANCELLED),
    RunState.RUNNING: (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED),
    RunState.SUCCEEDED: (),
    RunState.FAILED: (),
    RunState.CANCELLED: (),
}


class LifecycleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


def coerce_state(value) -> RunState:
    if isinstance(value, RunState):
        return value
    try:
        return RunState(str(value).lower())
    except ValueError:
        raise LifecycleError("unknown_state", f"unknown state {value!r}")


def is_terminal(state) -> bool:
    return coerce_state(state) in TERMINAL_STATES


def can_transition(current, target) -> bool:
    cur = coerce_state(current)
    tgt = coerce_state(target)
    if cur == tgt:
        return True  # idempotent re-apply is allowed
    return tgt in ALLOWED_TRANSITIONS[cur]


def assert_transition(current, target) -> RunState:
    """Raise LifecycleError when a move is illegal, else return the new state."""
    cur = coerce_state(current)
    tgt = coerce_state(target)
    if can_transition(cur, tgt):
        return tgt
    if cur in TERMINAL_STATES:
        raise LifecycleError(
            "already_terminal",
            f"job is already {cur.value}; cannot move to {tgt.value}",
        )
    raise LifecycleError("illegal_transition", f"cannot move {cur.value} -> {tgt.value}")


def cancel_state(current) -> RunState:
    """State after a cancel request: terminal cancelled, or unchanged if terminal."""
    cur = coerce_state(current)
    if cur in TERMINAL_STATES:
        return cur
    return RunState.CANCELLED


def event(kind: str, from_state: Optional[RunState] = None,
          to_state: Optional[RunState] = None, **extra) -> dict:
    """One lifecycle event, in a shape every surface can render."""
    payload = {
        "kind": kind,
        "from": from_state.value if isinstance(from_state, RunState) else from_state,
        "to": to_state.value if isinstance(to_state, RunState) else to_state,
        "ts": time.time(),
    }
    payload.update(extra)
    return payload
