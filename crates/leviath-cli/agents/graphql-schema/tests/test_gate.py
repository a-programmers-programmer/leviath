"""Focused acceptance regressions; no providers or live application required."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import gate
import generate_tool
import run_stage


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "contract"
        shutil.copytree(HERE / "examples/task-board", self.root)

    def request(self, op="check", **kwargs):
        return gate.dispatch({"op": op, "root": str(self.root), **kwargs})

    def change_contract(self, update):
        path = self.root / "contract.json"
        data = json.loads(path.read_text())
        update(data)
        path.write_text(json.dumps(data))

    def review(self):
        return {"fingerprint": self.request()["fingerprint"], "verdict": "approve",
                "blocking_findings": [], "rubric": {key: {"pass": True,
                "evidence": "Synthetic test approval, not a real review."} for key in gate.RUBRIC}}

    def assert_blocked(self, text):
        result = self.request()
        self.assertEqual("blocked", result["status"])
        self.assertIn(text, " ".join(result["errors"]))

    def test_example_acceptance_and_independent_verification(self):
        result = self.request("accept", review=self.review())
        self.assertEqual("accepted", result["status"])
        self.assertEqual(result["fingerprint"], self.request("verify")["fingerprint"])

    def test_parent_pin_rejects_a_different_accepted_contract(self):
        self.request("accept", review=self.review())
        with self.assertRaisesRegex(ValueError, "parent pipeline"):
            self.request("verify", expected_fingerprint="wrong-parent-fingerprint")

    def test_stale_review_cannot_accept(self):
        review = self.review()
        with (self.root / "design.md").open("a") as out:
            out.write("\nA design change after review.\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            self.request("accept", review=review)
        self.assertFalse((self.root / "accepted.json").exists())

    def test_downstream_rejects_valid_but_changed_contract(self):
        self.request("accept", review=self.review())
        with (self.root / "handoff.md").open("a") as out:
            out.write("\nChanged implementation obligations.\n")
        with self.assertRaisesRegex(ValueError, "changed after acceptance"):
            self.request("verify")

    def test_failed_new_attempt_invalidates_old_receipt(self):
        self.request("accept", review=self.review())
        self.change_contract(lambda c: c.update(open_questions=["Which role may complete tasks?"]))
        self.assert_blocked("open_questions")
        self.assertFalse((self.root / "accepted.json").exists())

    def test_missing_authorization_blocks(self):
        self.change_contract(lambda c: c["fields"]["Task.id"].pop("authorization"))
        self.assert_blocked("Task.id needs authorization")

    def test_unbounded_collection_blocks(self):
        self.change_contract(lambda c: c["collections"].pop("Query.tasks"))
        self.assert_blocked("max_items")

    def test_invalid_operation_blocks(self):
        path = self.root / "operations/tasks.graphql"
        path.write_text(path.read_text().replace("id title status revision", "id unknown status revision"))
        self.assert_blocked("unknown")

    def test_variables_must_coerce(self):
        path = self.root / "variables.json"
        data = json.loads(path.read_text())
        data[0]["variables"]["first"] = "bad"
        path.write_text(json.dumps(data))
        self.assert_blocked("Unexpected variable coercion")

    def test_requirement_must_select_its_claimed_coordinate(self):
        self.change_contract(lambda c: c["requirements"][0].update(coordinates=["Mutation.completeTask"]))
        self.assert_blocked("do not select required coordinates")

    def test_fragment_closure_cannot_borrow_another_operations_fields(self):
        path = self.root / "operations/tasks.graphql"
        path.write_text(path.read_text().replace("task { ...TaskSummary }", "task { id title }"))
        self.change_contract(lambda c: c["requirements"][2]["coordinates"].append("Task.revision"))
        self.assert_blocked("do not select required coordinates")

    def test_breaking_change_blocks_even_when_current_clients_are_valid(self):
        sdl = (self.root / "schema.graphql").read_text()
        (self.root / "baseline.graphql").write_text(sdl.replace('type Task {', 'type Task {\n  "Legacy field" legacy: String'))
        self.change_contract(lambda c: c.update(mode="evolve"))
        self.assert_blocked("FIELD_REMOVED")

    def test_dangerous_enum_addition_blocks(self):
        sdl = (self.root / "schema.graphql").read_text()
        (self.root / "baseline.graphql").write_text(sdl.replace('  """The task has been completed.""" COMPLETED\n', ''))
        self.change_contract(lambda c: c.update(mode="evolve"))
        self.assert_blocked("VALUE_ADDED_TO_ENUM")

    def test_baseline_cannot_be_present_in_new_mode(self):
        shutil.copyfile(self.root / "schema.graphql", self.root / "baseline.graphql")
        self.assert_blocked("use evolve mode")

    def test_no_synthetic_approval_without_all_dimensions(self):
        review = self.review()
        review["rubric"].pop("authorization")
        with self.assertRaisesRegex(ValueError, "authorization"):
            self.request("accept", review=review)

    def test_reviewer_rejection_blocks(self):
        review = self.review()
        review.update(verdict="revise", blocking_findings=["Weak domain model"])
        with self.assertRaisesRegex(ValueError, "did not approve"):
            self.request("accept", review=review)

    def test_symlink_artifact_rejected(self):
        (self.root / "alias.graphql").symlink_to(self.root / "schema.graphql")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.request()

    def test_nested_accepted_filename_is_still_hashed(self):
        path = self.root / "operations/accepted.json"
        path.write_text('{}')
        self.assertIn("operations/accepted.json", self.request()["files"])

    def test_cli_failure_is_nonzero_structured_json(self):
        result = subprocess.run([sys.executable, str(HERE / "scripts/gate.py"), "verify", "--root", str(self.root)], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("blocked", json.loads(result.stdout)["status"])

    def test_generated_checker_matches_source(self):
        self.assertEqual(generate_tool.render((HERE / "scripts/gate.py").read_bytes()),
                         (HERE / "tools/graphql_contract_gate.rhai").read_text())

    def test_embedded_tool_executes_and_returns_machine_json(self):
        import base64
        import re
        source = (HERE / "tools/graphql_contract_gate.rhai").read_text()
        loader = json.loads(re.search(r"let loader = (.*);", source).group(1))
        request = base64.b64encode(json.dumps({"op": "check", "root": str(self.root)}).encode()).decode()
        result = subprocess.run([sys.executable, "-c", loader, request], capture_output=True, text=True, check=True)
        self.assertEqual("checked", json.loads(result.stdout)["status"])

    def test_manifest_acceptance_and_review_tool_boundaries(self):
        manifest = tomllib.loads((HERE / "agent.leviath").read_text())
        stages = manifest["stages"]
        self.assertEqual("route", stages["accept"]["transition_region"])
        self.assertEqual(["graphql_contract_gate"], stages["accept"]["available_tools"])
        self.assertNotIn("bash", stages["review"]["available_tools"])
        self.assertNotIn("write_file", stages["review"]["available_tools"])
        for name, stage in stages.items():
            for target in stage.get("transitions", {}):
                self.assertIn(target, stages, (name, target))
            for hook in stage.get("hooks", {}).values():
                self.assertTrue((HERE / hook).exists())

    def test_runner_never_reuses_an_old_receipt_after_failed_run(self):
        self.request("accept", review=self.review())
        from contextlib import chdir
        with chdir(self.temp.name):
            self.root.rename(Path("graphql-contract"))
            with patch("run_stage.subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaisesRegex(ValueError, "Leviath run failed"):
                    run_stage.run("Test task")
            self.assertFalse(Path("graphql-contract/accepted.json").exists())

    def test_runner_blocks_zero_exit_without_accepted_artifact(self):
        from contextlib import chdir
        with chdir(self.temp.name):
            self.root.rename(Path("graphql-contract"))
            with patch("run_stage.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as launch:
                with self.assertRaisesRegex(ValueError, "No accepted contract"):
                    run_stage.run("Test task")
                self.assertIn("--wait", launch.call_args.args[0])
                self.assertNotIn("--yolo", launch.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
