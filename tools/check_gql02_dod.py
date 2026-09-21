#!/usr/bin/env python3
"""Mechanical proof of the GQL-02 definition of done, run from the worktree root.

This script is the *evidence command*.  It re-runs the real reconciliation engine
and asserts one clause of the definition of done at a time, plus the two
discrimination checks that make the evidence trustworthy:

* the contract gate must FAIL loudly on a drifted pin (exit 1, not 0);
* the acceptance script must be deterministic (two runs, byte-identical output).

It calls the CLI, pytest and the acceptance script as subprocesses; it does not
import the package, so a broken import surface cannot hide behind this script.

Exit 0 = every clause proven, 1 = at least one clause failed.

    python3 tools/check_gql02_dod.py [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCEPTANCE = os.path.join("docs", "workflow", "evidence", "run-gql02-acceptance.sh")
TRANSCRIPT = os.path.join("docs", "workflow", "evidence", "gql-02-acceptance-run.txt")

#: The pin GQL-01 accepted, spelled out so drift is visible in this file too.
PINNED = "ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559"

#: The conflict codes the concurrent-edit clause must produce.
CONFLICT_CODES = [
    "add-vs-delete",
    "ambiguous-identity",
    "delete-vs-edit",
    "existence-divergence",
    "rename-divergence",
    "rename-vs-delete",
    "type-divergence",
]

#: Adapter readings of the real accepted artifacts.
SCHEMA_FACTS = 2136
CODE_FACTS = 1995


class Failure(AssertionError):
    """One clause did not hold."""


def _run(
    cmd: str, env_extra: Dict[str, str] | None = None, merge: bool = False
) -> Tuple[int, str, str]:
    """Run ``cmd`` and return (rc, stdout, stderr).

    With ``merge=True`` stderr is folded into stdout (``2>&1``), which is how the
    recorded acceptance transcript was captured.  Comparing stderr-separated
    stdout against that transcript would compare the wrong stream, so the one
    caller that reads the transcript asks for the merged stream.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = "python"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        cmd, shell=True, cwd=ROOT, text=True, env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge else subprocess.PIPE,
    )
    if merge:
        return proc.returncode, proc.stdout, ""
    return proc.returncode, proc.stdout, proc.stderr


