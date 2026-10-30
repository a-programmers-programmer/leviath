"""Pinned acceptance tests for GQL-08: the simple code generation hook.

Every test asserts a **concrete expected value**, not a shape.  The seven
definition-of-done clauses of GQL-08 are:

    D1  one deterministic command: reconciled contract snapshot + output dir
    D2  emits typed interfaces/stubs, operation clients, scalar mappings,
        source maps
    D3  a second invocation produces no diff (idempotent)
    D4  a stale contract identity is rejected (exit 1 on drift)
    D5  handwritten resolvers survive regeneration
    D6  the Python and TypeScript outputs compile/typecheck
    D7  generated SDL/operations agree with the accepted contract

The contract under test is the **reconciled snapshot**, not the repository copy:
``repo/docs/workflow/schema-workflow.graphql`` is still the pre-GQL-01 base SDL
and does not even parse.  The snapshot is read from ``LEVIATH_CONTRACT_DIR``
when set, else from ``<workdir>/contract``.

Run with pytest, or directly:

    PYTHONPATH=python python3 python/leviath_codegen/test_codegen.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List

import pytest

from leviath_codegen import generate as generate_mod
from leviath_codegen import verify as verify_mod

#: The accepted fingerprint, spelled out so drift is visible in this file.
EXPECTED_PINNED = "ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559"

#: The reconciled contract snapshot: the command's input.
CONTRACT_DIR = Path(
    os.environ.get(
        "LEVIATH_CONTRACT_DIR",
        Path(__file__).resolve().parents[3] / "contract",
    )
)

#: Deterministic operation set generated from the accepted contract roots.
EXPECTED_OPERATIONS = (
    "SearchWorkflows",
    "Workflows",
    "Workflow",
    "WorkflowByName",
    "WorkflowRun",
    "EdgeInvariantRules",
    "DispatchWorkflow",
    "RunWorkflowStep",
    "RespondToGate",
)

#: Every path the generator owns, relative to the output directory.  Sorted, so
#: it compares directly with ``Report.unchanged`` / ``Report.written``.
EXPECTED_PATHS = (
    "contract/operations.graphql",
    "contract/schema.graphql",
    "handwritten/README.md",
    "handwritten/python/leviath_handwritten/resolvers.py",
    "handwritten/typescript/resolvers.ts",
    "manifest.json",
    "python/leviath_generated/__init__.py",
    "python/leviath_generated/interfaces.py",
    "python/leviath_generated/operations.py",
    "python/leviath_generated/py.typed",
    "python/leviath_generated/scalars.py",
    "sourcemap.json",
    "typescript/src/index.ts",
    "typescript/src/interfaces.ts",
    "typescript/src/operations.ts",
    "typescript/src/scalars.ts",
    "typescript/tsconfig.json",
)


def _contract() -> Path:
    if not (CONTRACT_DIR / "schema-workflow.graphql").is_file():
        pytest.fail(f"reconciled contract snapshot not found at {CONTRACT_DIR}")
    return CONTRACT_DIR


def _generate(out: Path, contract: Path | None = None, **kwargs) -> generate_mod.Report:
    return generate_mod.generate(contract or _contract(), out, **kwargs)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# D1/D2 — one deterministic command, the emitted surface
# ---------------------------------------------------------------------------


def test_generate_emits_the_expected_surface(tmp_path: Path):
    report = _generate(tmp_path / "out")
    out = tmp_path / "out"
    assert report.result == "PASS"
    assert report.contract_fingerprint == EXPECTED_PINNED
    for relative in EXPECTED_PATHS:
        assert (out / relative).is_file(), f"missing generated file {relative}"
    assert [op for op in report.operations] == list(EXPECTED_OPERATIONS)


def _all_keys(payload) -> List[str]:
    """Every key anywhere in a JSON payload, so a wall-clock field is visible."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from _all_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _all_keys(item)


