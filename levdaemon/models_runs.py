"""Run-side records. Keys come from real capture: fixtures/control_list.json,
fixtures/cli_ps.json, fixtures/control_welcome.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .record import Record


@dataclass(frozen=True)
class Active(Record):
    """Banked working seconds plus the start of the current window."""

    banked_secs: int
    since: Optional[int]


@dataclass(frozen=True)
class ReadPaths(Record):
    declared: int
    granted: int


@dataclass(frozen=True)
class Run(Record):
    """One row of control ``list`` or of ``lev ps --json``.

    The control socket omits ``age_secs``/``working_secs``; the CLI adds them.
    Both shapes parse into this type.
    """

    run_id: str
    status: str
    stage: str
    stage_index: int
    num_stages: int
    iteration: int
    tool_calls: int
    started_at: int
    last_progress_at: int
    active: Active
    unattended: bool
    empty_output: bool
    splits_degraded: int
    has_final_output: bool
    # Observed absent on some live runs (e.g. a run whose read set is not yet
    # resolved). Never omitted together with the other keys.
    read_paths: Optional[ReadPaths] = None
    title: Optional[str] = None
    age_secs: Optional[int] = None
    working_secs: Optional[int] = None

    TERMINAL_STATUSES = ("Complete", "Failed", "Cancelled")

    @property
    def is_live(self) -> bool:
        return self.status not in self.TERMINAL_STATUSES

    @property
    def stage_fraction(self) -> float:
        if not self.num_stages:
            return 0.0
        return self.stage_index / float(self.num_stages)


@dataclass(frozen=True)
class InferenceSlot(Record):
    model: str
    in_use: int
    cap: int


@dataclass(frozen=True)
class AgentCounts(Record):
    active: int
    waiting: int
    paused: int
    idle: int
    terminal: int


@dataclass(frozen=True)
class Health(Record):
    agents: AgentCounts
    inference: List[InferenceSlot]
    inference_providers: List[Any]
    tools_busy: int
    tools_queued: int
    tools_parked: int
    tools_workers: int
    dead_cycles: int
    relief_granted: int
    redrive_secs: int
    providers_down: List[Any]

    def slot(self, model: str) -> Optional[InferenceSlot]:
        for s in self.inference:
            if s.model == model:
                return s
        return None


@dataclass(frozen=True)
class RunList(Record):
    """Answer to control ``list`` and to ``lev ps --json``.

    ``result`` appears on the control-socket form only, so it is optional.
    """

    runs: List[Run]
    finished: List[Run]
    health: Health
    result: Optional[str] = None

    @property
    def all_runs(self) -> List[Run]:
        return list(self.runs) + list(self.finished)

    def by_id(self, run_id: str) -> Optional[Run]:
        for r in self.all_runs:
            if r.run_id == run_id:
                return r
        return None


@dataclass(frozen=True)
class DaemonInfo(Record):
    """``daemon`` block of the control-socket welcome."""

    version: str
    build: str
    pid: int
    tool_env: List[Any] = field(default_factory=list)


@dataclass(frozen=True)
class Welcome(Record):
    """Reply to ``authenticate`` with ``hello`` set."""

    result: str
    daemon: DaemonInfo


@dataclass(frozen=True)
class StatusReply(Record):
    """Reply to control ``status``; null when the run is not live here."""

    result: str
    status: Optional[str]


@dataclass(frozen=True)
class OkReply(Record):
    result: str
    ok: bool


@dataclass(frozen=True)
class ErrorReply(Record):
    result: str
    message: str


@dataclass(frozen=True)
class InteractionsReply(Record):
    result: str
    interactions: List[Any]


@dataclass(frozen=True)
class FinalOutput(Record):
    """``lev result <run_id> --json``. The CLI exits 1 when there is none."""

    content: str
    stage: str
    submitted_at: int
    truncated: bool


@dataclass(frozen=True)
class Blueprint(Record):
    """One agent blueprint from ``lev list --json`` ``agents``."""

    name: str
    version: str
    description: Optional[str]
    read_paths: Optional[str]
    source: str
    path: str

    @property
    def declared_read_paths(self) -> Optional[int]:
        """First integer of ``read_paths: 4 declared, 4 granted``."""
        if not self.read_paths:
            return None
        head = self.read_paths.split("declared")[0]
        digits = "".join(c for c in head if c.isdigit())
        return int(digits) if digits else None


@dataclass(frozen=True)
class BundledBlueprint(Record):
    name: str
    version: str


@dataclass(frozen=True)
class BlueprintList(Record):
    agents: List[Blueprint]
    bundled: List[BundledBlueprint]

    def by_name(self, name: str) -> Optional[Blueprint]:
        for a in self.agents:
            if a.name == name:
                return a
        return None


@dataclass(frozen=True)
class Command(Record):
    name: str
    group: str
    summary: str


@dataclass(frozen=True)
class CommandCatalogue(Record):
    """``lev commands --json``."""

    groups: List[str]
    commands: List[Command]

    def names(self) -> List[str]:
        return [c.name for c in self.commands]

    def in_group(self, group: str) -> List[Command]:
        return [c for c in self.commands if c.group == group]

    def by_name(self, name: str) -> Optional[Command]:
        for c in self.commands:
            if c.name == name:
                return c
        return None


@dataclass(frozen=True)
class Blob(Record):
    """One artifact row from ``lev blobs <run_id> --json``."""

    sha256: str
    mime_type: str
    name: str
    size: int
    tokens: int
    regions: List[str]
    stored: bool

    def __post_init__(self):
        if len(self.sha256) != 64:
            raise ValueError(f"blob {self.name}: sha256 is not 64 hex chars: {self.sha256!r}")

    @property
    def is_text(self) -> bool:
        return self.mime_type.startswith("text/") or self.mime_type in ("application/json",)
