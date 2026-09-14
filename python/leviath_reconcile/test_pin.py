"""Pinned acceptance tests for GQL-02b: contract pin + adapter binding.

Every test asserts a **concrete expected value**, not a shape:

* the pin equals the accepted fingerprint GQL-01 published, byte for byte;
* the pin parses out of its host artifact (the decision log) and the two agree;
* both adapters run through ``verify_contract()`` end-to-end and the observed
  fingerprint equals the pinned one;
* the schema adapter reads the real SDL as ``graphql-core`` with 2136 facts and
  the code adapter reads the real typed Python as ``python-ast`` with 1995 facts;
* a tampered SDL and a wrong expected fingerprint both **fail loudly**;
* repeated reconciliation on unchanged input reaches one unchanged digest
  (no ping-pong) over 3 rounds.

Run with pytest, or directly:

    PYTHONPATH=python python3 python/leviath_reconcile/test_pin.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import pytest

from leviath_reconcile import pin
from leviath_reconcile.adapters.schema_adapter import (
    ContractDrift,
    contract_fingerprint,
    verify_contract,
)
from leviath_reconcile.cli import ACCEPTED_CONTRACT_FINGERPRINT

#: The accepted fingerprint, spelled out so a drift in the code is visible here.
EXPECTED_PINNED = "ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559"

#: Concrete adapter readings of the real accepted artifacts.
EXPECTED_SCHEMA_MODE = "graphql-core"
EXPECTED_SCHEMA_FACTS = 2136
EXPECTED_CODE_MODE = "python-ast"
EXPECTED_CODE_FACTS = 1995


def test_pinned_constant_is_the_accepted_fingerprint():
    assert pin.PINNED_FINGERPRINT == EXPECTED_PINNED
    assert len(pin.PINNED_FINGERPRINT) == 64
    # The CLI pin must be the same value; two pins that disagree are worse
    # than none.
    assert ACCEPTED_CONTRACT_FINGERPRINT == EXPECTED_PINNED


def test_pin_is_read_from_its_host_and_matches_the_constant():
    assert pin.read_pin_from_host() == EXPECTED_PINNED
    assert pin.pinned_fingerprint() == EXPECTED_PINNED


def test_pin_host_missing_fails_loudly(tmp_path: Path):
    missing = tmp_path / "no-such-decisions.md"
    with pytest.raises(pin.ContractPinMismatch) as excinfo:
        pin.read_pin_from_host(missing)
    assert "pin host not found" in str(excinfo.value)


def test_pin_host_without_fingerprint_line_fails_loudly(tmp_path: Path):
    host = tmp_path / "decisions.md"
    host.write_text("# no pin here\n", encoding="utf-8")
    with pytest.raises(pin.ContractPinMismatch) as excinfo:
        pin.read_pin_from_host(host)
    assert "carries no 'Accepted contract fingerprint' line" in str(excinfo.value)


def test_pin_host_edited_away_from_constant_fails_loudly(tmp_path: Path):
    host = tmp_path / "decisions.md"
    other = "0" * 64
    host.write_text(f"- Accepted contract fingerprint (printed-SDL SHA-256): `{other}`\n", encoding="utf-8")
    with pytest.raises(pin.ContractPinMismatch) as excinfo:
        pin.pinned_fingerprint(host)
    assert other in str(excinfo.value)


def test_verify_pin_reports_pinned_equals_observed():
    report = pin.verify_pin()
    assert report.ok is True
    assert report.reasons == ()
    assert report.pinned == EXPECTED_PINNED
    assert report.observed == EXPECTED_PINNED
    assert report.sdl.endswith("docs/workflow/schema-workflow.graphql")


def test_verify_pin_on_tampered_sdl_fails_loudly(tmp_path: Path):
    real = Path("docs/workflow/schema-workflow.graphql")
    tampered = tmp_path / "schema-workflow.graphql"
    text = real.read_text(encoding="utf-8")
    tampered.write_text(text + "\ntype InjectedDrift { x: Int }\n", encoding="utf-8")
    with pytest.raises(pin.ContractPinMismatch) as excinfo:
        pin.verify_pin(tmp_path, sdl_path=tampered)
    assert "contract pin mismatch" in str(excinfo.value)


def test_verify_contract_rejects_a_wrong_expected_fingerprint():
    # The gate itself must fail loudly, not merely report a difference.
    with pytest.raises(ContractDrift):
        verify_contract("0" * 64, "docs/workflow")


def test_contract_fingerprint_matches_the_pin():
    assert (
        contract_fingerprint("docs/workflow", sdl_path="docs/workflow/schema-workflow.graphql")
        == EXPECTED_PINNED
    )


def test_bind_adapters_observes_the_pinned_fingerprint():
    report = pin.bind_adapters()
    assert report.pin.observed == EXPECTED_PINNED
    assert report.pin.pinned == EXPECTED_PINNED
    assert report.pin.ok is True
    assert len(report.bindings) == 2
    assert [b.adapter for b in report.bindings] == ["schema_adapter", "python_adapter"]


def test_adapter_bindings_read_the_real_artifacts():
    report = pin.bind_adapters()
    schema, code = report.bindings
    assert schema.mode == EXPECTED_SCHEMA_MODE
    assert schema.projection_name == "schema"
    assert schema.fact_count == EXPECTED_SCHEMA_FACTS
    assert schema.warnings == ()
    assert code.mode == EXPECTED_CODE_MODE
    assert code.projection_name == "code"
    assert code.fact_count == EXPECTED_CODE_FACTS
    assert code.warnings == ()
    # ``.projection`` is the accessor; it carries the same facts the count saw.
    assert len(schema.projection.facts) == EXPECTED_SCHEMA_FACTS
    assert len(code.projection.facts) == EXPECTED_CODE_FACTS


def test_repeated_reconciliation_has_no_pingpong():
    report = pin.assert_no_pingpong(rounds=3)
    assert report.ok is True
    assert report.rounds == 3
    assert report.changed == (False, False, False)
    assert len(set(report.digests)) == 1
    assert report.digests[0] == "0f71ee64d562e67bc0b7c4a91f6678a41b26fe4b0dc9135c90e4c75c528c8ef0"


def test_run_pin_check_returns_one_pass_payload():
    payload = pin.run_pin_check()
    assert payload["result"] == "PASS"
    assert payload["pinned"] == EXPECTED_PINNED
    assert payload["observed"] == EXPECTED_PINNED
    assert payload["pingpong"]["ok"] is True
    assert [row["adapter"] for row in payload["adapters"]] == [
        "schema_adapter",
        "python_adapter",
    ]


def _run_plain() -> int:
    """Run the tests without pytest and print one success line."""
    import inspect
    import tempfile

    failures: List[str] = []
    ran = 0
    for name, value in sorted(globals().items()):
        if not name.startswith("test_") or not callable(value):
            continue
        ran += 1
        try:
            if inspect.signature(value).parameters:
                with tempfile.TemporaryDirectory() as tmp:
                    value(Path(tmp))
            else:
                value()
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1
    print(f"pin_suite: PASS — {ran} tests, pinned fingerprint verified end-to-end")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_plain())