def test_manifest_pins_the_contract_and_has_no_timestamp(tmp_path: Path):
    report = _generate(tmp_path / "out")
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["contract_fingerprint"] == EXPECTED_PINNED
    assert manifest["format"] == "leviath-codegen-manifest/v1"
    assert manifest["operations"] == list(EXPECTED_OPERATIONS)
    # No wall-clock or random field: a timestamp would break idempotency.
    keys = {key.lower() for key in _all_keys(manifest)}
    assert not any(
        "timestamp" in key or "generated_at" in key or key in ("date", "time")
        for key in keys
    )
    assert manifest["files"] == report.files


def test_scalar_mappings_cover_every_contract_scalar(tmp_path: Path):
    _generate(tmp_path / "out")
    payload = json.loads((tmp_path / "out" / "sourcemap.json").read_text(encoding="utf-8"))
    scalars = payload["scalars"]
    assert scalars["DateTime"]["python"] == "DateTime"
    assert scalars["Confidential"]["python"] == "Confidential"
    assert scalars["String"]["python"] == "str"
    assert scalars["Int"]["python"] == "int"
    assert scalars["Boolean"]["python"] == "bool"
    assert scalars["ID"]["typescript"] == "ID"
    assert set(scalars) >= {
        "DateTime",
        "Duration",
        "Decimal",
        "URI",
        "Confidential",
        "PortValue",
        "String",
        "Int",
        "Float",
        "Boolean",
        "ID",
    }


def test_generated_python_names_the_contract_types(tmp_path: Path):
    _generate(tmp_path / "out")
    interfaces = (tmp_path / "out" / "python/leviath_generated/interfaces.py").read_text(
        encoding="utf-8"
    )
    assert "WorkflowStepInput" in interfaces
    assert "OAuthRefreshTokenInput" in interfaces
    assert "InvariantDisposition" in interfaces
    assert "class EdgeKind(enum.Enum)" in interfaces
    operations = (tmp_path / "out" / "python/leviath_generated/operations.py").read_text(
        encoding="utf-8"
    )
    assert "class WorkflowClient" in operations
    for name in EXPECTED_OPERATIONS:
        assert name in operations


def test_generated_typescript_names_the_contract_types(tmp_path: Path):
    _generate(tmp_path / "out")
    interfaces = (tmp_path / "out" / "typescript/src/interfaces.ts").read_text(encoding="utf-8")
    assert "export interface WorkflowStepInput" in interfaces
    assert "export type InvariantDisposition =" in interfaces
    operations = (tmp_path / "out" / "typescript/src/operations.ts").read_text(encoding="utf-8")
    assert "export class WorkflowClient" in operations


def test_sourcemap_points_each_generated_symbol_at_its_contract_line(tmp_path: Path):
    _generate(tmp_path / "out")
    payload = json.loads((tmp_path / "out" / "sourcemap.json").read_text(encoding="utf-8"))
    entries = {row["symbol"]: row for row in payload["entries"]}
    assert "WorkflowStep" in entries
    row = entries["WorkflowStep"]
    assert row["contract_path"].endswith("schema-workflow.graphql")
    assert row["contract_line"] >= 1
    assert row["generated_file"] and row["generated_line"] >= 1
    # The documented line really is where the SDL declares the type.
    text = (_contract() / "schema-workflow.graphql").read_text(encoding="utf-8")
    line = text.splitlines()[row["contract_line"] - 1]
    assert "WorkflowStep" in line


# ---------------------------------------------------------------------------
# D3 — a second invocation produces no diff
# ---------------------------------------------------------------------------


def test_second_invocation_writes_nothing_and_keeps_every_byte(tmp_path: Path):
    out = tmp_path / "out"
    first = _generate(out)
    before = {relative: _digest(out / relative) for relative in EXPECTED_PATHS}
    manifest_before = (out / "manifest.json").read_bytes()
    second = _generate(out)
    assert second.result == "PASS"
    assert second.written == ()
    assert second.unchanged == tuple(EXPECTED_PATHS)
    after = {relative: _digest(out / relative) for relative in EXPECTED_PATHS}
    assert before == after
    assert (out / "manifest.json").read_bytes() == manifest_before
    assert first.files == second.files


