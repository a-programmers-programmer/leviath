"""Verification: do the generated Python and TypeScript outputs hold up?

Three independent checks, each returning a serialisable result with the real exit
code of the real command:

``check_python``
    ``compileall`` the generated package, then ``mypy --strict`` it.
``check_typescript``
    ``tsc -p`` the generated project.
``check_agreement``
    Parse the generated operations and validate them against the generated SDL,
    which is the printed form of the accepted contract.

Nothing here calls a provider, touches the network or reads a secret.  A missing
toolchain is reported as ``SKIP`` with the reason, never as a pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from .model import ContractDrift

#: Where the TypeScript compiler lives when it is not on PATH.
TSC_CANDIDATES = (
    "/data/lsp/node_modules/typescript/bin/tsc",
    "node_modules/typescript/bin/tsc",
)


def _run(command: Sequence[str], cwd: Optional[Path] = None) -> Dict[str, object]:
    """Run a command and capture its real exit code and output."""
    completed = subprocess.run(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "command": " ".join(command),
        "exit": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _python() -> str:
    return sys.executable or "python3"


def check_python(output_dir: str | Path) -> Dict[str, object]:
    """Compile and typecheck the generated Python package."""
    out = Path(output_dir)
    package = out / "python" / "leviath_generated"
    if not package.is_dir():
        raise ContractDrift(f"no generated Python package at {package}")

    compileall = _run([_python(), "-m", "compileall", "-q", str(package)])
    mypy_command = [
        _python(),
        "-m",
        "mypy",
        "--strict",
        "--no-error-summary",
        "--no-incremental",
        "--cache-dir",
        str(out / ".mypy-cache"),
        str(package),
    ]
    mypy = _run(mypy_command, cwd=out / "python")
    ok = compileall["exit"] == 0 and mypy["exit"] == 0
    return {
        "result": "PASS" if ok else "FAIL",
        "compileall": compileall,
        "mypy": mypy,
    }


def _tsc() -> Optional[str]:
    found = shutil.which("tsc")
    if found:
        return found
    for candidate in TSC_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def check_typescript(output_dir: str | Path) -> Dict[str, object]:
    """Typecheck the generated TypeScript project."""
    out = Path(output_dir)
    project = out / "typescript"
    if not (project / "tsconfig.json").is_file():
        raise ContractDrift(f"no generated TypeScript project at {project}")
    tsc = _tsc()
    if tsc is None:
        return {
            "result": "SKIP",
            "reason": "no tsc on PATH and no vendored compiler found",
            "tsc": {"command": "tsc -p", "exit": None, "stdout": "", "stderr": ""},
        }
    command = [tsc, "-p", str(project)]
    if tsc.endswith(".js"):
        command = ["node", tsc, "-p", str(project)]
    tsc_result = _run(command)
    return {
        "result": "PASS" if tsc_result["exit"] == 0 else "FAIL",
        "tsc": tsc_result,
    }


def check_agreement(output_dir: str | Path) -> Dict[str, object]:
    """Validate the generated operations against the generated SDL."""
    from graphql import build_schema, parse, validate

    out = Path(output_dir)
    sdl_path = out / "contract" / "schema.graphql"
    operations_path = out / "contract" / "operations.graphql"
    for path in (sdl_path, operations_path):
        if not path.is_file():
            raise ContractDrift(f"no generated contract document at {path}")

    schema = build_schema(sdl_path.read_text(encoding="utf-8"))
    document = parse(operations_path.read_text(encoding="utf-8"))
    errors = [str(error) for error in validate(schema, document)]

    covered = set()
    for definition in document.definitions:
        selection_set = getattr(definition, "selection_set", None)
        for selection in getattr(selection_set, "selections", ()) or ():
            name = getattr(selection, "name", None)
            if name is not None:
                covered.add(name.value)
    roots = set()
    if schema.query_type is not None:
        roots |= set(schema.query_type.fields)
    if schema.mutation_type is not None:
        roots |= set(schema.mutation_type.fields)
    uncovered = sorted(roots - covered)
    if uncovered:
        errors.append(f"operations do not cover root fields: {uncovered}")

    return {
        "result": "PASS" if not errors else "FAIL",
        "errors": errors,
        "root_fields": sorted(roots),
        "covered_root_fields": sorted(covered),
    }


def verify_outputs(output_dir: str | Path) -> Dict[str, object]:
    """Run every check and return one serialisable payload."""
    from . import generate as generate_mod

    out = Path(output_dir)
    manifest_ok, problems = generate_mod.check_manifest(out)
    manifest = generate_mod.read_manifest(out)
    python_result = check_python(out)
    typescript_result = check_typescript(out)
    agreement = check_agreement(out)
    results = [python_result["result"], typescript_result["result"], agreement["result"]]
    if not manifest_ok:
        results.append("FAIL")
    ok = all(result == "PASS" for result in results)
    return {
        "result": "PASS" if ok else "FAIL",
        "contract_fingerprint": manifest.get("contract_fingerprint"),
        "pinned_fingerprint": manifest.get("pinned_fingerprint"),
        "manifest": {"result": "PASS" if manifest_ok else "FAIL", "problems": problems},
        "python": python_result,
        "typescript": typescript_result,
        "agreement": agreement,
    }