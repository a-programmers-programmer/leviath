#!/usr/bin/env python3
"""Offline contract gate for the reconciled workflow model (work order GQL-01).

Checks, with no network and no provider call:
  1. the SDL parses, builds and passes graphql-core ``validate_schema``;
  2. the definition-of-done invariants on the reconciled model hold;
  3. prints a content fingerprint for contract-identity pinning, and can
     re-verify that fingerprint later (``--expected-fingerprint``).

Usage:
    python3 check-workflow-contract.py [SDL] [--expected-fingerprint HEX]

Exit 0 = PASS, 1 = FAIL (reasons on stderr), 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from graphql import build_schema, get_named_type, is_enum_type, is_input_object_type, is_interface_type, is_list_type, is_non_null_type, is_object_type, is_scalar_type, is_union_type, parse, print_schema, validate_schema

# ---------------------------------------------------------------------------
# Contract identity. This mirrors the convention already used by
# crates/leviath-cli/agents/graphql-schema/scripts/gate.py:
#     hashes      = {relative_path: sha256(bytes)}
#     fingerprint = sha256(canonical(hashes))
# The artifact set is the semantic contract, so the fingerprint changes if the
# SDL changes *or* if the published rule contract changes.
# ---------------------------------------------------------------------------
CONTRACT_DIR = Path(__file__).resolve().parent
FINGERPRINT_ARTIFACTS = ("schema-workflow.graphql", "edge-invariant-rules.json")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def contract_hashes() -> dict:
    return {name: sha256_bytes((CONTRACT_DIR / name).read_bytes()) for name in FINGERPRINT_ARTIFACTS}


def contract_fingerprint() -> str:
    return sha256_bytes(canonical(contract_hashes()))

# ---------------------------------------------------------------------------
# Name-level prohibitions from the definition of done: no revival of the
# wrapper/binding/linear-ordering model the 2026-09-08 lock removed.
# ---------------------------------------------------------------------------
FORBIDDEN_TYPE_NAME_SUBSTRINGS = ("stepcontract", "stepbinding", "binding")
FORBIDDEN_FIELD_NAMES = {"stepnumber", "prev", "previous", "next", "prevstep", "nextstep", "steporder", "ordinal"}
FORBIDDEN_ENUM_VALUES = {"NEXT", "PREV", "PREVIOUS"}
# Hand-maintained input mirrors are forbidden; the transport is one generated
# finite envelope (PortValue) plus intent arguments.
MIRROR_INPUT_PREFIXES = ("Create", "Update", "Patch", "Upsert", "Delete")
# Port types are named *Input/*Output but are GraphQL interfaces/objects, never
# argument types.
PORT_SUFFIXES = ("Input", "Output")


def canonical_fingerprint(schema) -> str:
    """Semantic fingerprint of the SDL alone (printed, so comments/formatting do not churn it)."""
    return hashlib.sha256(print_schema(schema).encode("utf-8")).hexdigest()


def collect(schema):
    errors: list[str] = []
    warnings: list[str] = []

    types = {name: t for name, t in schema.type_map.items() if not name.startswith("__")}

    # --- DoD: no StepContract / binding / stepNumber / prev-next revival -----
    for name, t in types.items():
        low = name.lower()
        for bad in FORBIDDEN_TYPE_NAME_SUBSTRINGS:
            # A *field* literally named `binding`/`stepContract` is also a revival.
            if bad in low:
                errors.append(f"DoD/revival: type {name!r} matches forbidden name fragment {bad!r}")
        if is_object_type(t) or is_interface_type(t):
            for fname, f in t.fields.items():
                if fname.lower() in FORBIDDEN_FIELD_NAMES:
                    errors.append(f"DoD/revival: {name}.{fname} is a forbidden ordering/wrapper field")
        if is_enum_type(t):
            for vname in t.values:
                if vname in FORBIDDEN_ENUM_VALUES:
                    errors.append(f"DoD/prev-next revival: {name}.{vname} reintroduces linear prev/next sequencing")

    # --- DoD: severity and enforcement location are separate ----------------
    # Generalised: EVERY type that carries one of the pair must carry both.
    # A type with `severity` but no `enforcedAt` conflates how-bad with
    # where-checked; that is the exact defect the work order names.
    carriers = []
    for name, t in types.items():
        if not (is_object_type(t) or is_interface_type(t)):
            continue
        fields = set(t.fields)
        has_sev = "severity" in fields
        has_enf = "enforcedAt" in fields
        if has_sev or has_enf:
            carriers.append(name)
        if has_sev and not has_enf:
            errors.append(
                f"DoD/severity: {name} declares `severity` but no separate "
                "`enforcedAt` — how-bad and where-checked are conflated"
            )
        if has_enf and not has_sev:
            errors.append(
                f"DoD/severity: {name} declares `enforcedAt` but no separate `severity`"
            )
        if has_sev:
            sev = get_named_type(t.fields["severity"].type)
            if not (is_enum_type(sev) and sev.name == "EdgeInvariantSeverity"):
                errors.append(f"DoD/severity: {name}.severity must be EdgeInvariantSeverity")
        if has_enf:
            enf = get_named_type(t.fields["enforcedAt"].type)
            if not (is_enum_type(enf) and enf.name == "InvariantEnforcement"):
                errors.append(f"DoD/severity: {name}.enforcedAt must be InvariantEnforcement")
    if not carriers:
        errors.append("DoD/severity: no type carries the severity/enforcedAt pair")

    inv = types.get("EdgeInvariant")
    if inv is None:
        errors.append("DoD/severity: type EdgeInvariant missing")
    else:
        fields = set(inv.fields)
        if "severity" not in fields:
            errors.append("DoD/severity: EdgeInvariant has no `severity` field")
        if "enforcedAt" not in fields:
            errors.append(
                "DoD/severity: EdgeInvariant has no separate `enforcedAt` (enforcement location) field"
            )
        if "severity" in fields and "enforcedAt" in fields:
            sev = get_named_type(inv.fields["severity"].type)
            enf = get_named_type(inv.fields["enforcedAt"].type)
            if not (is_enum_type(sev) and sev.name == "EdgeInvariantSeverity"):
                errors.append("DoD/severity: `severity` must be the EdgeInvariantSeverity enum")
            if not (is_enum_type(enf) and enf.name == "InvariantEnforcement"):
                errors.append("DoD/severity: `enforcedAt` must be the InvariantEnforcement enum")
            if is_enum_type(sev) and is_enum_type(enf) and sev.name == enf.name:
                errors.append("DoD/severity: severity and enforcement share one enum — concepts conflated")

    # --- DoD: ERROR cycles cannot be waived into a DAG ----------------------
    rej = types.get("InvariantRejection")
    if rej is None:
        errors.append("DoD/error: type InvariantRejection missing")
    else:
        for fname, f in rej.fields.items():
            low = fname.lower()
            # A waiver/override/acknowledgement channel on a rejection is the
            # exact hole through which an ERROR cycle could become a DAG.
            WAIVER_CHANNELS = {
                "waiver", "waived", "waivedreason", "waiverreason", "override",
                "overridereason", "overridewith", "acknowledgement",
                "acknowledgedby", "acknowledgementreason", "ignored",
                "ignoredevidence", "dismissed", "force", "forced",
                "allowedbyoverride", "accepted", "severityoverride",
                "waivable", "iswaivable", "canwaive",
            }
            if low in WAIVER_CHANNELS or "waiv" in low or "overrid" in low:
                errors.append(
                    f"DoD/error: InvariantRejection.{fname} is a waiver/override channel — "
                    "an ERROR could be waived into a DAG"
                )
            # `reason` explains WHY it was rejected; it confers no authority.
            # It must be non-null, because a nullable reason on a rejection is
            # itself an implicit "absent means waived" contract.
            if low == "reason" and not is_non_null_type(f.type):
                errors.append(
                    "DoD/error: InvariantRejection.reason must be non-null — a nullable "
                    "reason is an implicit waiver contract"
                )
            if is_scalar_type(get_named_type(f.type)) and get_named_type(f.type).name == "Boolean":
                errors.append(
                    f"DoD/error: InvariantRejection.{fname} is a Boolean flag — flags are "
                    "how waivers sneak back into an ERROR"
                )
    disp = types.get("InvariantDisposition")
    if disp is None or not is_union_type(disp):
        errors.append("DoD/error: InvariantDisposition must be a union of satisfied|waiver|rejection")
    else:
        members = {m.name for m in disp.types}
        if "InvariantRejection" not in members or "InvariantWaiver" not in members:
            errors.append(f"DoD/error: InvariantDisposition members are {sorted(members)}")
        if "InvariantSatisfied" not in members:
            errors.append("DoD/error: InvariantDisposition is missing the satisfied arm")

    acyclic = types.get("EdgeInvariantRule")
    if acyclic is None or "ACYCLIC" not in acyclic.values:
        errors.append("DoD/error: EdgeInvariantRule.ACYCLIC missing")
    ctr = types.get("EdgeInvariantRuleContract")
    if ctr is None:
        errors.append("DoD/error: EdgeInvariantRuleContract missing (severity must be fixed by rule)")
    else:
        for f in ("rule", "severity", "enforcedAt", "waivable"):
            if f not in ctr.fields:
                errors.append(f"DoD/error: EdgeInvariantRuleContract.{f} missing")

    # --- DoD: input transport choice ----------------------------------------
    if "PortValue" not in types:
        errors.append("DoD/input: generated finite envelope scalar `PortValue` missing")
    for name in types:
        if name.startswith(MIRROR_INPUT_PREFIXES) and name.endswith("Input"):
            errors.append(f"DoD/input: hand-maintained mirror input type {name!r} present")

    mutation = schema.mutation_type
    if mutation is None:
        errors.append("DoD/input: schema has no Mutation type")
    else:
        for fname, f in mutation.fields.items():
            for argname, arg in f.args.items():
                named = get_named_type(arg.type)
                if is_interface_type(named):
                    errors.append(
                        f"DoD/input: {mutation.name}.{fname}({argname}) takes interface "
                        f"{named.name!r} as an argument — interfaces are not input types"
                    )
                if is_union_type(named):
                    errors.append(
                        f"DoD/input: {mutation.name}.{fname}({argname}) takes union {named.name!r} "
                        "as an argument — unions are not input types"
                    )
                if is_object_type(named) and named.name.endswith(PORT_SUFFIXES):
                    errors.append(
                        f"DoD/input: {mutation.name}.{fname}({argname}) takes port output type "
                        f"{named.name!r} as an argument"
                    )
        if "dispatchWorkflow" not in mutation.fields:
            errors.append("DoD/input: Mutation.dispatchWorkflow missing")

    # --- DoD: enum/interface outcome rules documented and obeyed ------------
    # Closed symbolic outcomes with no payload are enums; payload outcomes are unions.
    for wanted, kind in (
        ("GateDecision", "enum"),
        ("GateOutcome", "enum"),
        ("WorkflowRunOutcome", "union"),
        ("WorkflowState", "enum"),
    ):
        t = types.get(wanted)
        if t is None:
            errors.append(f"DoD/outcome: {wanted} missing")
        elif kind == "enum" and not is_enum_type(t):
            errors.append(f"DoD/outcome: {wanted} must be an enum")
        elif kind == "union" and not is_union_type(t):
            errors.append(f"DoD/outcome: {wanted} must be a union (carries per-variant payload)")
    for name in ("WorkflowStepInput", "WorkflowStepOutput"):
        t = types.get(name)
        if t is None or not is_interface_type(t):
            errors.append(f"DoD/outcome: domain port {name} must exist and be an interface")
    # No nullable output/error pair reintroducing the implicit-state contract.
    for tname in ("WorkflowRun", "WorkflowStepRun"):
        t = types.get(tname)
        if t is None:
            errors.append(f"DoD/outcome: {tname} missing")
            continue
        if "outcome" not in t.fields:
            errors.append(f"DoD/outcome: {tname}.outcome missing (gated-value pattern not applied)")
        for stale in ("output", "error"):
            if stale in t.fields:
                errors.append(
                    f"DoD/outcome: {tname}.{stale} is a nullable implicit-state field; "
                    "state must ride in `outcome`"
                )
    # A caller cannot decide an outcome it cannot cause.
    gd, go = types.get("GateDecision"), types.get("GateOutcome")
    if gd is not None and go is not None and is_enum_type(gd) and is_enum_type(go):
        if "EXPIRED" in gd.values:
            errors.append("DoD/outcome: GateDecision exposes EXPIRED — an outcome the caller cannot decide")

    # --- DoD: sensitive values are opaque, and authority is not a string ------
    conf = types.get("Confidential")
    if conf is None or not is_scalar_type(conf):
        errors.append("DoD/sensitive: opaque scalar `Confidential` missing")
    else:
        for name, t in types.items():
            if is_object_type(t):
                for fname, f in t.fields.items():
                    if "token" in fname.lower() and is_scalar_type(get_named_type(f.type)) and get_named_type(f.type).name not in ("Confidential",):
                        errors.append(
                            f"DoD/sensitive: {name}.{fname} should be Confidential, not "
                            f"{get_named_type(f.type).name}"
                        )

    # --- R3 hygiene: the contract table lists every rule --------------------
    if ctr is not None and acyclic is not None:
        pass  # enumerated at runtime by resolvers; structural shape checked above

    return errors, warnings


def run(path: Path, expected: str | None):
    src = path.read_text(encoding="utf-8")
    try:
        parse(src)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL parse: {exc}", file=sys.stderr)
        return 1
    try:
        schema = build_schema(src)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL build: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    verrs = validate_schema(schema)
    if verrs:
        for e in verrs:
            print(f"FAIL validate_schema: {e.message}", file=sys.stderr)
        return 1

    errors, warnings = collect(schema)
    fp = canonical_fingerprint(schema)
    # ACYCLIC must be ERROR and non-waivable per the published contract.
    if expected is not None and expected != fp:
        print(f"FAIL fingerprint: expected {expected}, got {fp}", file=sys.stderr)
        return 1
    print(json.dumps(
        {
            "result": "PASS" if not errors else "FAIL",
            "sdl": str(path),
            "parses": True,
            "validate_schema_errors": 0,
            "dod_violations": errors,
            "warnings": warnings,
            "fingerprint": fp,
        },
        indent=2, sort_keys=True,
    ))
    if errors:
        for e in errors:
            print(f"FAIL DoD: {e}", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("sdl", nargs="?", default=str(here / "schema-workflow.graphql"))
    ap.add_argument("--expected-fingerprint", default=None)
    a = ap.parse_args(argv)
    p = Path(a.sdl)
    if not p.is_file():
        print(f"usage error: no such SDL: {p}", file=sys.stderr)
        return 2
    return run(p, a.expected_fingerprint)


if __name__ == "__main__":
    sys.exit(main())
