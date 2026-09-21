"""GQL-07 — Run evidence: waiver records survive in run evidence.

`RunEvidence` is the single artifact both run modes emit:

  * workflow mode  — a whole branch/join DAG;
  * step mode      — one named step put through the same checks.

A blocked run records `blocked=True` plus the ERROR-level reasons and runs no
step. A WARN waiver records the approving identity and the reason, and those
two values survive `as_dict()` / `to_json()` unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class WaiverRecord:
    step: str
    identity: str
    reason: str
    severity: str = "WARN"

    @property
    def approved(self) -> bool:
        return bool(self.identity.strip()) and bool(self.reason.strip())


@dataclass
class RunEvidence:
    ok: bool
    blocked: bool = False
    mode: str = "workflow"  # "workflow" | "step"
    step: Optional[str] = None  # set in step mode
    results: Dict[str, object] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    waivers: List[WaiverRecord] = field(default_factory=list)
    dag_check: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "blocked": self.blocked,
            "mode": self.mode,
            "step": self.step,
            "errors": self.errors,
            "waivers": [
                {"step": w.step, "identity": w.identity, "reason": w.reason, "severity": w.severity}
                for w in self.waivers
            ],
            "dag_check": self.dag_check,
            "results": {
                k: {
                    "step": v.step if hasattr(v, "step") else k,
                    "outputs": v.outputs if hasattr(v, "outputs") else {},
                    "exit_code": v.exit_code if hasattr(v, "exit_code") else 0,
                    "ok": v.ok if hasattr(v, "ok") else True,
                }
                for k, v in self.results.items()
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)
