#!/usr/bin/env python3
"""Structured, exact merge decisions for HarnessQL (work order GQL-09).

The merge reviewer produces ONE JSON document (contract
`harnessql-merge-decisions/v1`). This module is the authority on it:

  * exact      — every divergent type carries a decision, and every field, enum
                 value, union member and interface of that type carries a
                 source. Totality is enforced, so "nothing was dropped" is a
                 checked property, not a claim.
  * strict     — omitted, duplicate, unknown, conflicting and stale decisions
                 are rejected with a machine-readable code.
  * deterministic — the same candidates and the same decision document produce
                 byte-identical output in any process, in any directory (see
                 `tests/test_merge_decisions.py::test_determinism`).
  * prose is evidence — `reason` strings are preserved, hashed and reported;
                 they are never parsed. The pre-GQL-09 `apply_merge` guessed a
                 base side and a field set out of English ("adopt B's fields",
                 "restore X from A"), which is exactly what this replaces.

Exit codes for the CLI (the runner reports them verbatim as its state):

  0  passed   — decisions valid, merge applied, result hashed
  1  rejected — the decision document is invalid (omitted/duplicate/unknown/
                conflicting/stale); the merge reviewer must fix it
  2  blocked  — the inputs themselves are unusable (unreadable, unparseable)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field as dc_field
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from graphql import parse as gql_parse  # noqa: E402
from graphql.language.ast import DocumentNode  # noqa: E402

from graphql_diff import (  # noqa: E402
    MergeDecision,
    apply_merge,
    diff_types,
    propose_renames,
)

CONTRACT = "harnessql-merge-decisions/v1"
DISPOSITIONS = ("keep_a", "keep_b", "reconcile")
SOURCES = ("a", "b")
RENAME_DISPOSITIONS = ("rename", "distinct")
ERROR_CODES = ("malformed", "duplicate", "omitted", "unknown", "conflict", "stale")
MAX_REASON = 400

#: Which AST node list holds the keys a decision chooses between, per node kind.
#: graphql-core names these `object_type_definition`, `enum_type_definition`, ...
_AST_KINDS = {
    "object_type_definition": {"attr": "fields", "label": "fields"},
    "interface_type_definition": {"attr": "fields", "label": "fields"},
    "input_object_type_definition": {"attr": "fields", "label": "fields"},
    "enum_type_definition": {"attr": "values", "label": "values"},
    "union_type_definition": {"attr": "types", "label": "members"},
}
#: the JSON key a reviewer writes for each AST attribute
_JSON_KEY = {"fields": "fields", "values": "values", "types": "members"}
#: definition kinds that carry no decidable keys
_KEYLESS_KINDS = ("scalar_type_definition", "schema_definition",
                  "directive_definition")


class MergeDecisionError(ValueError):
    """A rejected decision document, tagged with one of ERROR_CODES."""

    def __init__(self, code: str, message: str):
        assert code in ERROR_CODES, f"unknown error code {code!r}"
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


# ── canonical hashing ────────────────────────────────────────────────────

def canonical_json(obj: Any) -> str:
    """The one serialization used for hashing and for writing evidence."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | os.PathLike) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _no_duplicate_keys(pairs):
    """`json.loads` object hook: a repeated key is a duplicate decision, not a loser."""
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise MergeDecisionError("duplicate", f"duplicate JSON key {key!r}")
        seen[key] = value
    return seen


