#!/usr/bin/env python3
"""Focused black-box tests for the Oracle workspace kernel."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "workspace.py"


class WorkspaceKernelTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        self.run_git("init", "-q")
        self.run_git("config", "user.email", "oracle@example.test")
        self.run_git("config", "user.name", "Oracle Test")
        (self.repo / "a.txt").write_text("alpha\n")
        self.run_git("add", "a.txt")
        self.run_git("commit", "-qm", "initial")
        initial = self.call({"op": "bootstrap", "task": "change a.txt safely"})
        self.assertTrue(initial["ok"], initial)
        self.run_id = initial["run_id"]
        self.snapshot_id = initial["dossier"]["snapshot_id"]

    def tearDown(self):
        self.temporary.cleanup()

    def run_git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def call(self, request, ok=True):
        proc = subprocess.run([sys.executable, str(SCRIPT), json.dumps(request)], cwd=self.repo,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.stderr, "")
        response = json.loads(proc.stdout)
        self.assertEqual(response.get("ok"), ok, response)
        return response

    def authorize(self, checks=None, dependencies=None):
        return self.call({"op": "control", "run_id": self.run_id,
                          "decision": {"action": "AUTHORIZE", "snapshot_id": self.snapshot_id, "orders": [{
                              "id": "code-1", "objective": "replace file contents",
                              "paths": ["a.txt"], "depends_on": dependencies or [],
                              "checks": checks if checks is not None else [{"id": "smoke", "argv": [sys.executable, "-c", "print(1)"], "cwd": ".", "expect_tests": False}] }]}})

    def patch_and_seal(self):
        before = hashlib.sha256((self.repo / "a.txt").read_bytes()).hexdigest()
        self.call({"op": "worker", "run_id": self.run_id, "order_id": "code-1",
                   "action": "patch", "path": "a.txt",
                   "before_sha256": before, "content": "beta\n"})
        self.call({"op": "worker", "run_id": self.run_id, "order_id": "code-1",
                   "action": "seal", "summary": "updated a.txt",
                   "negative": [], "contradictions": [], "unread": []})

    def test_patch_uses_compare_and_swap(self):
        self.authorize()
        inspected = self.call({"op": "worker", "run_id": self.run_id, "order_id": "code-1",
                               "action": "inspect", "kind": "file", "path": "a.txt"})
        self.assertEqual(inspected["result"]["content"], "alpha\n")
        wrong = "0" * 64
        response = self.call({"op": "worker", "run_id": self.run_id, "order_id": "code-1",
                              "action": "patch", "path": "a.txt",
                              "before_sha256": wrong, "content": "beta\n"}, ok=False)
        self.assertIn("CAS mismatch", response["error"])
        self.assertEqual((self.repo / "a.txt").read_text(), "alpha\n")

    def test_accept_rejects_stale_snapshot(self):
        self.authorize()
        (self.repo / "a.txt").write_text("outside change\n")
        response = self.call({"op": "control", "run_id": self.run_id,
                              "decision": {"action": "ACCEPT", "snapshot_id": "0" * 64}}, ok=False)
        self.assertIn("stale", response["error"])

    def test_negative_evidence_survives_collect(self):
        self.call({"op": "worker", "run_id": self.run_id, "order_id": "read-001",
                   "action": "inspect", "kind": "inventory"})
        self.call({"op": "worker", "run_id": self.run_id, "order_id": "read-001",
                   "action": "seal", "summary": "inspected repository",
                   "negative": ["no migration file"],
                   "contradictions": ["two naming conventions"],
                   "unread": ["generated output"]})
        response = self.call({"op": "collect", "run_id": self.run_id,
                              "runtime_report": "worker prose ignored"})
        self.assertIn("read-001: no migration file", response["dossier"]["negative_evidence"])
        self.assertIn("read-001: two naming conventions", response["dossier"]["contradictions"])
        self.assertIn("read-001: generated output", response["dossier"]["unread"])

    def test_expected_tests_with_zero_tests_is_incomplete(self):
        self.authorize(checks=[{"id": "tests", "argv": [sys.executable, "-c", "print('no tests')"],
                                "cwd": ".", "expect_tests": True}])
        self.patch_and_seal()
        dispatched = self.call({"op": "collect", "run_id": self.run_id})
        self.assertEqual(dispatched["route"], "verifiers")
        verifier = dispatched["items"][0]["id"]
        checked = self.call({"op": "worker", "run_id": self.run_id, "order_id": verifier,
                             "action": "check", "check_id": "tests"})
        self.assertTrue(checked["result"]["incomplete"])
        self.assertIsNone(checked["result"]["tests_collected"])
        self.call({"op": "worker", "run_id": self.run_id, "order_id": verifier,
                   "action": "seal", "summary": "zero tests collected",
                   "negative": ["test discovery found zero tests"],
                   "contradictions": [], "unread": []})
        collected = self.call({"op": "collect", "run_id": self.run_id})
        self.assertEqual(collected["route"], "oracle")
        code = next(item for item in collected["dossier"]["orders"] if item["id"] == "code-1")
        self.assertEqual(code["status"], "needs_remediation")

    def test_invalid_order_graph_is_rejected(self):
        response = self.call({"op": "control", "run_id": self.run_id,
                              "decision": {"action": "AUTHORIZE", "snapshot_id": self.snapshot_id, "orders": [
                                  {"id": "one", "objective": "one", "paths": ["a.txt"],
                                   "depends_on": ["two"], "checks": [{"id": "c1", "argv": ["true"], "cwd": ".", "expect_tests": False}]},
                                  {"id": "two", "objective": "two", "paths": ["a.txt"],
                                   "depends_on": ["one"], "checks": [{"id": "c2", "argv": ["true"], "cwd": ".", "expect_tests": False}]}]}}, ok=False)
        self.assertIn("cycle", response["error"])

    def test_repository_wide_evidence_order_accepts_dot_path(self):
        response = self.call({"op": "control", "run_id": self.run_id,
                              "decision": {"action": "REQUEST_EVIDENCE",
                                           "snapshot_id": self.snapshot_id,
                                           "orders": [{"id": "read-all",
                                                       "objective": "inventory repository",
                                                       "paths": ["."]}]}})
        self.assertEqual(response["route"], "readers")
        self.assertEqual(response["items"][0]["id"], "read-all")


if __name__ == "__main__":
    unittest.main()
