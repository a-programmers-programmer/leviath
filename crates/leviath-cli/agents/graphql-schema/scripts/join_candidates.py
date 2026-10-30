"""Join reconciled HarnessQL merge work to the existing contract acceptance loop.

This module is deterministic code. It does not review, does not judge, and does not
mint acceptance. It:

  1. verifies the source identity of the reconciled merge result (candidate paths,
     candidate hashes, decision document, result hashes, lane run ids);
  2. installs the reconciled SDL as the contract schema under test;
  3. checks that the merged schema adds no obligation the contract omits;
  4. feeds the result into the EXISTING full contract gate (scripts/gate.py) and
     the EXISTING independent review route;
  5. records the checker version, guide version, source identity and review evidence;
  6. keeps the two repair passes and an explicit terminal failure route.

Merge success alone can never mint accepted.json: only the existing gate writes the
receipt, and only for a fresh review of the same fingerprint. Runtime verification and
product/rollout judgment are reported as separate, unperformed work.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import gate

MAX_REPAIR_PASSES = 2
GUIDE_VERSION = "v1"
HERE = Path(__file__).resolve().parents[1]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha256_of(value) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _blocked(errors, *, source_identity=None, obligations=None, repair_attempts=0,
             fingerprint=None, review_evidence=None):
    terminal = repair_attempts >= MAX_REPAIR_PASSES
    if terminal:
        errors = [*errors, f"Repair passes exhausted ({MAX_REPAIR_PASSES}); terminal failure"]
    return {
        "status": "blocked",
        "route": "terminal" if terminal else "revise",
        "fingerprint": fingerprint,
        "errors": [str(e) for e in errors],
        "checker": _checker(),
        "guide": _guide(),
        "source_identity": source_identity,
        "obligations": obligations or {"required": 0, "covered": 0, "omitted": []},
        "repair": _repair(repair_attempts),
        "contract": {"status": "blocked", "errors": [str(e) for e in errors]},
        "review_evidence": review_evidence,
        "runtime_verification": _runtime(),
        "product_judgment": _product(),
    }


def _checker():
    return {"version": gate.VERSION, "sha256": sha256_file(HERE / "scripts/gate.py"),
            "path": "scripts/gate.py"}


def _guide():
    return {"version": GUIDE_VERSION,
            "sha256": sha256_file(HERE / "README.md"),
            "tool_sha256": sha256_file(HERE / "tools/graphql_contract_guide.rhai"),
            "path": "README.md"}


def _repair(attempts):
    return {"passes_allowed": MAX_REPAIR_PASSES, "attempts": attempts,
            "state": "exhausted" if attempts >= MAX_REPAIR_PASSES else "available",
            "terminal": attempts >= MAX_REPAIR_PASSES}


def _runtime():
    return {"state": "unverified", "separate_from_acceptance": True,
            "owner": "application implementers",
            "note": "Contract acceptance does not verify runtime behavior."}


def _product():
    return {"state": "not_made", "separate_from_acceptance": True,
            "owner": "Josh / product",
            "note": "Contract acceptance is not a product or rollout judgment."}


def _verify_file(path, recorded, label, errors):
    path = Path(path)
    if not path.is_file():
        errors.append(f"source identity: {label} missing: {path}")
        return None
    actual = sha256_file(path)
    if recorded is not None and actual != recorded:
        errors.append(f"source identity: {label} hash mismatch for {path}: "
                      f"recorded {recorded}, actual {actual}")
        return None
    return actual


def source_identity(record, errors):
    """Verify the reconciled merge result and return its recorded identity."""
    identity = {"candidates": {}, "run_ids": [], "independent_convergence": False,
                "determinism": False, "state": "blocked"}
    if not isinstance(record, dict):
        errors.append("source identity: merge record must be an object")
        return identity
    if record.get("state") != "verified":
        errors.append(f"source identity: merge record state is {record.get('state')!r}, not 'verified'")

    paths = record.get("candidate_paths") or {}
    hashes = record.get("candidate_sha256") or {}
    for key in ("a", "b"):
        if not paths.get(key):
            errors.append(f"source identity: candidate {key} path missing")
            continue
        actual = _verify_file(paths[key], hashes.get(key), f"candidate {key}", errors)
        identity["candidates"][key] = {"path": str(paths[key]), "sha256": actual}

    merged = record.get("merged") or []
    merged_hashes = record.get("merged_sha256") or []
    if not merged:
        errors.append("source identity: no merged result recorded")
    else:
        if len(merged) != len(merged_hashes):
            errors.append("source identity: merged paths and hashes disagree in length")
        actual = []
        for index, path in enumerate(merged):
            recorded = merged_hashes[index] if index < len(merged_hashes) else None
            actual.append(_verify_file(path, recorded, f"merged result {index}", errors))
        if len(set(actual)) > 1:
            errors.append(f"source identity: divergent merge results {actual}")
        elif actual and actual[0]:
            identity["determinism"] = True
            identity["result"] = {"path": str(merged[0]), "sha256": actual[0]}

    decisions = record.get("decisions") or {}
    if decisions.get("path"):
        actual = _verify_file(decisions["path"], decisions.get("sha256"), "decisions", errors)
        identity["decisions"] = {"path": str(decisions["path"]), "sha256": actual}
    else:
        errors.append("source identity: decision document missing")

    provenance = record.get("provenance") or []
    run_ids = []
    for lane in provenance:
        if not isinstance(lane, dict) or not lane.get("run_id"):
            errors.append("source identity: lane run id missing")
            continue
        run_ids.append(str(lane["run_id"]))
    if not run_ids:
        errors.append("source identity: no lane run id recorded")
    if len(run_ids) != len(set(run_ids)):
        errors.append(f"source identity: self-merge, repeated lane run id in {run_ids}; "
                      "not independent convergence")
    identity["run_ids"] = run_ids
    identity["independent_convergence"] = len(set(run_ids)) > 1
    identity["state"] = "verified" if not errors else "blocked"
    return identity


def obligations(root, errors):
    """Every output field in the merged schema needs a contract policy."""
    from graphql import (build_schema, get_named_type, is_enum_type, is_input_object_type,
                         is_interface_type, is_object_type, is_scalar_type)

    root = Path(root)
    result = {"required": 0, "covered": 0, "omitted": []}
    schema_path = root / "schema.graphql"
    contract_path = root / "contract.json"
    if not schema_path.is_file() or not contract_path.is_file():
        return result
    try:
        schema = build_schema(schema_path.read_text())
        contract = json.loads(contract_path.read_text())
    except Exception as error:  # noqa: BLE001
        errors.append(f"obligations: cannot read contract artifacts: {type(error).__name__}: {error}")
        return result
    coords = set()
    for name, typ in schema.type_map.items():
        if name.startswith("__") or name in ("String", "ID", "Int", "Float", "Boolean"):
            continue
        if is_object_type(typ) or is_interface_type(typ):
            for fname in typ.fields:
                coords.add(f"{name}.{fname}")
        elif is_input_object_type(typ) or is_enum_type(typ) or is_scalar_type(typ):
            continue
    covered = set((contract.get("fields") or {}))
    omitted = sorted(coords - covered)
    result.update(required=len(coords), covered=len(coords & covered), omitted=omitted)
    if omitted:
        errors.append(f"Omitted obligations: merged schema fields without a contract policy: {omitted}")
    return result


def _install(root, identity, errors):
    result = identity.get("result")
    if not result:
        return
    source = Path(result["path"])
    target = Path(root) / "schema.graphql"
    if not source.is_file():
        errors.append(f"source identity: merged result missing: {source}")
        return
    data = source.read_bytes()
    if not target.is_file() or target.read_bytes() != data:
        target.write_bytes(data)


def _review_evidence(review):
    if not isinstance(review, dict):
        return None
    return {"fingerprint": review.get("fingerprint"), "verdict": review.get("verdict"),
            "rubric": sorted((review.get("rubric") or {})),
            "blocking_findings": len(review.get("blocking_findings") or []),
            "sha256": sha256_of(review)}


def join(root, record, op="check", review=None, repair_attempts=0):
    """Join a reconciled merge result to the existing gate and review route."""
    root = Path(root)
    errors = []
    if root.is_symlink():
        return _blocked(["Contract root cannot be a symlink"], repair_attempts=repair_attempts)
    receipt = root / "accepted.json"
    if receipt.is_symlink():
        return _blocked(["Receipt cannot be symlinked"], repair_attempts=repair_attempts)
    if receipt.exists():
        receipt.unlink()

    identity = source_identity(record, errors)
    if not errors:
        _install(root, identity, errors)
    obligations_result = obligations(root, errors) if not errors else {"required": 0, "covered": 0, "omitted": []}
    if errors:
        return _blocked(errors, source_identity=identity, obligations=obligations_result,
                        repair_attempts=repair_attempts)
    if op == "accept" and not isinstance(review, dict):
        return _blocked(["Acceptance requires a review record"], source_identity=identity,
                        obligations=obligations_result, repair_attempts=repair_attempts)

    request = {"op": op, "root": str(root)}
    if op == "accept":
        request["review"] = review
    try:
        result = gate.dispatch(request)
    except Exception as error:  # noqa: BLE001
        return _blocked([f"{type(error).__name__}: {error}"], source_identity=identity,
                        obligations=obligations_result, repair_attempts=repair_attempts)

    if result.get("status") not in ("checked", "accepted"):
        return _blocked(result.get("errors") or ["Contract gate blocked"], source_identity=identity,
                        obligations=obligations_result, repair_attempts=repair_attempts,
                        fingerprint=result.get("fingerprint"))

    accepted = result.get("status") == "accepted"
    contract = dict(result)
    contract["review_evidence"] = _review_evidence(result.get("review") or review)
    joined = {
        "status": "accepted" if accepted else "checked",
        "route": "done" if accepted else "review",
        "fingerprint": result.get("fingerprint"),
        "errors": [],
        "checker": _checker(),
        "guide": _guide(),
        "source_identity": identity,
        "obligations": obligations_result,
        "repair": _repair(repair_attempts),
        "contract": contract,
        "review_evidence": contract["review_evidence"],
        "runtime_verification": _runtime(),
        "product_judgment": _product(),
    }
    _write_record(root, joined)
    return joined


def _write_record(root, joined):
    out = Path(root).parent / "graphql-join"
    out.mkdir(parents=True, exist_ok=True)
    (out / "join-record.json").write_text(json.dumps(joined, indent=2, sort_keys=True) + "\n")
