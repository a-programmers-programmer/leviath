"""RED/GREEN tests for the GQL-28 contract-readiness gate.

The gate is deterministic code. It owns the bits: which source paths must
exist, whether tRPC markers are present, whether a contract artifact was
observed. It does not judge the contract. A model judges the contract.

Every test below builds a real temporary tree and runs the gate as a
subprocess, so the exit code and the JSON are the real ones.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GATE = HERE / "check_contract_ready.py"

SPEC = {
    "id": "GQL-28",
    "repository": "a-programmers-programmer/parlour",
    "existing_paths": [
        "AGENTS.md",
        "wiki",
        "server/routers.ts",
        "server/productRouter.ts",
        "server/eventRuntime.ts",
        "drizzle/schema.ts",
    ],
}


def run(root: Path, spec: Path):
    proc = subprocess.run(
        [sys.executable, str(GATE), "--root", str(root), "--spec", str(spec), "--json"],
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout) if proc.stdout.strip() else None
    return proc.returncode, payload, proc.stderr


def write_spec(tmp_path: Path) -> Path:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(SPEC), encoding="utf-8")
    return spec


def plant_checkout(root: Path, *, with_trpc: bool = True, with_contract: bool = True):
    """Plant a fake parlour checkout under root/parlour."""
    repo = root / "parlour"
    (repo / "server").mkdir(parents=True)
    (repo / "drizzle").mkdir(parents=True)
    (repo / "wiki").mkdir(parents=True)
    (repo / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    (repo / "wiki" / "INDEX.md").write_text("wiki\n", encoding="utf-8")
    marker = "createTRPCRouter" if with_trpc else "nothing_here"
    for name in ("routers.ts", "productRouter.ts", "eventRuntime.ts"):
        (repo / "server" / name).write_text(f"// {marker}\n", encoding="utf-8")
    (repo / "drizzle" / "schema.ts").write_text("// drizzle\n", encoding="utf-8")
    if with_contract:
        (repo / "contract" / "parlour.graphql").parent.mkdir(parents=True, exist_ok=True)
        (repo / "contract" / "parlour.graphql").write_text(
            "type Query { ping: String }\n", encoding="utf-8"
        )
    return repo


# --- RED: today's real shape, nothing on disk -> blocked -------------------


def test_empty_root_is_blocked_with_exit_2(tmp_path):
    spec = write_spec(tmp_path)
    code, payload, _ = run(tmp_path, spec)
    assert code == 2, "an empty root must be BLOCKED, not ready"
    assert payload["node"] == "GQL-28"
    assert payload["state"] == "blocked"
    names = {c["name"] for c in payload["checks"]}
    assert "checkout" in names
    assert payload["checks"][0]["state"] == "missing"


def test_no_checkout_reports_every_path_missing(tmp_path):
    spec = write_spec(tmp_path)
    _, payload, _ = run(tmp_path, spec)
    by_name = {c["name"]: c for c in payload["checks"]}
    for rel in SPEC["existing_paths"]:
        key = f"existing_path:{rel}"
        assert key in by_name, f"{key} must be checked"
        assert by_name[key]["state"] == "missing"


def test_missing_checkout_never_claims_a_contract(tmp_path):
    spec = write_spec(tmp_path)
    _, payload, _ = run(tmp_path, spec)
    by_name = {c["name"]: c for c in payload["checks"]}
    assert by_name["contract_artifact"]["state"] == "missing"
    assert payload["contract_accepted"] is False


# --- GREEN: a complete checkout with tRPC + a contract -> ready -----------


def test_complete_checkout_is_ready_with_exit_0(tmp_path):
    plant_checkout(tmp_path)
    spec = write_spec(tmp_path)
    code, payload, _ = run(tmp_path, spec)
    assert code == 0, payload
    assert payload["state"] == "ready"
    assert payload["contract_accepted"] is True
    assert all(c["state"] == "present" for c in payload["checks"])


def test_ready_requires_a_real_contract_file(tmp_path):
    plant_checkout(tmp_path, with_contract=False)
    spec = write_spec(tmp_path)
    code, payload, _ = run(tmp_path, spec)
    assert code == 2
    by_name = {c["name"]: c for c in payload["checks"]}
    assert by_name["contract_artifact"]["state"] == "missing"


def test_ready_requires_trpc_markers(tmp_path):
    plant_checkout(tmp_path, with_trpc=False)
    spec = write_spec(tmp_path)
    code, payload, _ = run(tmp_path, spec)
    assert code == 2
    by_name = {c["name"]: c for c in payload["checks"]}
    assert by_name["trpc_markers"]["state"] == "missing"


def test_ready_requires_every_existing_path(tmp_path):
    repo = plant_checkout(tmp_path)
    (repo / "server" / "eventRuntime.ts").unlink()
    spec = write_spec(tmp_path)
    code, payload, _ = run(tmp_path, spec)
    assert code == 2
    by_name = {c["name"]: c for c in payload["checks"]}
    assert by_name["existing_path:server/eventRuntime.ts"]["state"] == "missing"


# --- the gate must be honest about what it saw ----------------------------


def test_gate_records_the_root_it_scanned(tmp_path):
    plant_checkout(tmp_path)
    spec = write_spec(tmp_path)
    _, payload, _ = run(tmp_path, spec)
    assert payload["root"] == str(tmp_path)
    assert payload["checkout"] == str(tmp_path / "parlour")


def test_gate_reports_hashes_of_the_contract_it_observed(tmp_path):
    plant_checkout(tmp_path)
    spec = write_spec(tmp_path)
    _, payload, _ = run(tmp_path, spec)
    art = payload["contract_artifacts"]
    assert len(art) == 1
    assert art[0]["path"].endswith("contract/parlour.graphql")
    assert len(art[0]["sha256"]) == 64


def test_no_contract_artifact_list_when_none_observed(tmp_path):
    spec = write_spec(tmp_path)
    _, payload, _ = run(tmp_path, spec)
    assert payload["contract_artifacts"] == []


def test_bad_spec_path_is_an_error_exit_1(tmp_path):
    code, payload, err = run(tmp_path, tmp_path / "nope.json")
    assert code == 1
    assert "spec" in err.lower()
