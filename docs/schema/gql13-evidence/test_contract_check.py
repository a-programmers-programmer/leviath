"""RED/GREEN test for the GQL-13 Desk, queue, run and decision contract.

Runs the EXISTING deterministic gate (no model, no provider, no network) against
the contract authored in docs/schema/gql13. The gate is not re-authored: this
test only asserts the artifact passes it.

    python3 docs/schema/gql13-evidence/test_contract_check.py

Exit 0 when the gate reports status "checked".
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
ROOT = WT / "docs/schema/gql13"
GATE_SCRIPTS = WT / "crates/leviath-cli/agents/graphql-schema/scripts"
sys.path.insert(0, str(GATE_SCRIPTS))

import gate  # noqa: E402


def main() -> int:
    result = gate.dispatch({"op": "check", "root": str(ROOT)})
    print(json.dumps({k: result[k] for k in ("version", "status", "fingerprint")
                      if k in result}, sort_keys=True))
    for err in result.get("errors", []):
        print("  ERROR:", err)
    if result["status"] != "checked":
        print("FAIL: gate status is", result["status"])
        return 1
    # mode=new is the observed truth: this API is not the memory/kit SDL, so an
    # evolve baseline would be a lie about caller compatibility.
    assert not (ROOT / "baseline.graphql").exists(), "mode=new must carry no baseline"
    print("OK: checked, errors=0, fingerprint=" + result["fingerprint"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())