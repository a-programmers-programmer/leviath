"""``python -m leviath_codegen`` — the simple code generation hook.

Two subcommands, both offline (no provider call, no network, no secret read, no
production-store write):

``generate --contract <dir> --out <dir>``
    Read the reconciled contract snapshot, verify the pin, and emit the typed
    Python and TypeScript outputs plus the source map and the manifest.  Exit 0 =
    PASS, 1 = the contract identity is stale, unknown or tampered.
``verify --out <dir>``
    Re-hash the generated files against the manifest, compile and typecheck the
    Python output, typecheck the TypeScript output, and validate the generated
    operations against the generated SDL.  Exit 0 = PASS, 1 = FAIL.

Example:
    PYTHONPATH=python python3 -m leviath_codegen generate --contract contract --out out
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Sequence

from . import VERSION

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def _print_json(payload) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def cmd_generate(args: argparse.Namespace) -> int:
    from .generate import generate
    from .model import ContractDrift

    try:
        report = generate(
            args.contract,
            args.out,
            sdl_path=args.sdl or None,
            expected_fingerprint=args.expected_fingerprint or None,
        )
    except ContractDrift as exc:
        if args.json:
            _print_json({"result": "FAIL", "error": f"ContractDrift: {exc}"})
        else:
            print(f"generate: FAIL: contract identity rejected: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if args.json:
        _print_json(report.as_dict())
    else:
        print(
            f"generate: {report.result} contract={report.contract_fingerprint[:16]} "
            f"operations={len(report.operations)}"
        )
        print(f"  output: {report.output_dir}")
        print(f"  written: {len(report.written)}  unchanged: {len(report.unchanged)}")
        for path in report.written:
            print(f"  WRITE {path}")
        for path in report.handwritten_created:
            print(f"  SEED  {path}")
        for path in report.handwritten_kept:
            print(f"  KEEP  {path}")
    return EXIT_PASS


def cmd_verify(args: argparse.Namespace) -> int:
    from .model import ContractDrift
    from .verify import verify_outputs

    try:
        payload = verify_outputs(args.out)
    except ContractDrift as exc:
        if args.json:
            _print_json({"result": "FAIL", "error": f"ContractDrift: {exc}"})
        else:
            print(f"verify: FAIL: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if args.json:
        _print_json(payload)
    else:
        print(f"verify: {payload['result']} contract={str(payload['contract_fingerprint'])[:16]}")
        print(f"  manifest:    {payload['manifest']['result']}")
        print(f"  python:      {payload['python']['result']}")
        print(f"  typescript:  {payload['typescript']['result']}")
        print(f"  agreement:   {payload['agreement']['result']}")
        for problem in payload["manifest"]["problems"]:
            print(f"  PROBLEM {problem}", file=sys.stderr)
        for error in payload["agreement"]["errors"]:
            print(f"  ERROR {error}", file=sys.stderr)
    return EXIT_PASS if payload["result"] == "PASS" else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m leviath_codegen",
        description="Deterministic code generation from the reconciled contract snapshot (GQL-08).",
    )
    parser.add_argument("--version", action="version", version=f"leviath-codegen {VERSION}")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("generate", help="emit the typed outputs for a contract snapshot")
    gen.add_argument("--contract", required=True, help="the reconciled contract snapshot directory")
    gen.add_argument("--out", required=True, help="the output directory")
    gen.add_argument("--sdl", default="", help="override the contract SDL path")
    gen.add_argument(
        "--expected-fingerprint",
        default="",
        help="refuse to generate unless the contract carries this fingerprint",
    )
    gen.add_argument("--json", action="store_true")
    gen.set_defaults(func=cmd_generate)

    ver = subparsers.add_parser("verify", help="compile/typecheck and re-check the generated output")
    ver.add_argument("--out", required=True, help="the output directory to verify")
    ver.add_argument("--json", action="store_true")
    ver.set_defaults(func=cmd_verify)

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