def test_two_fresh_outputs_are_byte_identical(tmp_path: Path):
    left, right = tmp_path / "left", tmp_path / "right"
    left_report = _generate(left)
    right_report = _generate(right)
    assert left_report.files == right_report.files
    for relative in EXPECTED_PATHS:
        assert (left / relative).read_bytes() == (right / relative).read_bytes(), relative


# ---------------------------------------------------------------------------
# D4 — a stale contract identity is rejected
# ---------------------------------------------------------------------------


def test_tampered_contract_is_rejected(tmp_path: Path):
    contract = tmp_path / "contract"
    shutil.copytree(_contract(), contract)
    sdl = contract / "schema-workflow.graphql"
    sdl.write_text(
        sdl.read_text(encoding="utf-8") + "\ntype InjectedDrift { x: Int }\n", encoding="utf-8"
    )
    with pytest.raises(verify_mod.ContractDrift):
        _generate(tmp_path / "out", contract)


def test_unrelated_contract_directory_is_rejected(tmp_path: Path):
    contract = tmp_path / "contract"
    contract.mkdir()
    (contract / "schema-workflow.graphql").write_text("type Order { id: ID! }\n", encoding="utf-8")
    with pytest.raises(verify_mod.ContractDrift):
        _generate(tmp_path / "out", contract)


def test_wrong_expected_fingerprint_is_rejected(tmp_path: Path):
    with pytest.raises(verify_mod.ContractDrift):
        _generate(tmp_path / "out", expected_fingerprint="0" * 64)


def test_rejected_run_leaves_no_output(tmp_path: Path):
    contract = tmp_path / "contract"
    shutil.copytree(_contract(), contract)
    (contract / "schema-workflow.graphql").write_text("type Order { id: ID! }\n", encoding="utf-8")
    out = tmp_path / "out"
    with pytest.raises(verify_mod.ContractDrift):
        _generate(out, contract)
    assert not (out / "manifest.json").exists()
    assert not (out / "python").exists()


def test_cli_exits_one_on_drift_and_zero_on_success(tmp_path: Path):
    from leviath_codegen.cli import main

    ok = main(["generate", "--contract", str(_contract()), "--out", str(tmp_path / "out")])
    assert ok == 0
    contract = tmp_path / "contract"
    shutil.copytree(_contract(), contract)
    (contract / "schema-workflow.graphql").write_text("type Order { id: ID! }\n", encoding="utf-8")
    bad = main(["generate", "--contract", str(contract), "--out", str(tmp_path / "out2")])
    assert bad == 1
    assert not (tmp_path / "out2").exists()


# ---------------------------------------------------------------------------
# D5 — handwritten resolvers survive regeneration
# ---------------------------------------------------------------------------


def test_handwritten_resolvers_survive_regeneration(tmp_path: Path):
    out = tmp_path / "out"
    _generate(out)
    resolver = out / "handwritten/python/leviath_handwritten/resolvers.py"
    ts_resolver = out / "handwritten/typescript/resolvers.ts"
    marker = "\n# handwritten edit kept across regeneration\nHANDWRITTEN_MARKER = 'kept'\n"
    resolver.write_text(resolver.read_text(encoding="utf-8") + marker, encoding="utf-8")
    ts_resolver.write_text(
        ts_resolver.read_text(encoding="utf-8") + "\n// handwritten ts edit\nexport const TS_MARKER = 1;\n",
        encoding="utf-8",
    )
    before = _digest(resolver)
    ts_before = _digest(ts_resolver)

    report = _generate(out)

    assert report.result == "PASS"
    assert _digest(resolver) == before
    assert _digest(ts_resolver) == ts_before
    assert "HANDWRITTEN_MARKER" in resolver.read_text(encoding="utf-8")
    assert "TS_MARKER" in ts_resolver.read_text(encoding="utf-8")
    # The edited handwritten files are not claimed as generator output.
    assert "handwritten/python/leviath_handwritten/resolvers.py" in report.handwritten_kept


