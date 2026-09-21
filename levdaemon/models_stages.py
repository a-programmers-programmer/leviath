"""Stage and timeline records.

Keys come from real capture: fixtures/cli_stages_live.json,
fixtures/cli_timeline_live.json, fixtures/stages.json, fixtures/timeline.json.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .models_runs import Active
from .record import Record, SchemaError


@dataclass(frozen=True)
class StageVisit(Record):
    entered_at: int
    left_at: Optional[int]
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    cost_usd: float
    unpriced_calls: int
    cost_is_exact: bool
    cost_priced_usd: float
    active: Active

    @property
    def open(self) -> bool:
        return self.left_at is None


@dataclass(frozen=True)
class RegionTokens(Record):
    """Per-region prompt-token spend. The region set varies per run.

    The daemon sends this as a FLAT map of region name -> token count, so the
    type keeps the raw map and validates every value is a non-negative int.
    """

    values: Dict[str, int]

    @classmethod
    def known_keys(cls):
        return ("values",)

    @classmethod
    def from_dict(cls, d, where: str = None):
        where = where or cls.__name__
        if not isinstance(d, dict):
            raise SchemaError(f"{where}: expected dict, got {type(d).__name__}")
        values = {}
        for k, v in d.items():
            if not isinstance(k, str):
                raise SchemaError(f"{where}: region name must be str, got {type(k).__name__}")
            if isinstance(v, bool) or not isinstance(v, int):
                raise SchemaError(f"{where}.{k}: expected int, got {type(v).__name__} ({v!r})")
            if v < 0:
                raise SchemaError(f"{where}.{k}: negative token count {v}")
            values[k] = v
        return cls(values=values)

    def get(self, region: str, default: int = 0) -> int:
        return self.values.get(region, default)

    def __getitem__(self, region: str) -> int:
        return self.values[region]

    def __contains__(self, region: object) -> bool:
        return region in self.values

    def __iter__(self):
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    @property
    def total(self) -> int:
        return sum(self.values.values())


@dataclass(frozen=True)
class Stage(Record):
    """One row of ``lev stages <run_id> --json``."""

    name: str
    index: int
    status: str
    entered: bool
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    cost_usd: float
    unpriced_calls: int
    cost_is_exact: bool
    cost_priced_usd: float
    visits: List[StageVisit]
    visit_count: int
    region_tokens: RegionTokens
    first_call_prompt_tokens: Optional[int]
    runaway_warned: bool
    output_cap_raised: bool
    started_at: Optional[int]
    ended_at: Optional[int]
    active: Active

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def wall_secs(self) -> Optional[int]:
        if self.started_at is None or self.ended_at is None:
            return None
        return self.ended_at - self.started_at


def parse_stages(payload: Any, where: str = "stages") -> List[Stage]:
    """Parse the CLI list form of a run's stages."""
    if not isinstance(payload, list):
        raise SchemaError(f"{where}: expected list, got {type(payload).__name__}")
    return [Stage.from_dict(s, f"{where}[{i}]") for i, s in enumerate(payload)]


def stage_names(payload: Any) -> List[str]:
    return [s.name for s in parse_stages(payload)]


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StageCall(Record):
    """One inference call in a timeline ([{...}])."""

    stage: str
    iteration: int
    kind: str
    model: str
    started_at: int
    ended_at: int
    prompt_tokens: int
    cached_tokens: int
    completion_tokens: int

    @property
    def secs(self) -> int:
        return max(0, self.ended_at - self.started_at)


@dataclass(frozen=True)
class Totals(Record):
    wall: int
    inference: int
    tools: int
    waiting: int
    other: int


@dataclass(frozen=True)
class TimelineStageRow(Record):
    name: str
    secs: int
    calls: int
    output_tokens: int
    largest_reply: int


@dataclass(frozen=True)
class Timeline(Record):
    """One entry of ``lev timeline <run_id> --json``. Children nest the same type."""

    run_id: str
    agent_name: str
    status: str
    depth: int
    children: List["Timeline"]
    totals: Totals
    stages: List[TimelineStageRow]
    calls: List[StageCall]
    warnings: List[Any]

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def stage(self, name: str) -> Optional[TimelineStageRow]:
        for s in self.stages:
            if s.name == name:
                return s
        return None


def parse_timelines(payload: Any, where: str = "timeline") -> List[Timeline]:
    if not isinstance(payload, list):
        raise SchemaError(f"{where}: expected list, got {type(payload).__name__}")
    return [Timeline.from_dict(t, f"{where}[{i}]") for i, t in enumerate(payload)]
