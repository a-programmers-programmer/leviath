#!/usr/bin/env python3
"""GQL-10 deterministic proof: print JSON checks, exit 1 if any check fails.

Re-executable by the dispatcher. Asserts:
  * the existing gate and its 24 regressions are byte-identical to base;
  * the join module cannot write accepted.json;
  * the join module is present and the new regressions exist;
  * proposal/merge success alone leaves no accepted.json receipt;
  * exhausted repair takes the explicit terminal route;
  * runtime verification and product judgment stay separate from acceptance.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent / "repo"
AGENT = REPO / "crates/leviath-cli/agents/graphql-schema"
sys.path.insert(0, str(AGENT / "scripts"))

BASE = "15dc182e0bc6c28dfa93f4b8dd3c423372808f61"
GATE_SHA = "554c3f7e66e1bfed7c808cbf16a92cff28e7ebac0157ea44e85845be83f8ee39"
TEST_GATE_SHA = "bb1d1cb7ebdb9357a40f49c29929de63332261d0a4b474c6df71d9d20e4f535a"

checks = {}


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(name, value):
    checks[name] = bool(value)
    return bool(value)


check("gate_is_byte_identical_to_base", sha256_file(AGENT / "scripts/gate.py") == GATE_SHA)
check("test_gate_is_byte_identical_to_base",
      sha256_file(AGENT / "tests/test_gate.py") == TEST_GATE_SHA)
check("existing_24_regressions_still_present",
      (AGENT / "tests/test_gate.py").read_text().count("    def test_") == 24)
check("new_join_regressions_present",
      (AGENT / "tests/test_join.py").read_text().count("    def test_") >= 19)
_hook = (AGENT / "hooks/protocol.rhai").read_text()
check("hook_counts_repair_passes", "repairs" in _hook)
check("hook_has_explicit_terminal_route", 'route = "blocked"' in _hook)

import gate  # noqa: E402
import join_candidates as jc  # noqa: E402

check("checker_version_recorded", gate.VERSION == 1)
check("two_repair_passes", jc.MAX_REPAIR_PASSES == 2)

# The join module must never write accepted.json itself.
source = (AGENT / "scripts/join_candidates.py").read_text()
writes_receipt = False
for line in source.splitlines():
    stripped = line.strip()
    if "accepted.json" in stripped and ("write_text" in stripped or "write_bytes" in stripped):
        writes_receipt = True
check("join_module_never_writes_accepted_json", not writes_receipt)

FIXTURE = AGENT / "examples/task-board"


def fresh_root():
    tmp = Path(tempfile.mkdtemp(prefix="gql10-proof-"))
    root = tmp / "graphql-contract"
    shutil.copytree(FIXTURE, root)
    return root


def record_for(root):
    cand = root / "schema.graphql"
    return {
        "state": "verified",
        "candidate_paths": {"a": str(cand), "b": str(cand)},
        "candidate_sha256": {"a": sha256_file(cand), "b": sha256_file(cand)},
        "merged": [str(cand), str(cand)],
        "merged_sha256": [sha256_file(cand)] * 2,
        "decisions": {"path": str(root / "design.md"), "sha256": sha256_file(root / "design.md")},
        "provenance": [{"run_id": "fixture-lane0"}, {"run_id": "fixture-lane1"}],
    }


def review_for(root):
    checked = gate.dispatch({"op": "check", "root": str(root)})
    return {"fingerprint": checked["fingerprint"], "verdict": "approve", "blocking_findings": [],
            "rubric": {k: {"pass": True, "evidence": "Deterministic GQL-10 proof fixture."}
                       for k in gate.RUBRIC}}


# 1. merge success with no review cannot mint accepted.json.
root = fresh_root()
res = jc.join(root, record_for(root), op="accept")
check("merge_success_alone_blocks", res["status"] == "blocked")
check("merge_success_alone_mints_no_receipt", not (root / "accepted.json").exists())

# 2. merged schema field without a contract policy blocks (omitted obligation).
root = fresh_root()
rec = record_for(root)
with (root / "schema.graphql").open("a") as fh:
    fh.write("\n# a field the contract omits would block here\n")
res = jc.join(root, rec, op="check")
check("tampered_merge_blocks_source_identity", res["status"] == "blocked")

# 3. exhausted repair takes the explicit terminal route.
root = fresh_root()
contract = json.loads((root / "contract.json").read_text())
contract["fields"].pop("Task.title")
(root / "contract.json").write_text(json.dumps(contract))
res = jc.join(root, record_for(root), op="check", repair_attempts=2)
check("exhausted_repair_blocks", res["status"] == "blocked")
check("exhausted_repair_route_is_terminal", res["route"] == "terminal")

# 4. a fresh review is the only path to the receipt, and it is minted by the gate.
root = fresh_root()
res = jc.join(root, record_for(root), op="accept", review=review_for(root))
check("fresh_review_accepts_through_existing_gate", res["status"] == "accepted")
check("gate_mints_the_receipt", (root / "accepted.json").is_file())

# 5. runtime verification and product judgment stay separate from acceptance.
check("runtime_verification_separate", res["runtime_verification"]["separate_from_acceptance"])
check("runtime_verification_unverified", res["runtime_verification"]["state"] == "unverified")
check("product_judgment_separate", res["product_judgment"]["separate_from_acceptance"])
check("product_judgment_not_made", res["product_judgment"]["state"] == "not_made")
receipt_text = (root / "accepted.json").read_text().lower()
check("receipt_carries_no_runtime_verification", "runtime_verified" not in receipt_text)
check("receipt_carries_no_product_judgment", "product_judgment" not in receipt_text)

# 6. checker, guide, source identity and review evidence are recorded.
check("checker_sha_recorded", res["checker"]["sha256"] == GATE_SHA)
check("guide_sha_recorded",
      res["guide"]["sha256"] == sha256_file(AGENT / "README.md"))
check("source_identity_recorded", res["source_identity"]["state"] == "verified")
check("review_evidence_recorded", res["contract"]["review_evidence"]["verdict"] == "approve")

result = {"checks": checks, "failed": sorted(k for k, v in checks.items() if not v),
          "passed": all(checks.values()), "count": len(checks)}
print(json.dumps(result, indent=2, sort_keys=True))
sys.exit(0 if result["passed"] else 1)