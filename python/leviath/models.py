"""Data models for Leviath run results and statuses."""

from __future__ import annotations

import dataclasses
import enum
from typing import Optional


class TerminalStatus(enum.Enum):
    """The terminal statuses a run can reach."""

    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"

    @classmethod
    def _coerce_label(cls, label) -> str:
        """Tolerate a Rust-style `{"Error": {...}}` status dict, collapse to 'error' + message."""
        if isinstance(label, dict):
            if "Error" in label:
                inner = label["Error"]
                return "error"
            return "unknown"
        return str(label or "unknown")

    @classmethod
    def from_wire(cls, label: str) -> Optional["TerminalStatus"]:
        """Parse a wire status label into a TerminalStatus, or None."""
        label = cls._coerce_label(label)
        label_lower = label.lower()
        for member in cls:
            if member.value == label_lower:
                return member
        return None


class RunStatus(enum.Enum):
    """All known run statuses."""

    # Terminal
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"
    # Non-terminal
    ACTIVE = "active"
    IDLE = "idle"
    PAUSED = "paused"
    WAITING = "waiting"
    STARTING = "starting"
    # Unknown (pass-through)
    UNKNOWN = "unknown"

    @classmethod
    def _coerce_label(cls, label) -> str:
        """Tolerate a Rust-style `{"Error": {...}}` status dict, collapse to 'error' + message."""
        if isinstance(label, dict):
            if "Error" in label:
                return "error"
            return "unknown"
        return str(label or "unknown")

    @classmethod
    def from_wire(cls, label: str) -> "RunStatus":
        label = cls._coerce_label(label)
        label_lower = label.lower()
        for member in cls:
            if member.value == label_lower:
                return member
        return cls.UNKNOWN

    @property
    def is_terminal(self) -> bool:
        """Whether this status means the run will never move again."""
        return self in (
            RunStatus.COMPLETE,
            RunStatus.ERROR,
            RunStatus.CANCELLED,
        )


# The set of terminal status labels as lowercase strings.
TERMINAL_LABELS: frozenset = frozenset(
    {s.value for s in (RunStatus.COMPLETE, RunStatus.ERROR, RunStatus.CANCELLED)}
)


@dataclasses.dataclass
class RunResult:
    """The result of a completed run."""

    run_id: str
    status: RunStatus
    stage: Optional[str] = None
    iteration: int = 0
    final_output: Optional[str] = None
    error: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict = dataclasses.field(default_factory=dict)

    @classmethod
    def from_ps_entry(cls, entry: dict) -> "RunResult":
        """Build a RunResult from a `lev ps --json` entry."""
        status = RunStatus.from_wire(entry.get("status", "unknown"))
        return cls(
            run_id=entry.get("run_id", ""),
            status=status,
            stage=entry.get("stage"),
            iteration=entry.get("iteration", 0),
            error=entry.get("error"),
            prompt_tokens=0,
            completion_tokens=0,
            raw=entry,
        )


@dataclasses.dataclass
class RunListEntry:
    """One entry from list_runs()."""

    run_id: str
    status: RunStatus
    stage: Optional[str] = None
    title: Optional[str] = None
    has_final_output: bool = False
    live: bool = False
    started_at: Optional[int] = None