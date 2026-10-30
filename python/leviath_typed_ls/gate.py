"""GQL-04 gates: the contract pin and the obligation block.

Two small, loud gates, both deterministic and both offline:

* :func:`require_unblocked` turns an extraction's ``UnsupportedConstruct``
  findings into a refusal.  A dynamic construct the static reader cannot resolve
  is an **obligation**, not a warning: silently dropping it would let the server
  claim a fact it never read.  The obligation blocks.
* :func:`verify_contract` re-verifies the accepted GQL-01 contract fingerprint
  (printed-SDL SHA-256, the exact value the decision log publishes) and raises
  :class:`ContractDrift` on any difference.  A drifted contract is never
  reconciled as if it were the accepted model.

The fingerprint itself is delegated to the accepted
``leviath_reconcile.adapters.schema_adapter``, so this gate and GQL-02's gate
compute the same bytes by construction rather than by coincidence.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, Optional, Sequence

from leviath_reconcile.adapters import schema_adapter

#: The accepted GQL-01 contract fingerprint (printed-SDL SHA-256).
PINNED_FINGERPRINT = "ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559"

#: The contract SDL inside a contract directory.
CONTRACT_SDL_NAME = "schema-workflow.graphql"

#: The append-only decision log that hosts the pin.
DECISIONS_NAME = "workflow-schema-decisions.md"

#: The decision-log line that records the accepted fingerprint.
PIN_HOST_PATTERN = re.compile(
    r"Accepted contract fingerprint \(printed-SDL SHA-256\):\s*`([0-9a-f]{64})`"
)


class ContractDrift(RuntimeError):
    """The observed contract is not the pinned accepted contract."""


class ObligationBlocked(RuntimeError):
    """An unsupported dynamic construct leaves an obligation outstanding."""

    def __init__(self, findings: Sequence[object], what: str = "typed-code extraction") -> None:
        self.findings = tuple(findings)
        self.what = what
        lines = [
            getattr(finding, "message", lambda: str(finding))()
            for finding in self.findings
        ]
        super().__init__(
            f"{what} is blocked by {len(self.findings)} unresolved construct(s): "
            + " | ".join(lines)
        )


def require_unblocked(extraction: object, *, what: str = "typed-code extraction") -> object:
    """Return ``extraction`` unchanged, or raise :class:`ObligationBlocked`.

    An extraction is blocked when it carries any :class:`UnsupportedConstruct`
    finding.  The obligation is stated per finding (path, line, reason, what to
    do), so the author sees exactly which declaration to write.
    """
    findings: Iterable[object] = getattr(extraction, "unsupported", ()) or ()
    findings = list(findings)
    if findings:
        raise ObligationBlocked(findings, what=what)
    return extraction


def fingerprint(contract_dir: str | Path, *, sdl_path: str | Path | None = None) -> str:
    """The semantic contract fingerprint: ``sha256(print_schema(build_schema(sdl)))``."""
    try:
        return schema_adapter.contract_fingerprint(contract_dir, sdl_path=sdl_path)
    except schema_adapter.ContractDrift as exc:
        raise ContractDrift(str(exc)) from exc


def verify_contract(
    contract_dir: str | Path,
    *,
    expected: Optional[str] = PINNED_FINGERPRINT,
    sdl_path: str | Path | None = None,
) -> str:
    """Re-verify the contract and return the observed fingerprint.

    Raises :class:`ContractDrift` when the fingerprint differs from ``expected``
    (pass ``expected=None`` to observe without pinning) and when the SDL does not
    parse, build or validate.
    """
    directory = Path(contract_dir)
    target = Path(sdl_path) if sdl_path is not None else directory / CONTRACT_SDL_NAME
    if not target.is_file():
        raise ContractDrift(f"contract SDL not found: {target}")
    observed = fingerprint(directory, sdl_path=sdl_path)
    if expected is not None and observed != expected:
        raise ContractDrift(
            f"contract drift: expected {expected}, observed {observed} ({target})"
        )
    return observed


def read_pinned(contract_dir: str | Path) -> str:
    """Parse the pinned fingerprint out of the contract's decision log.

    A pin that cannot be read is a loud failure, never a silent fallback to the
    carried constant.
    """
    host = Path(contract_dir) / DECISIONS_NAME
    if not host.is_file():
        raise ContractDrift(f"pin host not found: {host}")
    match = PIN_HOST_PATTERN.search(host.read_text(encoding="utf-8"))
    if match is None:
        raise ContractDrift(f"pin host {host} carries no accepted fingerprint line")
    return match.group(1)


def pin_host_digest(contract_dir: str | Path) -> str:
    """SHA-256 of the decision-log file, as provenance for the pin."""
    host = Path(contract_dir) / DECISIONS_NAME
    if not host.is_file():
        raise ContractDrift(f"pin host not found: {host}")
    return hashlib.sha256(host.read_bytes()).hexdigest()


def verify_pin(contract_dir: str | Path, *, sdl_path: str | Path | None = None) -> dict:
    """The full pin check: host line, host digest, carried constant, SDL on disk.

    Fails when the decision log and the carried constant disagree (the pin was
    re-pinned without re-verification) and when the SDL drifts from the pin.
    """
    pinned = read_pinned(contract_dir)
    if pinned != PINNED_FINGERPRINT:
        raise ContractDrift(
            f"pin host says {pinned} but the module carries {PINNED_FINGERPRINT}"
        )
    observed = verify_contract(contract_dir, expected=pinned, sdl_path=sdl_path)
    return {
        "pinned": pinned,
        "observed": observed,
        "ok": observed == pinned,
        "host_digest": pin_host_digest(contract_dir),
    }