def load_document(text: str) -> dict:
    """Parse the reviewer's JSON, refusing anything that is not that document."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Tolerate a fenced block; the fence is formatting, not content.
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    if not stripped.startswith("{"):
        raise MergeDecisionError(
            "unknown",
            "the merge reviewer must emit the harnessql-merge-decisions/v1 JSON "
            "document; line-oriented prose decisions are not accepted",
        )
    try:
        doc = json.loads(stripped, object_pairs_hook=_no_duplicate_keys)
    except MergeDecisionError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported as a rejection, not a crash
        raise MergeDecisionError("malformed", f"decision document is not valid JSON: {exc}")
    if not isinstance(doc, dict):
        raise MergeDecisionError("malformed", "decision document must be a JSON object")
    return doc


# ── the JSON Schema the reviewer's submission is checked against ─────────

def _source_map_schema() -> dict:
    return {"type": "object", "additionalProperties": {"enum": list(SOURCES)}}


def decision_json_schema() -> dict:
    """The loose structural schema handed to `lev run --output-schema`.

    Deliberately loose: it stops a non-JSON or wrongly-shaped submission at the
    runtime boundary. The strict authority is :func:`validate`, which is the
    only thing that knows the diff.
    """
    candidate = {
        "type": "object",
        "required": ["path", "sha256"],
        "properties": {"path": {"type": "string"}, "sha256": {"type": "string"}},
    }
    type_entry = {
        "type": "object",
        "required": ["name", "disposition", "reason"],
        "properties": {
            "name": {"type": "string"},
            "disposition": {"enum": list(DISPOSITIONS)},
            "reason": {"type": "string", "maxLength": MAX_REASON},
            "fields": _source_map_schema(),
            "values": _source_map_schema(),
            "members": _source_map_schema(),
            "implements": _source_map_schema(),
        },
    }
    rename_entry = {
        "type": "object",
        "required": ["from", "to", "disposition", "reason"],
        "properties": {
            "from": {"type": "string"},
            "to": {"type": "string"},
            "disposition": {"enum": list(RENAME_DISPOSITIONS)},
            "reason": {"type": "string", "maxLength": MAX_REASON},
        },
    }
    return {
        "type": "object",
        "required": ["contract", "candidates", "types", "renames"],
        "properties": {
            "contract": {"enum": [CONTRACT]},
            "diff": {"type": "object", "properties": {"sha256": {"type": "string"}}},
            "candidates": {
                "type": "object",
                "required": ["a", "b"],
                "properties": {"a": candidate, "b": candidate},
            },
            "types": {"type": "array", "items": type_entry},
            "renames": {"type": "array", "items": rename_entry},
        },
    }


# ── validation ───────────────────────────────────────────────────────────

@dataclass
class Validated:
    decisions: list = dc_field(default_factory=list)
    renames: dict = dc_field(default_factory=dict)
    reasons: dict = dc_field(default_factory=dict)
    decision_hash: str = ""
    canonical: str = ""
    candidate_hashes: dict = dc_field(default_factory=dict)
    candidate_paths: dict = dc_field(default_factory=dict)
    diff_sha256: Optional[str] = None


def _ast_index(text: str, label: str) -> dict:
    try:
        doc: DocumentNode = gql_parse(text)
    except Exception as exc:  # noqa: BLE001
        raise MergeDecisionError(
            "malformed", f"candidate {label} is not parseable SDL: {exc}"
        )
    index = {}
    for definition in doc.definitions:
        name = getattr(getattr(definition, "name", None), "value", None)
        if name:
            index[name] = definition
    return index


def _keys(node, attr: str) -> list[str]:
    out = []
    for item in getattr(node, attr, None) or ():
        key = getattr(getattr(item, "name", None), "value", None)
        if key and key not in out:
            out.append(key)
    return out


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise MergeDecisionError(code, message)


def validate(
    doc: dict,
    a_text: str,
    b_text: str,
    expected: Optional[dict] = None,
) -> Validated:
    """Validate a decision document against the two candidates.

    `expected` optionally binds the document to the exact inputs the reviewer was
    shown: `{"diff_text": ..., "candidates": {"a": path, "b": path}}`.
    """
    expected = expected or {}

    _require(
        doc.get("contract") == CONTRACT,
        "unknown",
        f"contract must be {CONTRACT!r}, got {doc.get('contract')!r}",
    )

    a_sha = sha256_text(a_text)
    b_sha = sha256_text(b_text)

    candidates = doc.get("candidates")
    _require(isinstance(candidates, dict), "omitted", "missing 'candidates' block")
    candidate_paths: dict[str, str] = {}
    for side, sha in (("a", a_sha), ("b", b_sha)):
        entry = candidates.get(side)
        _require(isinstance(entry, dict), "omitted", f"missing candidates.{side}")
        _require(
            isinstance(entry.get("sha256"), str) and entry.get("sha256"),
            "omitted",
            f"missing candidates.{side}.sha256",
        )
        _require(
            entry["sha256"] == sha,
            "stale",
            f"candidates.{side}.sha256 is {entry['sha256']}, but the actual "
            f"candidate hashes to {sha}: the decisions do not describe these bytes",
        )
        _require(
            isinstance(entry.get("path"), str) and entry.get("path"),
            "omitted",
            f"missing candidates.{side}.path",
        )
        candidate_paths[side] = entry["path"]

    diff = diff_types(a_text, b_text)
    a_index = _ast_index(a_text, "a")
    b_index = _ast_index(b_text, "b")

    diff_text = expected.get("diff_text")
    diff_sha = None
    if diff_text is not None:
        diff_sha = sha256_text(diff_text)
        got = (doc.get("diff") or {}).get("sha256")
        _require(isinstance(got, str) and got, "omitted", "missing diff.sha256")
        _require(
            got == diff_sha,
            "stale",
            f"diff.sha256 is {got}, but the diff shown to the reviewer hashes "
            f"to {diff_sha}",
        )

    decisions: list[MergeDecision] = []
    reasons: dict[str, str] = {}
    seen_types: set[str] = set()

    entries = doc.get("types")
    _require(isinstance(entries, list), "omitted", "missing 'types' array")

    for entry in entries:
        _require(isinstance(entry, dict), "malformed", "each type entry must be an object")
        name = entry.get("name")
        _require(isinstance(name, str) and name, "omitted", "a type entry has no name")
        _require(name not in seen_types, "duplicate", f"type {name!r} is decided twice")
        seen_types.add(name)

        _require(
            name in diff.divergent,
            "unknown",
            f"type {name!r} is not a divergent type of these candidates "
            f"(only-A: {name in diff.only_a}, only-B: {name in diff.only_b})",
        )
        a_def = a_index.get(name)
        b_def = b_index.get(name)
        _require(
            a_def is not None and b_def is not None,
            "malformed",
            f"type {name!r} is divergent but not present in both candidates' ASTs",
        )

        disposition = entry.get("disposition")
        _require(
            disposition in DISPOSITIONS,
            "unknown",
            f"{name}: disposition {disposition!r} is not one of {list(DISPOSITIONS)}",
        )

        reason = entry.get("reason")
        _require(
            isinstance(reason, str) and reason.strip(),
            "omitted",
            f"{name}: 'reason' is required (rationale is part of the record)",
        )
        _require(
            len(reason) <= MAX_REASON,
            "unknown",
            f"{name}: reason is longer than {MAX_REASON} characters",
        )
        reasons[name] = reason.strip()

        attr = _AST_KINDS.get(a_def.kind, {}).get("attr")
        if attr is None:  # scalar / schema / directive: nothing decidable
            _require(
                a_def.kind in _KEYLESS_KINDS,
                "malformed",
                f"{name}: unrecognized definition kind {a_def.kind!r}",
            )
            _require(
                disposition != "reconcile",
                "conflict",
                f"{name}: a {a_def.kind.replace('_', ' ')} has no keys to reconcile",
            )
            decisions.append(MergeDecision(name, disposition, reasons[name]))
            continue

        group_name = _JSON_KEY[attr]
        a_keys = _keys(a_def, attr)
        b_keys = _keys(b_def, attr)
        union = a_keys + [k for k in b_keys if k not in a_keys]
        supplied = entry.get(group_name)
        _require(
            isinstance(supplied, dict),
            "omitted",
            f"{name}: '{group_name}' map is required (every {group_name} entry of "
            f"the merged type needs a source)",
        )

        if disposition == "keep_a":
            permitted, forced = a_keys, "a"
        elif disposition == "keep_b":
            permitted, forced = b_keys, "b"
        else:
            permitted, forced = union, None

        missing = [k for k in permitted if k not in supplied]
        _require(
            not missing,
            "omitted",
            f"{name}: no source given for {group_name} {missing}",
        )
        not_of_either = sorted(k for k in supplied if k not in union)
        _require(
            not not_of_either,
            "unknown",
            f"{name}: {group_name} {not_of_either} belongs to neither candidate's "
            f"version of this type",
        )
        excluded = sorted(k for k in supplied if k not in permitted)
        _require(
            not excluded,
            "conflict",
            f"{name}: disposition {disposition} covers only "
            f"{'A' if disposition == 'keep_a' else 'B'}'s {group_name}, so "
            f"{excluded} cannot be taken from the other candidate; use "
            "'reconcile' and name each side",
        )
        bad_source = sorted({v for v in supplied.values() if v not in SOURCES})
        _require(
            not bad_source,
            "unknown",
            f"{name}: source {bad_source} is not one of {list(SOURCES)}",
        )

        if disposition in ("keep_a", "keep_b"):
            wrong = sorted(k for k, v in supplied.items() if v != forced)
            _require(
                not wrong,
                "conflict",
                f"{name}: disposition {disposition} cannot take {wrong} from the "
                f"other candidate; use 'reconcile' and name the source per {group_name}",
            )
        else:
            if "__KIND_CHANGED__" in diff.divergent[name].changed:
                raise MergeDecisionError(
                    "conflict",
                    f"{name}: the two candidates define this name with different "
                    "kinds, which cannot be reconciled field by field",
                )
            used = set(supplied.values())
            _require(
                used == set(SOURCES),
                "conflict",
                f"{name}: disposition 'reconcile' must take something from both "
                f"sides; this decision takes only from {sorted(used)} (that is a "
                "whole-side pick, so declare keep_a or keep_b)",
            )

        decision = MergeDecision(name, disposition, reasons[name])
        decision.sources = {k: supplied[k] for k in union if k in supplied}
        decision.sources = dict(sorted(decision.sources.items()))

        if attr == "fields" and a_def.kind in (
                "object_type_definition", "interface_type_definition"):
            a_ifaces = _keys(a_def, "interfaces")
            b_ifaces = _keys(b_def, "interfaces")
            iface_union = a_ifaces + [k for k in b_ifaces if k not in a_ifaces]
            if iface_union:
                given = entry.get("implements")
                if disposition == "reconcile":
                    _require(
                        isinstance(given, dict),
                        "omitted",
                        f"{name}: 'implements' map is required ({iface_union})",
                    )
                    _require(
                        not [k for k in iface_union if k not in given],
                        "omitted",
                        f"{name}: no source given for implements "
                        f"{[k for k in iface_union if k not in given]}",
                    )
                    _require(
                        not [k for k in given if k not in iface_union],
                        "unknown",
                        f"{name}: implements {[k for k in given if k not in iface_union]} "
                        "is not an interface of either candidate's version",
                    )
                    _require(
                        not [v for v in given.values() if v not in SOURCES],
                        "unknown",
                        f"{name}: implements source must be one of {list(SOURCES)}",
                    )
                    decision.implements = dict(sorted(given.items()))
                else:
                    side = a_ifaces if disposition == "keep_a" else b_ifaces
                    decision.implements = {k: ("a" if disposition == "keep_a" else "b")
                                           for k in side}

        decisions.append(decision)

    omitted_types = sorted(set(diff.divergent) - seen_types)
    _require(
        not omitted_types,
        "omitted",
        f"no decision for divergent type(s) {omitted_types}",
    )

    # ── renames ──────────────────────────────────────────────────────────
    proposals = propose_renames(diff)
    proposal_set = set(proposals)
    rename_entries = doc.get("renames")
    _require(isinstance(rename_entries, list), "omitted", "missing 'renames' array")

    renames: dict[str, str] = {}
    decided_pairs = set()
    taken_from: set[str] = set()
    taken_to: set[str] = set()
    for entry in rename_entries:
        _require(isinstance(entry, dict), "malformed", "each rename entry must be an object")
        old, new = entry.get("from"), entry.get("to")
        _require(
            isinstance(old, str) and isinstance(new, str) and old and new,
            "omitted",
            "'renames' entries need string 'from' and 'to'",
        )
        pair = (old, new)
        _require(pair not in decided_pairs, "duplicate", f"rename {old} -> {new} is decided twice")
        decided_pairs.add(pair)
        _require(
            pair in proposal_set,
            "unknown",
            f"rename {old} -> {new} was not proposed for these candidates "
            f"(proposed: {proposals})",
        )
        disposition = entry.get("disposition")
        _require(
            disposition in RENAME_DISPOSITIONS,
            "unknown",
            f"rename {old} -> {new}: disposition must be one of "
            f"{list(RENAME_DISPOSITIONS)}",
        )
        reason = entry.get("reason")
        _require(
            isinstance(reason, str) and reason.strip(),
            "omitted",
            f"rename {old} -> {new}: 'reason' is required",
        )
        reasons[f"rename:{old}->{new}"] = reason.strip()
        if disposition == "rename":
            _require(old not in taken_from, "conflict", f"{old} is renamed twice")
            _require(new not in taken_to, "conflict", f"two renames target {new}")
            _require(
                new not in taken_from and old not in taken_to,
                "conflict",
                f"rename {old} -> {new} chains into another rename decision",
            )
            _require(
                new in diff.only_b,
                "conflict",
                f"rename target {new} must be a type only candidate B defines; "
                f"{new} is "
                + ("shared by both candidates" if new in diff.both or new in diff.divergent
                   else "not a type of these candidates"),
            )
            taken_from.add(old)
            taken_to.add(new)
            renames[old] = new

    omitted_pairs = sorted(proposal_set - decided_pairs)
    _require(
        not omitted_pairs,
        "omitted",
        f"no decision for proposed rename(s) {omitted_pairs}",
    )

    canonical = canonical_json(doc)
    return Validated(
        decisions=decisions,
        renames=renames,
        reasons=reasons,
        decision_hash=sha256_text(canonical),
        canonical=canonical,
        candidate_hashes={"a": a_sha, "b": b_sha},
        candidate_paths=candidate_paths,
        diff_sha256=diff_sha,
    )


# ── merge + evidence ─────────────────────────────────────────────────────

@dataclass
class Outcome:
    result_sdl: str
    result_sha256: str
    decision_hash: str
    candidate_hashes: dict
    diff_sha256: Optional[str]
    reasons: dict
    renames: dict
    decisions: list
    rationale_md: str
    candidate_paths: dict = dc_field(default_factory=dict)
    status: str = "verified"


def build_rationale(validated: Validated, result_sdl: str, candidates: dict) -> str:
    paths = validated.candidate_paths or candidates
    rows = [
        "# HarnessQL merge rationale",
        "",
        f"- contract: `{CONTRACT}`",
        f"- decision document sha256: `{validated.decision_hash}`",
        f"- candidate a: `{paths.get('a', '?')}` sha256 `{validated.candidate_hashes['a']}`",
        f"- candidate b: `{paths.get('b', '?')}` sha256 `{validated.candidate_hashes['b']}`",
        f"- diff sha256: `{validated.diff_sha256 or 'not supplied'}`",
        f"- result sha256: `{sha256_text(result_sdl)}`",
        "",
        "## Per-type decisions",
        "",
        "| type | disposition | sources | reason |",
        "| --- | --- | --- | --- |",
    ]
    for decision in validated.decisions:
        if decision.sources:
            sources = ", ".join(f"{k}={v}" for k, v in sorted(decision.sources.items()))
            if decision.implements:
                sources += "; " + ", ".join(
                    f"implements {k}={v}" for k, v in sorted(decision.implements.items())
                )
        else:
            sources = "—"
        rows.append(
            f"| {decision.type_name} | {decision.action} | {sources} | "
            f"{decision.reason.replace('|', '/')} |"
        )
    rows += ["", "## Rename decisions", ""]
    if validated.renames:
        for old, new in sorted(validated.renames.items()):
            rows.append(f"- `{old}` -> `{new}` (rename) — {validated.reasons.get(f'rename:{old}->{new}', '')}")
    else:
        rows.append("- none carried out")
    for key, reason in sorted(validated.reasons.items()):
        if key.startswith("rename:") and key.split("->")[-1] not in validated.renames.values():
            rows.append(f"- `{key}` (distinct) — {reason}")
    rows.append("")
    return "\n".join(rows)


def merge(a_text: str, b_text: str, doc: dict, expected: Optional[dict] = None) -> Outcome:
    """Validate then apply. Raises MergeDecisionError; never merges an invalid doc.

    The candidate paths recorded in the document travel through to the outcome,
    so a rationale (and any later re-application) names exactly the files that
    were decided on instead of re-deriving them from the caller's argv.
    """
    validated = validate(doc, a_text, b_text, expected=expected)
    result = apply_merge(a_text, b_text, validated.decisions, renames=validated.renames)
    paths = dict(validated.candidate_paths)
    for side, fallback in (("a", "a"), ("b", "b")):
        paths.setdefault(side, ((expected or {}).get("candidates") or {}).get(side, fallback))
    return Outcome(
        result_sdl=result,
        result_sha256=sha256_text(result),
        decision_hash=validated.decision_hash,
        candidate_hashes=validated.candidate_hashes,
        diff_sha256=validated.diff_sha256,
        reasons=validated.reasons,
        renames=validated.renames,
        decisions=validated.decisions,
        rationale_md=build_rationale(validated, result, paths),
        candidate_paths=paths,
    )


def merge_text(a_text: str, b_text: str, decisions_text: str,
               expected: Optional[dict] = None) -> Outcome:
    doc = load_document(decisions_text)
    return merge(a_text, b_text, doc, expected=expected)


# ── compatibility route ──────────────────────────────────────────────────

def parse_decisions(text: str, a_text: str = None, b_text: str = None):
    """Legacy-shaped entry point: returns `(decisions, renames)`.

    Only the structured v1 JSON document is accepted. A pre-GQL-09 line/prose
    decision file raises instead of being interpreted; that interpretation is
    what GQL-09 removed.
    """
    doc = load_document(text)
    if a_text is None or b_text is None:
        raise MergeDecisionError(
            "malformed",
            "structured decisions must be validated against both candidates: "
            "call validate()/merge() with the candidate texts",
        )
    validated = validate(doc, a_text, b_text)
    return validated.decisions, validated.renames


# ── CLI ──────────────────────────────────────────────────────────────────

def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise MergeDecisionError("malformed", f"cannot read {path}: {exc}")


def _emit(payload: dict, code: int) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return code


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    def add_pair(p):
        p.add_argument("--a", required=True)
        p.add_argument("--b", required=True)

    p_prop = sub.add_parser("propose", help="proposed rename pairs for two candidates")
    add_pair(p_prop)
    p_prop.add_argument("--json", action="store_true")

    p_schema = sub.add_parser(
        "json-schema",
        help="write the loose structural JSON Schema for `lev run --output-schema`",
    )
    p_schema.add_argument("--out", help="write here; default stdout")

    p_diff = sub.add_parser("diff-task", help="write the compact diff a lane decides on")
    add_pair(p_diff)
    p_diff.add_argument("--out", required=True)

    p_val = sub.add_parser("validate", help="validate a decision document")
    add_pair(p_val)
    p_val.add_argument("--decisions", required=True)
    p_val.add_argument("--diff-file")

    p_merge = sub.add_parser("merge", help="validate and apply; write the merged schema")
    add_pair(p_merge)
    p_merge.add_argument("--decisions", required=True)
    p_merge.add_argument("--diff-file")
    p_merge.add_argument("--out", required=True)
    p_merge.add_argument("--rationale")

    args = parser.parse_args(argv)

    try:
        if args.mode == "json-schema":
            schema = json.dumps(decision_json_schema(), indent=2, sort_keys=True)
            if getattr(args, "out", None):
                with open(args.out, "w", encoding="utf-8") as handle:
                    handle.write(schema + "\n")
                return _emit({"status": "passed", "out": args.out,
                              "sha256": sha256_text(schema + "\n")}, 0)
            print(schema)
            return 0

        a_text, b_text = _read(args.a), _read(args.b)
        diff_text = _read(args.diff_file) if getattr(args, "diff_file", None) else None
        expected = {
            "diff_text": diff_text,
            "candidates": {"a": args.a, "b": args.b},
        }
        if args.mode == "propose":
            diff = diff_types(a_text, b_text)
            pairs = propose_renames(diff)
            payload = {
                "status": "passed",
                "a": {"path": args.a, "sha256": sha256_text(a_text)},
                "b": {"path": args.b, "sha256": sha256_text(b_text)},
                "divergent": sorted(diff.divergent),
                "proposed_renames": [{"from": x, "to": y} for x, y in pairs],
            }
            return _emit(payload, 0)

        if args.mode == "diff-task":
            from harnessql_driver import compact_diff_text  # local import: no cycle

            text = compact_diff_text(a_text, b_text, {"a": args.a, "b": args.b},
                                     diff_sha_placeholder=None)
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text)
            return _emit({"status": "passed", "out": args.out,
                          "diff_sha256": sha256_text(text)}, 0)

        doc = load_document(_read(args.decisions))

        if args.mode == "validate":
            validated = validate(doc, a_text, b_text, expected=expected)
            return _emit({
                "status": "passed",
                "decision_hash": validated.decision_hash,
                "types": len(validated.decisions),
                "renames": sorted(validated.renames.items()),
                "candidate_hashes": validated.candidate_hashes,
                "candidate_paths": validated.candidate_paths,
                "diff_sha256": validated.diff_sha256,
            }, 0)

        outcome = merge(a_text, b_text, doc, expected=expected)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(outcome.result_sdl)
        if args.rationale:
            with open(args.rationale, "w", encoding="utf-8") as handle:
                handle.write(outcome.rationale_md)
        return _emit({
            "status": outcome.status,
            "out": args.out,
            "rationale": args.rationale,
            "result_sha256": outcome.result_sha256,
            "decision_hash": outcome.decision_hash,
            "candidate_hashes": outcome.candidate_hashes,
            "candidate_paths": outcome.candidate_paths,
            "diff_sha256": outcome.diff_sha256,
        }, 0)

    except MergeDecisionError as exc:
        return _emit({"status": "rejected", **exc.as_dict()}, 1)
    except Exception as exc:  # noqa: BLE001 - blocked, not a decision defect
        return _emit({"status": "blocked", "code": "malformed", "message": str(exc)}, 2)


if __name__ == "__main__":
    sys.exit(main())