def _cli(args: str) -> dict:
    rc, out, err = _run(f"python3 -m leviath_reconcile {args} --json")
    if rc != 0:
        raise Failure(f"{args}: exit {rc}, stderr={err.strip()[:300]}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise Failure(f"{args}: output is not JSON: {exc}") from exc


def _scenario(payload: dict, name: str) -> dict:
    for scenario in payload["scenarios"]:
        if scenario["name"] == name:
            if not scenario["ok"]:
                raise Failure(f"scenario {name} failed: {scenario['detail'][:400]}")
            return scenario
    raise Failure(f"scenario {name} is missing from selftest output")


def check_selftest() -> Dict[str, object]:
    """All four DoD clauses run in one engine self-test."""
    payload = _cli("selftest")
    if payload["result"] != "PASS":
        raise Failure(f"selftest result={payload['result']}: {payload['failures']}")
    for name in ("symmetry", "concurrent", "staleness", "idempotence"):
        _scenario(payload, name)
    if payload["crosswalk"]["self_test"] != "PASS":
        raise Failure("crosswalk self-test did not pass")
    if payload["invariants"]["self_test"] != "PASS":
        raise Failure("invariants self-test did not pass")
    return {
        "result": payload["result"],
        "scenarios": {s["name"]: s["ok"] for s in payload["scenarios"]},
        "crosswalk": payload["crosswalk"]["self_test"],
        "crosswalk_cases": payload["crosswalk"]["cases"],
        "invariants": payload["invariants"]["self_test"],
        "invariants_cases": payload["invariants"]["cases"],
    }


def check_symmetry() -> Dict[str, object]:
    """Clause 1: a schema-only edit and the mirrored code-only edit are symmetric."""
    payload = _cli("e2e-real")
    if payload["result"] != "PASS":
        raise Failure(f"e2e-real result={payload['result']}")
    checks = payload["checks"]
    wanted = [c for c in checks if "schema-only" in c]
    if len(wanted) != 2:
        raise Failure(f"expected the schema-only rename and retype checks, got {wanted}")
    metrics = payload["metrics"]
    if metrics["schema_facts"] != SCHEMA_FACTS or metrics["code_facts"] != CODE_FACTS:
        raise Failure(
            f"real artifact sizes moved: {metrics['schema_facts']}/{metrics['code_facts']}"
        )
    return {
        "schema_only_checks": wanted,
        "schema_facts": metrics["schema_facts"],
        "code_facts": metrics["code_facts"],
        "schema_mode": metrics["sdl_mode"],
        "code_mode": metrics["code_mode"],
    }


def check_concurrent() -> Dict[str, object]:
    """Clause 2: competing edits preserve both versions and stay actionable."""
    payload = _cli("selftest")
    scenario = _scenario(payload, "concurrent")
    observed = sorted(scenario["metrics"]["conflict_codes"])
    if observed != CONFLICT_CODES:
        raise Failure(f"conflict codes are {observed}, expected {CONFLICT_CODES}")
    return {
        "conflict_codes": observed,
        "detail": scenario["detail"],
        "disjoint_conflicts": scenario["metrics"]["disjoint_conflicts"],
    }


def check_staleness() -> Dict[str, object]:
    """Clause 3: a stale document version cannot overwrite newer work."""
    payload = _cli("selftest")
    scenario = _scenario(payload, "staleness")
    e2e = _cli("e2e-real")
    stale_check = [c for c in e2e["checks"] if "superseded" in c]
    if not stale_check:
        raise Failure("e2e-real did not exercise a superseded real document")
    return {
        "selftest_detail": scenario["detail"],
        "e2e_real": stale_check,
    }


def check_idempotence() -> Dict[str, object]:
    """Clause 4: repeated reconciliation reaches an unchanged result, no ping-pong."""
    selftest = _scenario(_cli("selftest"), "idempotence")
    pin = _cli("pin-check")
    if pin["result"] != "PASS":
        raise Failure(f"pin-check result={pin['result']}")
    if pin["pinned"] != PINNED or pin["observed"] != PINNED:
        raise Failure(f"pin moved: pinned={pin['pinned']} observed={pin['observed']}")
    ping = pin["pingpong"]
    if not ping["ok"] or len(set(ping["digests"])) != 1:
        raise Failure(f"ping-pong detected: {ping}")
    if any(ping["changed"]):
        raise Failure(f"a round reported a change: {ping['changed']}")
    bindings = {row["adapter"]: row for row in pin["adapters"]}
    if bindings["schema_adapter"]["fact_count"] != SCHEMA_FACTS:
        raise Failure("schema adapter fact count moved")
    if bindings["python_adapter"]["fact_count"] != CODE_FACTS:
        raise Failure("code adapter fact count moved")
    return {
        "selftest_digest": selftest["metrics"]["body_digest"],
        "selftest_changed_rounds": selftest["metrics"]["changed_rounds"],
        "pin_digest": ping["digests"][0],
        "pin_rounds": ping["rounds"],
        "pin_changed": ping["changed"],
    }


def check_contract_pin() -> Dict[str, object]:
    """The unit tests, and the pinned fingerprint re-verified from disk."""
    rc, out, err = _run("python3 -m pytest python/leviath_reconcile/test_pin.py -q")
    if rc != 0:
        raise Failure(f"pytest exit {rc}: {(out + err)[-300:]}")
    if "13 passed" not in out:
        raise Failure(f"pytest did not report 13 passed: {out[-200:]}")
    payload = _cli("verify-contract")
    if payload["observed"] != PINNED or payload["result"] != "PASS":
        raise Failure(f"contract pin drift: {payload}")
    return {
        "pytest": "13 passed",
        "pinned": payload["expected"],
        "observed": payload["observed"],
        "sdl": payload["sdl"],
    }


def check_gate_discrimination() -> Dict[str, object]:
    """The gate must FAIL loudly on a wrong pin, not merely report a difference."""
    wrong = "0" * 64
    rc, out, err = _run(
        "python3 -m leviath_reconcile verify-contract --expected " + wrong
    )
    if rc != 1:
        raise Failure(f"a wrong pin must exit 1, got {rc}")
    text = out + err
    if "contract drift" not in text or wrong not in text:
        raise Failure(f"the failure is not loud: {text[-300:]}")
    return {"exit": rc, "message": "contract drift: expected " + wrong[:16] + "... observed " + PINNED[:16] + "..."}


def check_determinism() -> Dict[str, object]:
    """Two consecutive acceptance runs must be byte-identical, and match the transcript."""
    digests: List[str] = []
    for _ in range(2):
        rc, out, err = _run(f"bash {ACCEPTANCE}", merge=True)
        if rc != 0:
            raise Failure(f"{ACCEPTANCE} exit {rc}: {(out + err)[-400:]}")
        digests.append(hashlib.sha256(out.encode()).hexdigest())
    if digests[0] != digests[1]:
        raise Failure(f"the acceptance run is not deterministic: {digests}")
    transcript = open(os.path.join(ROOT, TRANSCRIPT), "rb").read()
    recorded = hashlib.sha256(transcript).hexdigest()
    if recorded != digests[0]:
        raise Failure(
            f"the recorded transcript {recorded[:16]} does not match a fresh run {digests[0][:16]}"
        )
    if "GQL-02 acceptance: PASS" not in transcript.decode():
        raise Failure("the recorded transcript does not end in PASS")
    return {
        "script": ACCEPTANCE,
        "transcript": TRANSCRIPT,
        "run_digest": digests[0],
        "runs_identical": True,
        "bytes": len(transcript),
    }


CHECKS = (
    ("selftest (all four clauses)", check_selftest),
    ("DoD 1 symmetry", check_symmetry),
    ("DoD 2 concurrent conflicts", check_concurrent),
    ("DoD 3 staleness", check_staleness),
    ("DoD 4 idempotence / no ping-pong", check_idempotence),
    ("contract pin + unit tests", check_contract_pin),
    ("gate discrimination", check_gate_discrimination),
    ("acceptance determinism", check_determinism),
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    evidence: Dict[str, object] = {}
    failures: List[str] = []
    for label, func in CHECKS:
        try:
            evidence[label] = func()
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            failures.append(f"{label}: {type(exc).__name__}: {exc}")

    ok = not failures
    if args.json:
        print(json.dumps({"result": "PASS" if ok else "FAIL", "checks": evidence, "failures": failures}, indent=2, sort_keys=True))
    else:
        for label in evidence:
            print(f"  [PASS] {label}")
        for failure in failures:
            print(f"  [FAIL] {failure}", file=sys.stderr)
        print(f"gql-02 DoD: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
