"""GQL-10 regressions: join proposal/merge work to the existing acceptance loop.

These tests assert the join reuses the existing gate, blocks stale reviews, omitted
obligations and exhausted repair, cannot mint accepted.json from merge success, and
keeps runtime verification and product judgment separate from contract acceptance.
Fixture run ids are obviously synthetic; no real lane is claimed here.
"""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import gate
import join_candidates


class JoinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "graphql-contract"
        shutil.copytree(HERE / "examples/task-board", self.root)
        self.source = self.base / "merge"
        self.source.mkdir()
        self.sdl = (self.root / "schema.graphql").read_text()
        self.a = self.source / "cand_a.graphql"
        self.b = self.source / "cand_b.graphql"
        self.merged = self.source / "merged.graphql"
        self.decisions = self.source / "decisions.json"
        self.a.write_text(self.sdl)
        self.b.write_text(self.sdl)
        self.merged.write_text(self.sdl)
        self.decisions.write_text('{"contract": "harnessql-merge-decisions/v1"}')

    def record(self, **kwargs):
        record = {
            "state": "verified",
            "candidate_paths": {"a": str(self.a), "b": str(self.b)},
            "candidate_sha256": {"a": join_candidates.sha256_file(self.a),
                                 "b": join_candidates.sha256_file(self.b)},
            "merged": [str(self.merged), str(self.merged)],
            "merged_sha256": [join_candidates.sha256_file(self.merged)] * 2,
            "decisions": {"path": str(self.decisions),
                          "sha256": join_candidates.sha256_file(self.decisions)},
            "provenance": [{"lane": 0, "run_id": "fixture-lane0", "state": "verified"},
                           {"lane": 1, "run_id": "fixture-lane1", "state": "verified"}],
            "convergence": {"independent": True, "label": "two independent candidates"},
        }
        record.update(kwargs)
        return record

    def review(self, root=None):
        checked = gate.dispatch({"op": "check", "root": str(root or self.root)})
        return {"fingerprint": checked["fingerprint"], "verdict": "approve",
                "blocking_findings": [],
                "rubric": {key: {"pass": True,
                                 "evidence": "Synthetic GQL-10 test approval, not a real review."}
                           for key in gate.RUBRIC}}

    def change_contract(self, update):
        path = self.root / "contract.json"
        data = json.loads(path.read_text())
        update(data)
        path.write_text(json.dumps(data))

    def assert_blocked(self, result, text):
        self.assertEqual("blocked", result["status"])
        self.assertIn(text, " ".join(result["errors"]))
        self.assertFalse((self.root / "accepted.json").exists())

    def test_merge_success_alone_never_mints_accepted_json(self):
        result = join_candidates.join(self.root, self.record())
        self.assertNotEqual("accepted", result["status"])
        self.assertNotEqual("accepted", result["contract"]["status"])
        self.assertFalse((self.root / "accepted.json").exists())

    def test_join_reuses_the_existing_gate(self):
        calls = []
        real = join_candidates.gate.dispatch

        def spy(request):
            calls.append(request)
            return real(request)

        with patch.object(join_candidates.gate, "dispatch", side_effect=spy):
            result = join_candidates.join(self.root, self.record())
        self.assertTrue(calls, "The join must call the existing gate")
        self.assertEqual("check", calls[0]["op"])
        self.assertEqual(str(self.root), calls[0]["root"])
        expected = real({"op": "check", "root": str(self.root)})["fingerprint"]
        self.assertEqual(expected, result["fingerprint"])

    def test_stale_review_blocks_and_leaves_no_receipt(self):
        review = self.review()
        with (self.root / "design.md").open("a") as out:
            out.write("\nA design change after review.\n")
        result = join_candidates.join(self.root, self.record(), op="accept", review=review)
        self.assert_blocked(result, "stale")

    def test_unapproved_review_blocks(self):
        review = self.review()
        review.update(verdict="revise", blocking_findings=["Weak domain model"])
        result = join_candidates.join(self.root, self.record(), op="accept", review=review)
        self.assert_blocked(result, "did not approve")

    def test_join_accepts_only_with_a_fresh_review(self):
        result = join_candidates.join(self.root, self.record(), op="accept", review=self.review())
        self.assertEqual("accepted", result["status"])
        receipt = json.loads((self.root / "accepted.json").read_text())
        self.assertEqual(result["fingerprint"], receipt["fingerprint"])
        self.assertEqual("accepted", receipt["status"])

    def test_omitted_obligation_blocks(self):
        self.merged.write_text(self.sdl.replace("type Task {", "type Task {\n  ebikes: Int\n", 1))
        record = self.record(merged_sha256=[join_candidates.sha256_file(self.merged)] * 2)
        self.assert_blocked(join_candidates.join(self.root, record), "obligation")

    def test_covered_obligations_do_not_block(self):
        result = join_candidates.join(self.root, self.record())
        self.assertEqual("checked", result["status"])
        self.assertEqual([], result["obligations"]["omitted"])
        self.assertTrue(result["obligations"]["required"] > 0)

    def test_source_identity_mismatch_blocks(self):
        record = self.record()
        self.merged.write_text(self.sdl + "\n# tampered after the merge\n")
        self.assert_blocked(join_candidates.join(self.root, record), "source identity")

    def test_divergent_merge_results_block(self):
        other = self.source / "merged_other.graphql"
        other.write_text(self.sdl + "\n# divergent lane output\n")
        record = self.record(merged=[str(self.merged), str(other)],
                             merged_sha256=[join_candidates.sha256_file(self.merged),
                                            join_candidates.sha256_file(other)])
        self.assert_blocked(join_candidates.join(self.root, record), "diverg")

    def test_self_merge_is_not_independent_convergence(self):
        record = self.record(provenance=[{"lane": 0, "run_id": "fixture-lane0",
                                          "state": "verified"}] * 2,
                             convergence={"independent": True, "label": "claimed independent"})
        self.assert_blocked(join_candidates.join(self.root, record), "self-merge")

    def test_single_proposer_is_not_called_independent_convergence(self):
        record = self.record(provenance=[{"lane": 0, "run_id": "fixture-lane0",
                                          "state": "verified"}])
        result = join_candidates.join(self.root, record)
        self.assertEqual("checked", result["status"])
        self.assertFalse(result["source_identity"]["independent_convergence"])
        self.assertEqual(["fixture-lane0"], result["source_identity"]["run_ids"])

    def test_missing_run_id_blocks(self):
        self.assert_blocked(join_candidates.join(self.root, self.record(provenance=[])),
                            "run id")

    def test_exhausted_repair_blocks_terminally(self):
        self.change_contract(lambda c: c["fields"]["Task.id"].pop("authorization"))
        result = join_candidates.join(self.root, self.record(), repair_attempts=2)
        self.assert_blocked(result, "Repair passes exhausted")
        self.assertEqual("terminal", result["route"])
        self.assertEqual("exhausted", result["repair"]["state"])

    def test_repair_passes_within_limit_leave_the_route_open(self):
        self.change_contract(lambda c: c["fields"]["Task.id"].pop("authorization"))
        result = join_candidates.join(self.root, self.record(), repair_attempts=1)
        self.assert_blocked(result, "Task.id needs authorization")
        self.assertEqual("revise", result["route"])
        self.assertEqual("available", result["repair"]["state"])

    def test_repairs_that_succeeded_are_still_acceptable(self):
        result = join_candidates.join(self.root, self.record(), op="accept",
                                      review=self.review(), repair_attempts=2)
        self.assertEqual("accepted", result["status"])
        self.assertEqual("exhausted", result["repair"]["state"])

    def test_record_carries_checker_guide_source_and_review_evidence(self):
        review = self.review()
        result = join_candidates.join(self.root, self.record(), op="accept", review=review)
        self.assertEqual(gate.VERSION, result["checker"]["version"])
        self.assertEqual(join_candidates.sha256_file(HERE / "scripts/gate.py"),
                         result["checker"]["sha256"])
        self.assertEqual(join_candidates.sha256_file(HERE / "README.md"),
                         result["guide"]["sha256"])
        self.assertEqual(join_candidates.sha256_file(HERE / "tools/graphql_contract_guide.rhai"),
                         result["guide"]["tool_sha256"])
        identity = result["source_identity"]
        self.assertEqual(join_candidates.sha256_file(self.merged), identity["result"]["sha256"])
        self.assertEqual(join_candidates.sha256_file(self.a),
                         identity["candidates"]["a"]["sha256"])
        self.assertTrue(identity["determinism"])
        evidence = result["contract"]["review_evidence"]
        self.assertEqual("approve", evidence["verdict"])
        self.assertEqual(sorted(gate.RUBRIC), evidence["rubric"])
        self.assertEqual(join_candidates.sha256_of(review), evidence["sha256"])
        written = json.loads((self.base / "graphql-join" / "join-record.json").read_text())
        self.assertEqual(result["fingerprint"], written["fingerprint"])

    def test_runtime_and_product_judgment_stay_separate_from_acceptance(self):
        result = join_candidates.join(self.root, self.record(), op="accept", review=self.review())
        self.assertTrue(result["runtime_verification"]["separate_from_acceptance"])
        self.assertEqual("unverified", result["runtime_verification"]["state"])
        self.assertTrue(result["product_judgment"]["separate_from_acceptance"])
        self.assertEqual("not_made", result["product_judgment"]["state"])
        receipt = (self.root / "accepted.json").read_text().lower()
        self.assertNotIn("runtime_verified", receipt)
        self.assertNotIn("product_judgment", receipt)

    def test_hook_keeps_two_repair_passes_then_a_terminal_route(self):
        manifest = tomllib.loads((HERE / "agent.leviath").read_text())
        self.assertIn("repairs", manifest["context"]["regions"])
        self.assertEqual(2, manifest["stages"]["revise"]["max_revisits"])
        hook = (HERE / "hooks/protocol.rhai").read_text()
        self.assertIn("repairs", hook)
        self.assertIn('route = "blocked"', hook)
        self.assertEqual(2, join_candidates.MAX_REPAIR_PASSES)

    def test_existing_regressions_remain_intact(self):
        source = (HERE / "tests/test_gate.py").read_text()
        self.assertEqual(24, source.count("    def test_"))


if __name__ == "__main__":
    unittest.main()