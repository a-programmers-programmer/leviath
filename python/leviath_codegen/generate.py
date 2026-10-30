"""The generator: one deterministic command over a contract snapshot.

:func:`generate` is the whole hook.  It reads the reconciled contract snapshot,
verifies the pin, renders every file, writes only what changed, seeds the
handwritten files once, and records the manifest and the source map.

Determinism is the design: the output is a pure function of the contract, so a
second invocation writes nothing and the manifest is byte-identical.  A drifted
contract is rejected **before** the output directory is created, so a failed run
leaves no half-written tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from . import render
from .model import Contract, ContractDrift, load_contract

MANIFEST_NAME = "manifest.json"
SOURCEMAP_NAME = "sourcemap.json"


@dataclass(frozen=True)
class Report:
    """What one invocation did: the pin, the operations and the file outcomes."""

    result: str
    contract_fingerprint: str
    pinned_fingerprint: str
    output_dir: str
    operations: Tuple[str, ...]
    files: Mapping[str, str] = field(default_factory=dict)          # generated path -> digest
    written: Tuple[str, ...] = ()
    unchanged: Tuple[str, ...] = ()
    handwritten_created: Tuple[str, ...] = ()
    handwritten_kept: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "result": self.result,
            "contract_fingerprint": self.contract_fingerprint,
            "pinned_fingerprint": self.pinned_fingerprint,
            "output_dir": self.output_dir,
            "operations": list(self.operations),
            "files": dict(self.files),
            "written": list(self.written),
            "unchanged": list(self.unchanged),
            "handwritten_created": list(self.handwritten_created),
            "handwritten_kept": list(self.handwritten_kept),
        }


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".codegen-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def generate(
    contract_dir: str | Path,
    output_dir: str | Path,
    *,
    sdl_path: Optional[str | Path] = None,
    expected_fingerprint: Optional[str] = None,
) -> Report:
    """Generate the typed outputs for a reconciled contract snapshot.

    Raises :class:`~leviath_codegen.model.ContractDrift` when the contract
    identity is stale, unknown or tampered.  The output directory is not created
    until the pin has passed, so a rejected run leaves no output behind.
    """
    contract: Contract = load_contract(
        contract_dir, sdl_path=sdl_path, expected_fingerprint=expected_fingerprint
    )

    out = Path(output_dir)
    generated = render.generated_files(contract)
    handwritten = render.handwritten_files(contract)

    # The source map is generated from the same contract, so it is part of the
    # deterministic set; the manifest is computed last, from every digest.
    sourcemap_text = render.source_map(contract, generated)
    sourcemap_path = SOURCEMAP_NAME

    digests: Dict[str, str] = {file.path: _digest(file.text) for file in generated}
    digests[sourcemap_path] = _digest(sourcemap_text)

    generated_paths = sorted(digests)
    manifest_text = render.manifest(contract, digests, generated_paths)

    written: List[str] = []
    unchanged: List[str] = []
    handwritten_created: List[str] = []
    handwritten_kept: List[str] = []

    for path, text in [(file.path, file.text) for file in generated] + [(sourcemap_path, sourcemap_text)]:
        target = out / path
        if _read(target) == text:
            unchanged.append(path)
            continue
        _write_atomic(target, text)
        written.append(path)

    # The manifest is written from the same comparison, so a second run is a no-op.
    manifest_target = out / MANIFEST_NAME
    if _read(manifest_target) == manifest_text:
        unchanged.append(MANIFEST_NAME)
    else:
        _write_atomic(manifest_target, manifest_text)
        written.append(MANIFEST_NAME)

    # Handwritten behaviour: created once, then owned by the developer.
    for file in handwritten:
        target = out / file.path
        if target.is_file():
            handwritten_kept.append(file.path)
            unchanged.append(file.path)
            continue
        _write_atomic(target, file.text)
        handwritten_created.append(file.path)
        written.append(file.path)

    return Report(
        result="PASS",
        contract_fingerprint=contract.fingerprint,
        pinned_fingerprint=contract.pinned_fingerprint,
        output_dir=str(out),
        operations=tuple(operation.name for operation in contract.operations),
        files=digests,
        written=tuple(sorted(written)),
        unchanged=tuple(sorted(unchanged)),
        handwritten_created=tuple(sorted(handwritten_created)),
        handwritten_kept=tuple(sorted(handwritten_kept)),
    )


def read_manifest(output_dir: str | Path) -> Mapping[str, object]:
    """Read the manifest a previous run wrote.  Raises when it is missing."""
    path = Path(output_dir) / MANIFEST_NAME
    if not path.is_file():
        raise ContractDrift(f"no manifest at {path}; run generate first")
    return json.loads(path.read_text(encoding="utf-8"))


def check_manifest(output_dir: str | Path) -> Tuple[bool, List[str]]:
    """Re-hash every generated file and compare it with the manifest.

    Returns ``(ok, problems)``.  A file the manifest does not list, a missing
    file or a changed digest is a problem, so an edited generated file is caught.
    """
    manifest = read_manifest(output_dir)
    out = Path(output_dir)
    problems: List[str] = []
    for path, expected in sorted(dict(manifest.get("files", {})).items()):
        target = out / path
        if not target.is_file():
            problems.append(f"missing generated file: {path}")
            continue
        observed = hashlib.sha256(target.read_bytes()).hexdigest()
        if observed != expected:
            problems.append(f"generated file changed since generation: {path}")
    return (not problems), problems