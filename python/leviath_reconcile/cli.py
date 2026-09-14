"""``python -m leviath_reconcile`` — offline reconciliation driver.

Subcommands (all offline: no provider call, no network, no secret read, no
production-store mutation):

``selftest``
    Run the GQL-02 definition-of-done scenarios plus the crosswalk and rule
    self-tests.  Exit 0 = PASS, 1 = FAIL.  This is the command the evidence
    transcript runs.
``verify-contract``
    Re-verify the pinned accepted contract fingerprint (GQL-01) against the
    artifact set on disk.  Exit 0 = match, 1 = drift.
``extract``
    Print the shared structured representation extracted from one SDL or Python
    artifact (facts + warnings), for inspecting the projection an adapter yields.
``reconcile``
    Reconcile an SDL artifact against a Python artifact: print the plan, the
    conflicts and the merged state, with ``--json`` for machine reading.  Exit
    0 = clean, 3 = conflicts present (the plan is still printed in full).

Example:
    PYTHONPATH=python python3 -m leviath_reconcile selftest --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from . import VERSION
from .reconcile import Reconciler
from .scenarios import run_all

#: Exit codes.  Distinct so a caller can tell "disagreement" from "broken".
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_CONFLICTS = 3

DEFAULT_CONTRACT_DIR = "docs/workflow"
#: Pinned by GQL-01 (docs/workflow/check-workflow-contract.py).
ACCEPTED_CONTRACT_FINGERPRINT = "ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559"


def _print_json(payload) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_selftest(args: argparse.Namespace) -> int:
    result = run_all(work_dir=args.work_dir)
    if args.json:
        _print_json(result)
    else:
        print(f"leviath-reconcile {VERSION} — self-test: {result['result']}")
        for scenario in result["scenarios"]:
            mark = "PASS" if scenario["ok"] else "FAIL"
            print(f"  [{mark}] {scenario['name']:<12} {scenario['detail']}")
        print(
            f"  crosswalk: {result['crosswalk']['self_test']} "
            f"({result['crosswalk']['cases']} cases)"
        )
        print(
            f"  invariants: {result['invariants']['self_test']} "
            f"({result['invariants']['cases']} cases)"
        )
        for failure in result["failures"]:
            print(f"  FAILURE: {failure}", file=sys.stderr)
    return EXIT_PASS if result["result"] == "PASS" else EXIT_FAIL


def cmd_verify_contract(args: argparse.Namespace) -> int:
    from .adapters.schema_adapter import (
        ContractDrift,
        contract_artifact_fingerprint,
        contract_fingerprint,
        verify_contract,
    )

    expected = args.expected or ACCEPTED_CONTRACT_FINGERPRINT
    sdl_path = args.sdl or None
    try:
        observed = verify_contract(expected, args.contract_dir, sdl_path=sdl_path)
    except ContractDrift as exc:
        observed = ""
        try:
            observed = contract_fingerprint(args.contract_dir, sdl_path=sdl_path)
        except ContractDrift:
            observed = "<unreadable>"
        if args.json:
            _print_json(
                {
                    "result": "FAIL",
                    "expected": expected,
                    "observed": observed,
                    "artifact_fingerprint": contract_artifact_fingerprint(args.contract_dir),
                    "error": str(exc),
                }
            )
        else:
            print(f"contract drift: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if args.json:
        _print_json(
            {
                "result": "PASS",
                "expected": expected,
                "observed": observed,
                "artifact_fingerprint": contract_artifact_fingerprint(args.contract_dir),
                "sdl": args.sdl or str(Path(args.contract_dir) / "schema-workflow.graphql"),
            }
        )
    else:
        print(f"contract fingerprint OK: {observed}")
    return EXIT_PASS


def cmd_extract(args: argparse.Namespace) -> int:
    from .adapters import python_adapter, schema_adapter

    path = Path(args.artifact)
    if not path.is_file():
        print(f"no such artifact: {path}", file=sys.stderr)
        return EXIT_USAGE
    extraction = (
        python_adapter.load_python(path)
        if args.kind == "python"
        else schema_adapter.load_sdl(path)
    )
    projection = extraction.projection
    if args.json:
        _print_json(
            {
                "projection": projection.name,
                "document_identity": projection.document_identity,
                "digest": projection.digest,
                "mode": extraction.mode,
                "warnings": list(extraction.warnings),
                "fact_count": len(projection.facts),
                "facts": [f.as_dict() if hasattr(f, "as_dict") else repr(f) for f in projection.facts],
            }
        )
    else:
        print(f"{projection.name}: {len(projection.facts)} facts, mode={extraction.mode}")
        print(f"document identity: {projection.document_identity}")
        print(f"digest: {projection.digest}")
        for warning in extraction.warnings:
            print(f"  WARN {warning}")
        for fact in projection.facts:
            print(f"  {fact.symbol_key:<48} {fact.attribute:<24} {fact.value}")
    return EXIT_PASS


def cmd_reconcile(args: argparse.Namespace) -> int:
    from .adapters import python_adapter, schema_adapter
    from .baseline import Baseline, BaselineStore

    schema_path, code_path = Path(args.schema), Path(args.code)
    for path in (schema_path, code_path):
        if not path.is_file():
            print(f"no such artifact: {path}", file=sys.stderr)
            return EXIT_USAGE

    schema = schema_adapter.load_sdl(schema_path).projection
    code = python_adapter.load_python(code_path).projection

    baseline = Baseline()
    if args.baseline:
        baseline = BaselineStore(args.baseline).load()

    reconciler = Reconciler(
        baseline,
        auto_apply_divergence=args.auto_apply_divergence,
        expected_contract_fingerprint=(
            ACCEPTED_CONTRACT_FINGERPRINT if args.verify_contract else ""
        ),
        contract_dir=args.contract_dir if args.verify_contract else None,
        contract_sdl=args.sdl or None,
    )
    if args.current_schema or args.current_code:
        reconciler.declare(
            {
                "schema": args.current_schema or schema.document_identity,
                "code": args.current_code or code.document_identity,
            }
        )

    outcome = reconciler.reconcile(schema, code)
    plan = outcome.plan

    if args.json:
        _print_json(
            {
                "contract": "leviath-reconcile/v1",
                "ok": plan.ok,
                "blocked": list(plan.blocked),
                "stale": list(plan.stale),
                "changed": outcome.changed,
                "body_digest": outcome.body.digest,
                "identity_report": plan.identity_report,
                "plan": plan.as_dict(),
                "conflicts": [c.as_dict() for c in plan.conflicts],
                "resolutions": [r.as_dict() for r in plan.resolutions],
            }
        )
    else:
        print(f"plan: {plan.summary()}")
        print(f"body digest: {outcome.body.digest}")
        if plan.stale:
            for reason in plan.stale:
                print(f"  STALE {reason}")
        for code_name, count in sorted(plan.edit_counts.items()):
            print(f"  {code_name}: {count}")
        for resolution in plan.resolutions:
            print(
                f"  {resolution.origin:<22} {resolution.symbol_key:<32} "
                f"{resolution.attribute:<24} -> {resolution.value}"
            )
        for conflict in plan.conflicts:
            print(f"  CONFLICT {conflict.code}: {conflict.message}")
            for option in conflict.options:
                print(f"      option: {option}")
        if plan.identity_report.get("renamed"):
            print("  renames:")
            for row in plan.identity_report["renamed"]:
                print(f"      {row['candidate']} -> {row['canonical']} ({row['reason']})")
        for failure in list(plan.blocked):
            print(f"  BLOCKED {failure}", file=sys.stderr)

    if not plan.ok or plan.stale or plan.blocked:
        return EXIT_FAIL
    if plan.conflicts:
        # Conflicts are a real answer, not an error of the tool, so they get
        # their own exit code and the plan above is the actionable output.
        return EXIT_CONFLICTS
    return EXIT_PASS


def cmd_e2e_real(args: argparse.Namespace) -> int:
    from .e2e_real import run_e2e

    try:
        result = run_e2e(args.contract_dir, args.code, work_dir=args.work_dir)
    except Exception as exc:  # noqa: BLE001 - a CLI must report, not traceback
        if args.json:
            _print_json({"result": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
        else:
            print(f"e2e-real: FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if args.json:
        _print_json(result)
    else:
        print(f"e2e-real: {result['result']}")
        for check in result["checks"]:
            print(f"  [PASS] {check}")
        print(f"  body digest: {result['metrics']['body_digest']}")
    return EXIT_PASS


def cmd_pin_check(args: argparse.Namespace) -> int:
    from . import pin

    try:
        payload = pin.run_pin_check(
            args.contract_dir,
            args.code,
            rounds=args.rounds,
            sdl_path=args.sdl or None,
            work_dir=args.work_dir,
        )
    except Exception as exc:  # noqa: BLE001 - a CLI must report, not traceback
        if args.json:
            _print_json({"result": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
        else:
            print(f"pin-check: FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if args.json:
        _print_json(payload)
    else:
        print(
            f"pin-check: {payload['result']} pinned={payload['pinned'][:16]} "
            f"observed={payload['observed'][:16]}"
        )
        for row in payload["adapters"]:
            print(
                f"  [{row['adapter']}] {row['mode']} facts={row['fact_count']} "
                f"projection={row['projection_name']}"
            )
        print(f"  no-ping-pong: {payload['pingpong']['detail']}")
    return EXIT_PASS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m leviath_reconcile",
        description="Offline schema/code reconciliation and conflict detection (GQL-02).",
    )
    parser.add_argument("--version", action="version", version=f"leviath-reconcile {VERSION}")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    selftest = subparsers.add_parser(
        "selftest", help="run the definition-of-done scenarios"
    )
    selftest.add_argument("--work-dir", default=None, help="scratch dir for the baseline CAS scenario")
    selftest.add_argument("--json", action="store_true")
    selftest.set_defaults(func=cmd_selftest)

    verify = subparsers.add_parser(
        "verify-contract", help="re-verify the pinned accepted contract fingerprint"
    )
    verify.add_argument("--contract-dir", default=DEFAULT_CONTRACT_DIR)
    verify.add_argument("--sdl", default="", help="override the contract SDL path")
    verify.add_argument("--expected", default="")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify_contract)

    extract = subparsers.add_parser("extract", help="print the shared representation of one artifact")
    extract.add_argument("artifact")
    extract.add_argument("--kind", choices=("graphql", "python"), default="graphql")
    extract.add_argument("--json", action="store_true")
    extract.set_defaults(func=cmd_extract)

    reconcile = subparsers.add_parser(
        "reconcile", help="reconcile an SDL artifact against a Python artifact"
    )
    reconcile.add_argument("schema")
    reconcile.add_argument("code")
    reconcile.add_argument("--baseline", default=None, help="baseline JSON to compare against")
    reconcile.add_argument("--current-schema", default=None, help="current document identity of the SDL")
    reconcile.add_argument("--current-code", default=None, help="current document identity of the code")
    reconcile.add_argument("--contract-dir", default=DEFAULT_CONTRACT_DIR)
    reconcile.add_argument("--sdl", default="", help="override the contract SDL path for the pin")
    reconcile.add_argument(
        "--verify-contract",
        action="store_true",
        help="abort on accepted-contract drift before reconciling",
    )
    reconcile.add_argument(
        "--auto-apply-divergence",
        action="store_true",
        help="apply the policy's default for a divergence instead of raising a conflict",
    )
    reconcile.add_argument("--json", action="store_true")
    reconcile.set_defaults(func=cmd_reconcile)

    e2e = subparsers.add_parser(
        "e2e-real",
        help="real-artifact end-to-end proof against the accepted contract SDL and its code counterpart",
    )
    e2e.add_argument("--contract-dir", default=DEFAULT_CONTRACT_DIR)
    e2e.add_argument("--code", default="python/leviath_reconcile/fixtures/workflow_model.py")
    e2e.add_argument("--work-dir", default=None)
    e2e.add_argument("--json", action="store_true")
    e2e.set_defaults(func=cmd_e2e_real)

    pin_parser = subparsers.add_parser(
        "pin-check",
        help="re-verify the pinned accepted contract and bind both adapters end-to-end",
    )
    pin_parser.add_argument("--contract-dir", default=DEFAULT_CONTRACT_DIR)
    pin_parser.add_argument("--code", default="python/leviath_reconcile/fixtures/workflow_model.py")
    pin_parser.add_argument("--sdl", default="", help="override the contract SDL path")
    pin_parser.add_argument("--rounds", type=int, default=3)
    pin_parser.add_argument("--work-dir", default=None)
    pin_parser.add_argument("--json", action="store_true")
    pin_parser.set_defaults(func=cmd_pin_check)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BrokenPipeError:  # pragma: no cover - piping into head
        return EXIT_PASS
    except Exception as exc:  # noqa: BLE001 - a CLI must report, not traceback
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())