def test_handwritten_seeded_once_with_baseline_text(tmp_path: Path):
    out = tmp_path / "out"
    first = _generate(out)
    resolver = out / "handwritten/python/leviath_handwritten/resolvers.py"
    assert "handwritten/python/leviath_handwritten/resolvers.py" in first.handwritten_created
    assert "def resolve_workflow" in resolver.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# D6 — Python and TypeScript outputs compile/typecheck
# ---------------------------------------------------------------------------


def test_generated_python_compiles_and_typechecks(tmp_path: Path):
    out = tmp_path / "out"
    _generate(out)
    result = verify_mod.check_python(out)
    assert result["result"] == "PASS", result
    assert result["compileall"]["exit"] == 0
    assert result["mypy"]["exit"] == 0


def test_generated_typescript_typechecks(tmp_path: Path):
    out = tmp_path / "out"
    _generate(out)
    result = verify_mod.check_typescript(out)
    assert result["result"] == "PASS", result
    assert result["tsc"]["exit"] == 0


def test_verify_command_passes_on_a_fresh_generation(tmp_path: Path):
    out = tmp_path / "out"
    _generate(out)
    payload = verify_mod.verify_outputs(out)
    assert payload["result"] == "PASS", payload
    assert payload["agreement"]["result"] == "PASS"
    assert payload["python"]["result"] == "PASS"
    assert payload["typescript"]["result"] == "PASS"
    assert payload["contract_fingerprint"] == EXPECTED_PINNED


# ---------------------------------------------------------------------------
# D7 — generated SDL/operations agree with the accepted contract
# ---------------------------------------------------------------------------


def test_generated_sdl_is_the_accepted_contract(tmp_path: Path):
    from graphql import build_schema, print_schema

    out = tmp_path / "out"
    _generate(out)
    generated = (out / "contract/schema.graphql").read_text(encoding="utf-8")
    accepted = (_contract() / "schema-workflow.graphql").read_text(encoding="utf-8")
    assert hashlib.sha256(generated.encode("utf-8")).hexdigest() == EXPECTED_PINNED
    assert print_schema(build_schema(generated)) == print_schema(build_schema(accepted))


def test_generated_operations_validate_against_the_generated_sdl(tmp_path: Path):
    from graphql import build_schema, parse, validate

    out = tmp_path / "out"
    _generate(out)
    schema = build_schema((out / "contract/schema.graphql").read_text(encoding="utf-8"))
    document = parse((out / "contract/operations.graphql").read_text(encoding="utf-8"))
    errors = validate(schema, document)
    assert errors == [], [str(error) for error in errors]


def test_generated_operations_cover_every_root_field(tmp_path: Path):
    from graphql import build_schema, parse

    out = tmp_path / "out"
    _generate(out)
    schema = build_schema((out / "contract/schema.graphql").read_text(encoding="utf-8"))
    document = parse((out / "contract/operations.graphql").read_text(encoding="utf-8"))
    covered = {
        node.name.value
        for definition in document.definitions
        for selection in definition.selection_set.selections  # type: ignore[union-attr]
        for node in [selection]
        if hasattr(node, "name")
    }
    roots = set(schema.query_type.fields) | set(schema.mutation_type.fields)
    assert roots <= covered


def test_operations_file_declares_the_port_value_envelope(tmp_path: Path):
    out = tmp_path / "out"
    _generate(out)
    text = (out / "contract/operations.graphql").read_text(encoding="utf-8")
    assert "input: $input" in text
    assert "PortValue!" in text


# ---------------------------------------------------------------------------
# Plain-runner support (no pytest needed)
# ---------------------------------------------------------------------------


def _run_plain() -> int:
    import inspect

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
    print(f"codegen_suite: PASS — {ran} tests, contract {EXPECTED_PINNED[:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_plain())