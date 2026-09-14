"""Contract pin: the accepted fingerprint as a checked, module-carried value.

GQL-02 depends on GQL-01's accepted contract (``workflow-model v1``).  This
module makes that dependency a **pin**, not a hope:

* :data:`PINNED_FINGERPRINT` is the accepted value the module carries.  It is
  the printed-SDL SHA-256 GQL-01 published.
* :data:`PIN_HOST` is where the pin is **read from**:
  ``docs/workflow/workflow-schema-decisions.md``, the append-only decision log
  whose ``2026-09-12 — GQL-01 reconciliation`` entry records
  ``- Accepted contract fingerprint (printed-SDL SHA-256): `<hex>`.``
  :func:`read_pin_from_host` parses that line, so the pin can be re-derived from
  the artifact of record instead of trusted blindly.
* :func:`pinned_fingerprint` reads the host and **fails loudly** when the host
  and the carried constant disagree (the decision log was edited).
* :func:`verify_pin` re-verifies the SDL on disk against the pin and raises
  :class:`ContractPinMismatch` on any drift.
* :func:`bind_adapters` drives **both** adapters through ``verify_contract()``
  end-to-end and returns the observed fingerprint beside the pinned one, plus
  each adapter's projection (``.projection``, never ``.facts``).
* :func:`assert_no_pingpong` re-reconciles the bound pair repeatedly and fails
  when the merged body moves after the first round.

The pin is fixed **before** dependent implementation: GQL-01 accepted the
fingerprint, GQL-02a built the reconciliation code against it, and this
sub-order (GQL-02b) re-verifies it before any dependent work is accepted.  A
mismatch is never a warning: it raises, so a drifted contract blocks the merge
instead of being reconciled as if it were the accepted model.

Offline: no provider, no network, no secret read, no production-store write.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .model import FactSet, Projection

#: The accepted contract fingerprint, pinned by GQL-01 and carried here.
#: Printed-SDL SHA-256: ``sha256(print_schema(build_schema(sdl)))``.
PINNED_FINGERPRINT = "ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559"

#: Where the pin is read from, relative to the repository root.
PIN_HOST = "docs/workflow/workflow-schema-decisions.md"

#: The decision-log line that records the accepted fingerprint.
PIN_HOST_PATTERN = re.compile(
    r"Accepted contract fingerprint \(printed-SDL SHA-256\):\s*`([0-9a-f]{64})`"
)

#: The contract SDL the pin covers, inside the contract directory.
CONTRACT_SDL_NAME = "schema-workflow.graphql"

#: Default contract directory and code counterpart, relative to the repo root.
DEFAULT_CONTRACT_DIR = "docs/workflow"
DEFAULT_CODE_PATH = "python/leviath_reconcile/fixtures/workflow_model.py"

#: When the pin was fixed, relative to the sub-orders that depend on it.
PINNED_BEFORE = "GQL-02a (reconciliation code) and GQL-02b (adapter binding)"


class ContractPinMismatch(RuntimeError):
    """The observed contract is not the pinned accepted contract."""


@dataclass(frozen=True)
class PinReport:
    """The result of a pin check: pinned value, observed value, provenance."""

    pinned: str
    observed: str
    sdl: str
    host: str
    host_digest: str
    ok: bool
    reasons: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "pinned": self.pinned,
            "observed": self.observed,
            "sdl": self.sdl,
            "host": self.host,
            "host_digest": self.host_digest,
            "ok": self.ok,
            "reasons": list(self.reasons),
        }


# ---------------------------------------------------------------------------
# Reading the pin from its host artifact.
# ---------------------------------------------------------------------------


def read_pin_from_host(host: str | os.PathLike = PIN_HOST) -> str:
    """Parse the accepted fingerprint out of the decision log.

    Raises :class:`ContractPinMismatch` when the host is missing or carries no
    fingerprint line — a pin that cannot be read is a loud failure, never a
    silent fallback to the constant.
    """
    path = Path(host)
    if not path.is_file():
        raise ContractPinMismatch(f"pin host not found: {path}")
    match = PIN_HOST_PATTERN.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ContractPinMismatch(
            f"pin host {path} carries no 'Accepted contract fingerprint' line"
        )
    return match.group(1)


def host_digest(host: str | os.PathLike = PIN_HOST) -> str:
    """SHA-256 of the pin host file, as provenance for the pin."""
    path = Path(host)
    if not path.is_file():
        raise ContractPinMismatch(f"pin host not found: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned_fingerprint(host: str | os.PathLike = PIN_HOST) -> str:
    """The pin, cross-checked between the host artifact and the carried constant.

    Raises :class:`ContractPinMismatch` when the two disagree: the decision log
    was edited after the pin was fixed, so the pin is no longer trustworthy.
    """
    from_host = read_pin_from_host(host)
    if from_host != PINNED_FINGERPRINT:
        raise ContractPinMismatch(
            f"pin host {Path(host)} says {from_host}, but the module carries "
            f"{PINNED_FINGERPRINT}; the pin was re-pinned without re-verification"
        )
    return PINNED_FINGERPRINT


# ---------------------------------------------------------------------------
# Verifying the pin against the contract on disk.
# ---------------------------------------------------------------------------


def verify_pin(
    contract_dir: str | os.PathLike = DEFAULT_CONTRACT_DIR,
    *,
    sdl_path: Optional[str | os.PathLike] = None,
    host: str | os.PathLike = PIN_HOST,
) -> PinReport:
    """Re-verify the SDL on disk against the pin.  Raises on any mismatch.

    Returns a :class:`PinReport` whose ``observed`` equals ``pinned`` when the
    check passes.  The SDL is read with the schema adapter's semantic
    fingerprint (printed-SDL SHA-256), so comments and formatting do not churn
    the pin, and any semantic change does.
    """
    from .adapters.schema_adapter import ContractDrift, contract_fingerprint

    pinned = pinned_fingerprint(host)
    target = Path(sdl_path) if sdl_path is not None else Path(contract_dir) / CONTRACT_SDL_NAME
    try:
        observed = contract_fingerprint(contract_dir, sdl_path=sdl_path)
    except ContractDrift as exc:
        raise ContractPinMismatch(f"pinned contract is unreadable: {exc}") from exc
    reasons: List[str] = []
    if observed != pinned:
        reasons.append(f"observed {observed} != pinned {pinned}")
    report = PinReport(
        pinned=pinned,
        observed=observed,
        sdl=str(target),
        host=str(host),
        host_digest=host_digest(host),
        ok=not reasons,
        reasons=tuple(reasons),
    )
    if reasons:
        raise ContractPinMismatch(
            f"contract pin mismatch for {target}: " + "; ".join(reasons)
        )
    return report


# ---------------------------------------------------------------------------
# Adapter binding: both adapters through verify_contract().
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterBinding:
    """One adapter driven through ``verify_contract()`` and its projection."""

    adapter: str
    mode: str
    projection_name: str
    document_identity: str
    fact_count: int
    warnings: Tuple[str, ...]
    projection: Projection = field(repr=False, compare=False)

    def as_dict(self) -> dict:
        return {
            "adapter": self.adapter,
            "mode": self.mode,
            "projection_name": self.projection_name,
            "document_identity": self.document_identity,
            "fact_count": self.fact_count,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class BindReport:
    """The end-to-end binding: pin report plus one binding per adapter."""

    pin: PinReport
    bindings: Tuple[AdapterBinding, ...]

    def as_dict(self) -> dict:
        return {
            "result": "PASS",
            "pinned": self.pin.pinned,
            "observed": self.pin.observed,
            "sdl": self.pin.sdl,
            "pin_host": self.pin.host,
            "pin_host_digest": self.pin.host_digest,
            "adapters": [b.as_dict() for b in self.bindings],
        }


def bind_adapters(
    contract_dir: str | os.PathLike = DEFAULT_CONTRACT_DIR,
    code_path: str | os.PathLike = DEFAULT_CODE_PATH,
    *,
    sdl_path: Optional[str | os.PathLike] = None,
    host: str | os.PathLike = PIN_HOST,
) -> BindReport:
    """Drive both adapters through ``verify_contract()`` end-to-end.

    Order matters and is the point: the pin is re-verified **first**, and only
    then is each adapter allowed to read its artifact.  The observed fingerprint
    is returned beside the pinned one, so a caller can compare them without
    trusting this function's word.  The accessor is ``.projection``; ``.facts``
    is not used.
    """
    from .adapters import python_adapter, schema_adapter

    pin = verify_pin(contract_dir, sdl_path=sdl_path, host=host)
    # Re-verify through the adapter's own gate, so the pin check that a
    # dependent actually calls is the one exercised here.
    schema_adapter.verify_contract(pin.pinned, contract_dir, sdl_path=sdl_path)

    schema_extraction = schema_adapter.load_sdl(
        Path(sdl_path) if sdl_path is not None else Path(contract_dir) / CONTRACT_SDL_NAME
    )
    code_extraction = python_adapter.load_python(code_path)

    bindings = []
    for name, extraction in (
        ("schema_adapter", schema_extraction),
        ("python_adapter", code_extraction),
    ):
        projection = extraction.projection
        bindings.append(
            AdapterBinding(
                adapter=name,
                mode=extraction.mode,
                projection_name=projection.name,
                document_identity=projection.document_identity,
                fact_count=len(projection.facts),
                warnings=tuple(extraction.warnings),
                projection=projection,
            )
        )
    return BindReport(pin=pin, bindings=tuple(bindings))


# ---------------------------------------------------------------------------
# No ping-pong: repeated reconciliation on unchanged input is a fixed point.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PingPongReport:
    """The fixed-point proof: one digest, one change flag, per round."""

    rounds: int
    digests: Tuple[str, ...]
    changed: Tuple[bool, ...]
    ok: bool
    detail: str

    def as_dict(self) -> dict:
        return {
            "rounds": self.rounds,
            "digests": list(self.digests),
            "changed": list(self.changed),
            "ok": self.ok,
            "detail": self.detail,
            "digest_stable": len(set(self.digests)) == 1,
        }


def _agreed_baseline(schema: Projection, code: Projection, fingerprint: str):
    from .baseline import Baseline

    return Baseline(
        revision=1,
        contract="workflow-model v1",
        contract_fingerprint=fingerprint,
        documents={schema.name: schema.document_identity, code.name: code.document_identity},
        body=FactSet(tuple(schema.facts) + tuple(code.facts)),
    )


def assert_no_pingpong(
    contract_dir: str | os.PathLike = DEFAULT_CONTRACT_DIR,
    code_path: str | os.PathLike = DEFAULT_CODE_PATH,
    *,
    rounds: int = 3,
    sdl_path: Optional[str | os.PathLike] = None,
    host: str | os.PathLike = PIN_HOST,
    work_dir: Optional[str] = None,
) -> PingPongReport:
    """Re-reconcile the pin-bound real pair and fail on any body movement.

    The reconciler is contract-pinned, so every round also re-verifies the
    fingerprint: the fixed-point proof and the pin proof are the same run.  The
    merged body must keep one digest and report ``changed=False`` after the
    first round, which is the DoD's "without ping-pong" clause.
    """
    from .baseline import BaselineStore
    from .reconcile import Reconciler

    binding = bind_adapters(contract_dir, code_path, sdl_path=sdl_path, host=host)
    schema, code = (b.projection for b in binding.bindings)
    fingerprint = binding.pin.pinned

    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        store = BaselineStore(os.path.join(tmp, "baseline.json"))
        current = _agreed_baseline(schema, code, fingerprint)
        digests: List[str] = []
        changed: List[bool] = []
        for index in range(rounds):
            reconciler = Reconciler(
                current,
                expected_contract_fingerprint=fingerprint,
                contract_dir=str(contract_dir),
                contract_sdl=str(sdl_path) if sdl_path is not None else None,
            )
            outcome = reconciler.reconcile(schema, code)
            if outcome.plan.conflicts or not outcome.plan.ok:
                raise AssertionError(
                    f"round {index}: pinned pair must not conflict: {outcome.plan.summary()}"
                )
            digests.append(outcome.body.digest)
            changed.append(bool(outcome.changed))
            reconciler.accept(schema, code, accepted_at="2026-09-12T00:00:00Z")
            current = reconciler.accepted
            store.commit(current, expected_revision=index + 1)

    stable = len(set(digests)) == 1
    quiet = not any(changed)
    ok = stable and quiet
    detail = (
        f"{rounds} rounds re-reconciled the pin-bound pair; body digest stable at "
        f"{digests[0][:12]}; zero changes after the first"
        if ok
        else f"ping-pong: digests={digests} changed={changed}"
    )
    report = PingPongReport(
        rounds=rounds,
        digests=tuple(digests),
        changed=tuple(changed),
        ok=ok,
        detail=detail,
    )
    if not ok:
        raise AssertionError(detail)
    return report


def run_pin_check(
    contract_dir: str | os.PathLike = DEFAULT_CONTRACT_DIR,
    code_path: str | os.PathLike = DEFAULT_CODE_PATH,
    *,
    rounds: int = 3,
    sdl_path: Optional[str | os.PathLike] = None,
    host: str | os.PathLike = PIN_HOST,
    work_dir: Optional[str] = None,
) -> Dict[str, object]:
    """Pin + adapter binding + no-ping-pong, as one serialisable PASS payload."""
    binding = bind_adapters(contract_dir, code_path, sdl_path=sdl_path, host=host)
    pingpong = assert_no_pingpong(
        contract_dir, code_path, rounds=rounds, sdl_path=sdl_path, host=host, work_dir=work_dir
    )
    payload = binding.as_dict()
    payload["contract"] = "leviath-reconcile/pin-check/v1"
    payload["pinned_before"] = PINNED_BEFORE
    payload["pingpong"] = pingpong.as_dict()
    return payload