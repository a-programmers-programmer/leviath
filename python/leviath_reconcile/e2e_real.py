#!/usr/bin/env python3
"""Real-artifact end-to-end proof for GQL-02.

The synthetic ``Order`` domain in :mod:`leviath_reconcile.scenarios` proves the
reconciliation *rules*.  This module proves the same rules against the **real
accepted artifacts**:

* the GQL-01 contract SDL, ``docs/workflow/schema-workflow.graphql``, read with
  :func:`leviath_reconcile.adapters.schema_adapter.load_sdl`;
* its independently-authored typed-Python counterpart,
  ``python/leviath_reconcile/fixtures/workflow_model.py``, read with
  :func:`leviath_reconcile.adapters.python_adapter.load_python`.

Both adapters return an ``Extraction``; the projection is ``.projection`` (the
facts plus document identity), **not** ``.facts``.

What it proves, in order:

1. the two real projections reconcile to **zero conflicts** — the crosswalk
   equates every real SDL/code spelling pair and nothing more;
2. a **schema-only** edit and the mirrored **code-only** edit each resolve with
   zero conflicts and on their own side only (symmetry);
3. a **stale** document version is blocked instead of overwriting newer work;
4. re-reconciling the same real inputs is a **fixed point** (no ping-pong).

Offline: no provider, no network, no secret read, no production-store write.
Exit 0 = PASS, 1 = FAIL.  Usage:

    PYTHONPATH=python python3 -m leviath_reconcile e2e-real --json
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from .adapters import python_adapter, schema_adapter
from .baseline import Baseline, BaselineStore
from .model import FactSet, Projection
from .reconcile import Reconciler, StaleView
from .scenarios import projection
from .scenarios import half, rename_symbol, with_edit

SCHEMA = "schema"
CODE = "code"

DEFAULT_CONTRACT_DIR = "docs/workflow"
DEFAULT_CODE_PATH = "python/leviath_reconcile/fixtures/workflow_model.py"


def _agreed_baseline(schema: Projection, code: Projection) -> Baseline:
    """The last agreed version of the real pair: both projections, as read."""
    return Baseline(
        revision=1,
        contract="workflow-model v1",
        documents={SCHEMA: schema.document_identity, CODE: code.document_identity},
        body=FactSet(tuple(schema.facts) + tuple(code.facts)),
    )


def run_e2e(
    contract_dir: str | Path = DEFAULT_CONTRACT_DIR,
    code_path: str | Path = DEFAULT_CODE_PATH,
    *,
    work_dir: Optional[str] = None,
) -> Dict[str, object]:
    """Run the real-artifact proof.  Returns a serialisable result; raises on failure."""
    sdl_path = Path(contract_dir) / "schema-workflow.graphql"
    schema_extraction = schema_adapter.load_sdl(sdl_path)
    code_extraction = python_adapter.load_python(code_path)
    schema = schema_extraction.projection
    code = code_extraction.projection

    checks: List[str] = []
    metrics: Dict[str, object] = {
        "sdl": str(sdl_path),
        "code": str(code_path),
        "sdl_mode": schema_extraction.mode,
        "code_mode": code_extraction.mode,
        "schema_facts": len(schema.facts),
        "code_facts": len(code.facts),
    }

    # -- 1. the real pair reconciles cleanly ---------------------------------
    base = _agreed_baseline(schema, code)
    outcome = Reconciler(base).reconcile(schema, code)
    if outcome.plan.conflicts or not outcome.plan.ok:
        raise AssertionError(
            "the real contract pair must reconcile with zero conflicts, got "
            f"{outcome.plan.summary()}"
        )
    if outcome.changed:
        raise AssertionError("reconciling the agreed real pair must be a no-op")
    checks.append("real SDL and real typed-Python pair reconcile with zero conflicts")

    # -- 2. symmetry: a schema-only edit and its code-only mirror ------------
    schema_half, code_half = half(base.body, SCHEMA), half(base.body, CODE)
    schema_renamed = rename_symbol(schema_half, SCHEMA, "EdgeCondition", "EdgeConditionX")
    code_renamed = rename_symbol(code_half, CODE, "EdgeCondition", "EdgeConditionX")

    out_s = Reconciler(base).reconcile(
        projection(SCHEMA, schema_renamed, "sdl@v2"),
        projection(CODE, code_half, code.document_identity),
    )
    out_c = Reconciler(base).reconcile(
        projection(SCHEMA, schema_half, schema.document_identity),
        projection(CODE, code_renamed, "code@v2"),
    )
    for label, result in (("schema-only", out_s), ("code-only", out_c)):
        if result.plan.conflicts or not result.plan.ok:
            raise AssertionError(f"{label} real rename must not conflict: {result.plan.summary()}")
    s_origins = sorted({o for r in out_s.plan.resolutions for o in r.origins})
    c_origins = sorted({o for r in out_c.plan.resolutions for o in r.origins})
    if s_origins != [SCHEMA]:
        raise AssertionError(f"schema-only real edit resolved on {s_origins}, expected ['schema']")
    if c_origins != [CODE]:
        raise AssertionError(f"code-only real edit resolved on {c_origins}, expected ['code']")
    checks.append("a real schema-only rename and its code-only mirror resolve symmetrically")

    # A real retype, each direction, through the crosswalk.
    out_s2 = Reconciler(base).reconcile(
        projection(SCHEMA, with_edit(schema_half, SCHEMA, "EdgeCondition.signal", "type.kind", "Int!"), "sdl@v2"),
        projection(CODE, code_half, code.document_identity),
    )
    out_c2 = Reconciler(base).reconcile(
        projection(SCHEMA, schema_half, schema.document_identity),
        projection(CODE, with_edit(code_half, CODE, "EdgeCondition.signal", "type.kind", "int"), "code@v2"),
    )
    if not out_s2.plan.ok or not out_c2.plan.ok:
        raise AssertionError(f"real retype must not conflict: {out_s2.plan.summary()} / {out_c2.plan.summary()}")
    checks.append("a real schema-only retype and its code-only mirror resolve symmetrically")

    # -- 3. staleness: a superseded document cannot overwrite newer work -----
    stale_rec = Reconciler(base)
    stale_rec.declare({SCHEMA: "sdl@v2"})
    stale_out = stale_rec.reconcile(
        projection(SCHEMA, schema_half, "sdl@v1-superseded"),
        projection(CODE, code_half, code.document_identity),
    )
    if not stale_out.plan.stale or stale_out.plan.ok:
        raise AssertionError(
            f"a superseded real document must block the merge, got stale={stale_out.plan.stale} ok={stale_out.plan.ok}"
        )
    try:
        stale_rec.accept(
            projection(SCHEMA, schema_half, "sdl@v1-superseded"),
            projection(CODE, code_half, code.document_identity),
        )
        raise AssertionError("accept() must refuse a stale real view")
    except StaleView:
        pass
    checks.append("a superseded real document blocks the merge and accept() raises StaleView")

    # -- 4. idempotence: the real pair is a fixed point ----------------------
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        store = BaselineStore(os.path.join(tmp, "baseline.json"))
        seed_rec = Reconciler(base)
        seed_rec.declare({SCHEMA: schema.document_identity, CODE: code.document_identity})
        accepted = seed_rec.accept(schema, code, accepted_at="2026-09-12T00:00:00Z")
        digest = accepted.body.digest
        store.commit(seed_rec.accepted, expected_revision=1)
        current = store.load()
        for round_index in range(3):
            round_rec = Reconciler(current)
            round_out = round_rec.reconcile(schema, code)
            if round_out.plan.conflicts or not round_out.plan.ok:
                raise AssertionError(f"round {round_index}: real conflicts reappeared: {round_out.plan.summary()}")
            if round_out.body.digest != digest:
                raise AssertionError(
                    f"round {round_index}: real body moved to {round_out.body.digest[:12]} from {digest[:12]}"
                )
            if round_out.changed:
                raise AssertionError(f"round {round_index}: real merge is not a fixed point")
            round_rec.accept(schema, code, accepted_at="2026-09-12T00:00:00Z")
            current = round_rec.accepted
    checks.append("3 rounds re-reconciled the real pair; body digest stable; zero changes after the first")
    metrics["body_digest"] = digest

    return {
        "result": "PASS",
        "contract": "leviath-reconcile/e2e-real/v1",
        "checks": checks,
        "metrics": metrics,
        "schema_warnings": list(schema_extraction.warnings),
        "code_warnings": list(code_extraction.warnings),
    }


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="leviath_reconcile.e2e_real", description=__doc__)
    parser.add_argument("--contract-dir", default=DEFAULT_CONTRACT_DIR)
    parser.add_argument("--code", default=DEFAULT_CODE_PATH)
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_e2e(args.contract_dir, args.code, work_dir=args.work_dir)
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        if args.json:
            json.dump({"result": "FAIL", "error": f"{type(exc).__name__}: {exc}"}, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(f"e2e-real: FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(f"e2e-real: {result['result']}")
        for check in result["checks"]:
            print(f"  [PASS] {check}")
        print(f"  body digest: {result['metrics']['body_digest']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())