"""``lev`` CLI client. Every surface returns parsed JSON, never text.

Verified live against the captures named per method. The CLI prints
``--json`` payloads after a banner, so the reader strips ANSI colour and
leading log lines before ``json.loads``.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, List, Optional

ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOGLINE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z")


class CliUnavailable(RuntimeError):
    """The ``lev`` binary is not on PATH or did not answer."""


class CliError(RuntimeError):
    """The CLI exited non-zero for a reason other than 'no output yet'."""


def strip_banner(text: str) -> str:
    text = ANSI.sub("", text)
    lines = [ln for ln in text.splitlines() if not LOGLINE.match(ln)]
    return "\n".join(lines)


def extract_json(text: str) -> Any:
    """Return the JSON payload printed by ``lev ... --json``."""
    clean = strip_banner(text)
    start = next((i for i, ch in enumerate(clean) if ch in "[{"), None)
    if start is None:
        raise CliUnavailable(f"no JSON in CLI output: {clean[:200]!r}")
    return json.loads(clean[start:])


class CliClient:
    def __init__(self, binary: str = "lev", timeout: float = 120.0):
        self.binary = binary
        self.timeout = timeout

    def run(self, args: List[str]):
        try:
            p = subprocess.run([self.binary] + args, capture_output=True, text=True,
                               timeout=self.timeout)
        except FileNotFoundError as exc:
            raise CliUnavailable(f"{self.binary}: not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise CliUnavailable(f"{self.binary} {' '.join(args)}: timed out") from exc
        return p

    def json(self, args: List[str], allow_rc: tuple = (0,)) -> Any:
        p = self.run(args)
        if p.returncode not in allow_rc:
            raise CliError(f"lev {' '.join(args)} rc={p.returncode}: {(p.stderr or p.stdout)[:300]}")
        return extract_json(p.stdout)

    # -- surfaces (fixture each method was validated against) --------------
    def ps(self) -> Any:
        """fixtures/cli_ps.json"""
        return self.json(["ps", "--json"])

    def blueprints(self) -> Any:
        """fixtures/cli_list.json"""
        return self.json(["list", "--json"])

    def help_text(self) -> str:
        """Raw ``lev --help`` text. There is no ``lev commands`` subcommand."""
        p = self.run(["--help"])
        if p.returncode != 0:
            raise CliError(f"lev --help rc={p.returncode}: {(p.stderr or p.stdout)[:300]}")
        return strip_banner(p.stdout)

    def commands(self) -> Any:
        """fixtures/cli_commands.json. Parsed from ``lev --help`` by code."""
        from .commands import parse_help

        return parse_help(self.help_text()).as_dict()

    def stages(self, run_id: str) -> Any:
        """fixtures/cli_stages_live.json"""
        return self.json(["stages", run_id, "--json"])

    def timeline(self, run_id: str) -> Any:
        """fixtures/cli_timeline_live.json"""
        return self.json(["timeline", run_id, "--json"])

    def blobs(self, run_id: str) -> Any:
        """fixtures/cli_blobs_done.json (empty list is a valid answer)"""
        return self.json(["blobs", run_id, "--json"])

    def result(self, run_id: str) -> Optional[Any]:
        """fixtures/cli_result_done.json. rc=1 means 'no final output yet'."""
        p = self.run(["result", run_id, "--json"])
        if p.returncode != 0:
            return None
        try:
            return extract_json(p.stdout)
        except CliUnavailable:
            return None
