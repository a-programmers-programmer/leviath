"""Typed high-level adapter over the real Leviath daemon.

Surfaces: runs, blueprints, stages, artifacts (blobs), commands.
Types live in ``levdaemon.models_runs`` and ``levdaemon.models_stages``.
Each was validated against captured real daemon responses in ``fixtures/``.

    from levdaemon import DaemonAdapter
    a = DaemonAdapter()
    a.runs()                 # RunList
    a.stages("worker-...")   # List[Stage]
    a.blobs("worker-...")    # List[Blob]
    a.blueprints()           # BlueprintList
    a.commands()             # CommandCatalogue
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .cli import CliClient, CliError, CliUnavailable
from .control import DEFAULT_HOME, ControlClient, ControlUnavailable, DaemonError
from .models_runs import (
    Blob,
    BlueprintList,
    CommandCatalogue,
    FinalOutput,
    Health,
    InteractionsReply,
    OkReply,
    Run,
    RunList,
    StatusReply,
    Welcome,
)
from .models_stages import RegionTokens, Stage, Timeline, parse_stages, parse_timelines
from .record import Record, SchemaError

__all__ = [
    "DaemonAdapter", "Capabilities",
    "ControlClient", "CliClient",
    "ControlUnavailable", "CliUnavailable", "DaemonError", "CliError", "SchemaError",
    "Active", "ReadPaths", "Run", "RunList", "Health", "Welcome", "StatusReply", "OkReply",
    "InteractionsReply", "FinalOutput", "BlueprintList", "CommandCatalogue", "Blob",
    "Stage", "StageVisit", "RegionTokens", "Timeline", "parse_stages", "parse_timelines",
    "Record",
]

from .models_runs import Active, ReadPaths  # noqa: E402  (re-export)


def _models(module, names: List[str]) -> Dict[str, Any]:
    return {n: getattr(module, n) for n in names}


@dataclass(frozen=True)
class Capabilities:
    """Which transports this machine can reach right now."""

    control_socket: bool
    control_path: str
    cli: bool
    cli_binary: str
    leviath_home: str

    @property
    def any_live(self) -> bool:
        return self.control_socket or self.cli


class DaemonAdapter:
    """Typed reads over the daemon. Prefers the control socket, falls back to CLI."""

    def __init__(self, home: str = DEFAULT_HOME, binary: str = "lev", timeout: float = 30.0,
                 prefer: str = "control"):
        self.home = home
        self.control = ControlClient(home, timeout)
        self.cli = CliClient(binary, timeout)
        self.prefer = prefer
        self._control_open: Optional[ControlClient] = None

    # -- transport management ---------------------------------------------
    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            control_socket=ControlClient.available(self.home),
            control_path=self.control.sock_path,
            cli=bool(os.environ.get("PATH"))
            and any(
                os.path.exists(os.path.join(d, self.cli.binary))
                for d in os.environ.get("PATH", "").split(os.pathsep)
                if d
            ),
            cli_binary=self.cli.binary,
            leviath_home=self.home,
        )

    def _live_control(self) -> ControlClient:
        if self._control_open is None:
            self._control_open = ControlClient(self.home, self.control.timeout).connect()
        return self._control_open

    def close(self) -> None:
        if self._control_open is not None:
            self._control_open.close()
            self._control_open = None

    def __enter__(self) -> "DaemonAdapter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def welcome(self) -> Welcome:
        """Daemon identity from the control socket handshake."""
        return Welcome.from_dict(self._live_control().welcome or {}, "welcome")

    # -- runs --------------------------------------------------------------
    def runs_raw(self) -> Dict[str, Any]:
        if self.prefer == "control" and self.capabilities.control_socket:
            return self._live_control().list_raw()
        return self.cli.ps()

    def runs(self) -> RunList:
        """Live and finished runs, plus daemon health."""
        return RunList.from_dict(self.runs_raw(), "runs")

    def run(self, run_id: str) -> Optional[Run]:
        return self.runs().by_id(run_id)

    def status(self, run_id: str) -> Optional[str]:
        """Control-socket status string, or None when the run is not live."""
        reply = StatusReply.from_dict(self._live_control().status_raw(run_id),
                                     f"status[{run_id}]")
        return reply.status

    def health(self) -> Health:
        return self.runs().health

    def interactions(self) -> InteractionsReply:
        return InteractionsReply.from_dict(self._live_control().interactions_raw(), "interactions")

    # -- stages / timeline -------------------------------------------------
    def stages(self, run_id: str) -> List[Stage]:
        """Per-stage token, cost and region accounting for one run."""
        return parse_stages(self.cli.stages(run_id), f"stages[{run_id}]")

    def current_stage(self, run_id: str) -> Optional[Stage]:
        for s in self.stages(run_id):
            if s.is_active:
                return s
        return None

    def region_tokens(self, run_id: str) -> RegionTokens:
        stage = self.current_stage(run_id)
        return stage.region_tokens if stage else RegionTokens(values={})

    def timeline(self, run_id: str) -> List[Timeline]:
        """Per-call timeline, nested through children."""
        return parse_timelines(self.cli.timeline(run_id), f"timeline[{run_id}]")

    # -- artifacts ---------------------------------------------------------
    def blobs(self, run_id: str) -> List[Blob]:
        """Artifacts the daemon stored for a run (empty list is valid)."""
        payload = self.cli.blobs(run_id)
        if not isinstance(payload, list):
            raise SchemaError(f"blobs[{run_id}]: expected list, got {type(payload).__name__}")
        return [Blob.from_dict(b, f"blobs[{run_id}][{i}]") for i, b in enumerate(payload)]

    def result(self, run_id: str) -> Optional[FinalOutput]:
        """Final output of a run, or None when there is none yet."""
        payload = self.cli.result(run_id)
        if payload is None:
            return None
        return FinalOutput.from_dict(payload, f"result[{run_id}]")

    # -- blueprints --------------------------------------------------------
    def blueprints(self) -> BlueprintList:
        """Installed blueprints plus the bundled set."""
        return BlueprintList.from_dict(self.cli.blueprints(), "blueprints")

    def blueprint(self, name: str):
        return self.blueprints().by_name(name)

    # -- commands ----------------------------------------------------------
    def commands(self) -> CommandCatalogue:
        """The CLI's own command catalogue, grouped."""
        return CommandCatalogue.from_dict(self.cli.commands(), "commands")