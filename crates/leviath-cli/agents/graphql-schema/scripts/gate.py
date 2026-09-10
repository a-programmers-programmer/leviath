"""Static GraphQL contract gate; no model/provider calls or server execution.

CLI: python gate.py check|accept|verify [--root graphql-contract]
Accept additionally requires --review PATH. Installed tool supplies JSON argv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

VERSION = 1
RUBRIC = ("domain", "client", "authorization", "performance", "evolution")
MAX_BYTES = 2_000_000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def meaningful(value):
    return isinstance(value, str) and len(value.strip()) >= 8


def check(root):
    from graphql import (
        build_schema, parse, validate, validate_schema, get_named_type,
        is_object_type, is_interface_type, is_input_object_type, is_enum_type,
        is_list_type, is_non_null_type, is_scalar_type, TypeInfo, TypeInfoVisitor,
        Visitor, DocumentNode, OperationDefinitionNode, FragmentDefinitionNode,
    )
    from graphql.execution.values import get_variable_values
    from graphql.utilities import find_breaking_changes, find_dangerous_changes

    require(root.is_dir() and not root.is_symlink(), "Contract directory missing or symlinked")
    files = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"Symlink is not a contract artifact: {path}")
        if path.is_file() and path.relative_to(root).as_posix() != "accepted.json":
            data = path.read_bytes()
            require(len(data) <= MAX_BYTES, f"Artifact too large: {path}")
            files[path.relative_to(root).as_posix()] = data
    require(sum(map(len, files.values())) <= 8 * MAX_BYTES, "Contract exceeds 16 MB")
    hashes = {name: digest(data) for name, data in files.items()}
    fingerprint = digest(canonical(hashes))
    errors = []

    def need(condition, message):
        if not condition:
            errors.append(message)

    def obj(name):
        require(name in files, f"Missing {name}")
        return json.loads(files[name])

    contract = obj("contract.json")
    require(contract.get("version") == VERSION, "Unsupported contract version")
    require(contract.get("mode") in ("new", "evolve"), "mode must be new or evolve")
    for name in ("requirements.md", "design.md", "handoff.md", "schema.graphql"):
        require(name in files and meaningful(files[name].decode()), f"Missing or empty {name}")
    need(contract.get("open_questions") == [], "Unresolved open_questions block acceptance")
    require(isinstance(contract.get("assumptions"), list), "assumptions must be a list")
    schema = build_schema(files["schema.graphql"].decode())
    schema_errors = [str(e) for e in validate_schema(schema)]
    require(not schema_errors, "Invalid SDL: " + "; ".join(schema_errors))

    fields = {}
    for name, typ in schema.type_map.items():
        if name.startswith("__") or name in ("String", "ID", "Int", "Float", "Boolean"):
            continue
        need(meaningful(typ.description), f"Description required: {name}")
        need(bool(re.fullmatch(r"[A-Z][A-Za-z0-9]*", name)), f"Use PascalCase type names: {name}")
        if is_object_type(typ) or is_interface_type(typ) or is_input_object_type(typ):
            for fname, field in typ.fields.items():
                coord = f"{name}.{fname}"
                need(bool(re.fullmatch(r"[a-z][A-Za-z0-9]*", fname)), f"Use camelCase fields: {coord}")
                need(meaningful(field.description), f"Description required: {coord}")
                if is_input_object_type(typ):
                    continue
                fields[coord] = field
                for argname, arg in field.args.items():
                    need(meaningful(arg.description), f"Argument description required: {coord}({argname}:)")
        if is_enum_type(typ):
            for value, enum in typ.values.items():
                need(bool(re.fullmatch(r"[A-Z][A-Z0-9_]*", value)), f"Use UPPER_SNAKE_CASE enum: {name}.{value}")
                need(meaningful(enum.description), f"Enum description required: {name}.{value}")
        if is_scalar_type(typ):
            policy = contract.get("scalars", {}).get(name, {})
            for key in ("format", "validation"):
                need(meaningful(policy.get(key)), f"Custom scalar {name} needs {key}")

    policies = contract.get("fields", {})
    need(set(policies) == set(fields), "fields must cover every output field coordinate exactly")
    collection_coords = set()
    for coord, field in fields.items():
        policy = policies.get(coord, {})
        for key in ("authorization", "nullability", "source", "batching"):
            need(meaningful(policy.get(key)), f"{coord} needs {key} policy")
        outer = field.type.of_type if is_non_null_type(field.type) else field.type
        connection = get_named_type(field.type).name.endswith("Connection")
        if is_list_type(outer) or connection:
            collection_coords.add(coord)
            collection = contract.get("collections", {}).get(coord, {})
            need(type(collection.get("max_items")) is int and 0 < collection["max_items"] <= 10000,
                 f"{coord} needs positive max_items <= 10000")
            need(meaningful(collection.get("enforcement")), f"{coord} needs collection enforcement")
            need(collection.get("kind") in ("bounded", "cursor"), f"{coord} needs bounded or cursor kind")
            if collection.get("kind") == "cursor":
                need(connection, f"{coord}: cursor collection must return a Connection")
                need("first" in field.args and str(field.args["first"].type) in ("Int", "Int!"), f"{coord} needs first: Int")
                need("after" in field.args and str(field.args["after"].type) in ("String", "ID"), f"{coord} needs nullable after: String or ID")
                for key in ("order", "tie_breaker", "cursor_scope"):
                    need(meaningful(collection.get(key)), f"{coord} needs {key}")
                target = get_named_type(field.type)
                target_fields = getattr(target, "fields", {})
                need("edges" in target_fields and "pageInfo" in target_fields, f"{coord}: connection needs edges and pageInfo")
                if "edges" in target_fields:
                    edge_type = target_fields["edges"].type
                    edge_type = edge_type.of_type if is_non_null_type(edge_type) else edge_type
                    edge_fields = getattr(get_named_type(edge_type), "fields", {})
                    need(is_list_type(edge_type) and "node" in edge_fields and "cursor" in edge_fields,
                         f"{coord}: edges must be a list with node and cursor")
                if "pageInfo" in target_fields:
                    page_fields = getattr(get_named_type(target_fields["pageInfo"].type), "fields", {})
                    need("hasNextPage" in page_fields and str(page_fields["hasNextPage"].type) == "Boolean!"
                         and "endCursor" in page_fields and str(page_fields["endCursor"].type) in ("String", "ID"),
                         f"{coord}: pageInfo needs hasNextPage: Boolean! and nullable endCursor")
    need(set(contract.get("collections", {})) == collection_coords, "collections must cover all list/connection fields exactly")

    mutations = {} if schema.mutation_type is None else schema.mutation_type.fields
    expected_mutations = {f"{schema.mutation_type.name}.{n}" for n in mutations}
    need(set(contract.get("mutations", {})) == expected_mutations, "mutations must cover mutation coordinates exactly")
    for coord in expected_mutations:
        for key in ("intent", "idempotency", "concurrency", "errors"):
            need(meaningful(contract["mutations"].get(coord, {}).get(key)), f"{coord} needs {key}")
    for key in ("demand_control", "tenant_isolation", "authorization_tests", "performance_tests", "schema_codegen", "subscriptions"):
        need(meaningful(contract.get("runtime", {}).get(key)), f"runtime needs {key}")

    operation_files = [n for n in files if n.startswith("operations/") and n.endswith(".graphql")]
    require(operation_files, "No client operation documents")
    definitions = []
    for name in operation_files:
        definitions.extend(parse(files[name].decode()).definitions)
    document = DocumentNode(definitions=tuple(definitions))
    errors.extend(str(e) for e in validate(schema, document))
    operations = {}
    fragments = [d for d in definitions if isinstance(d, FragmentDefinitionNode)]
    for operation in definitions:
        if isinstance(operation, OperationDefinitionNode):
            need(operation.name is not None, "All operations must be named")
            if operation.name:
                operations[operation.name.value] = operation
    require(operations, "No named operations")

    # Track actual selections through the operation's transitive fragment closure.
    from graphql import visit, FragmentSpreadNode
    fragment_map = {f.name.value: f for f in fragments}

    def selected(operation):
        reachable = {}
        def expand(node):
            if isinstance(node, FragmentSpreadNode):
                name = node.name.value
                if name in fragment_map and name not in reachable:
                    reachable[name] = fragment_map[name]
                    expand(fragment_map[name])
            for key in getattr(node, "keys", ()):
                value = getattr(node, key, None)
                if isinstance(value, tuple):
                    for child in value:
                        expand(child)
                elif hasattr(value, "kind"):
                    expand(value)
        expand(operation)
        info = TypeInfo(schema)
        coords = set()
        class Coordinates(Visitor):
            def enter_field(self, node, *_):
                parent = info.get_parent_type()
                if parent and not node.name.value.startswith("__"):
                    coords.add(f"{parent.name}.{node.name.value}")
        visit(DocumentNode(definitions=(operation, *reachable.values())), TypeInfoVisitor(info, Coordinates()))
        return coords
    selections = {name: selected(op) for name, op in operations.items()}
    requirements = contract.get("requirements", [])
    require(isinstance(requirements, list) and requirements, "No application requirements")
    seen = set()
    mapped = set()
    for req in requirements:
        require(isinstance(req, dict), "Requirement must be an object")
        rid = req.get("id")
        need(isinstance(rid, str) and bool(rid) and rid not in seen, "Missing or duplicate requirement id")
        seen.add(rid)
        need(meaningful(req.get("description")), f"{rid}: missing description")
        names = req.get("operations", [])
        require(isinstance(names, list), f"{rid}: operations must be a list")
        need(bool(names) and all(n in operations for n in names), f"{rid}: missing or unknown client operation")
        mapped.update(names)
        coords = req.get("coordinates", [])
        need(bool(coords) and all(c in fields for c in coords), f"{rid}: unknown or missing field coordinates")
        used = set().union(*(selections.get(n, set()) for n in names))
        need(set(coords) <= used, f"{rid}: mapped operations do not select required coordinates")
    need(mapped == set(operations), "Every operation must trace to an application requirement")
    root_coords = {f"{root.name}.{name}" for root in (schema.query_type, schema.mutation_type, schema.subscription_type)
                   if root for name in root.fields}
    used_coords = set().union(*selections.values())
    need(root_coords <= used_coords, f"Client operations omit root fields: {sorted(root_coords - used_coords)}")

    cases = obj("variables.json")
    require(isinstance(cases, list) and cases, "variables.json must have coercion cases")
    valid_cases = set()
    for case in cases:
        name = case.get("operation")
        need(name in operations, f"Unknown case operation: {name}")
        require(isinstance(case.get("variables"), dict), "Case variables must be an object")
        require(case.get("expected") in ("valid", "invalid"), "Case expected must be valid or invalid")
        if name in operations:
            result = get_variable_values(schema, operations[name].variable_definitions or (), case["variables"])
            ok = not isinstance(result, list)
            need(ok == (case["expected"] == "valid"), f"Unexpected variable coercion: {name}")
            if ok and case["expected"] == "valid":
                valid_cases.add(name)
    need(valid_cases == set(operations), "Every operation needs valid example variables")

    changes = []
    if contract["mode"] == "evolve":
        require("baseline.graphql" in files, "Evolution requires baseline.graphql")
        baseline = build_schema(files["baseline.graphql"].decode())
        require(not validate_schema(baseline), "Baseline SDL is invalid")
        changes = [f"{c.type.name}: {c.description}" for c in
                   (*find_breaking_changes(baseline, schema), *find_dangerous_changes(baseline, schema))]
        # v1 deliberately requires additive evolution. No model-written waiver.
        need(not changes, "Breaking or dangerous changes require a separate migration: " + "; ".join(changes))
    else:
        need("baseline.graphql" not in files, "Baseline present: use evolve mode")
    return {"version": VERSION, "status": "checked" if not errors else "blocked",
            "fingerprint": fingerprint, "files": hashes, "errors": errors,
            "changes": changes, "operations": sorted(operations), "requirements": len(requirements)}


def review_ok(review, fingerprint):
    require(isinstance(review, dict), "Review must be an object")
    require(review.get("fingerprint") == fingerprint, "Review is stale or for another contract")
    require(review.get("verdict") == "approve" and review.get("blocking_findings") == [], "Review did not approve")
    for key in RUBRIC:
        item = review.get("rubric", {}).get(key, {})
        require(item.get("pass") is True and meaningful(item.get("evidence")), f"Review missing evidence: {key}")


def dispatch(request):
    op = request.get("op", "check")
    require(op in ("check", "accept", "verify"), "Unknown gate operation")
    root = Path(request.get("root", "graphql-contract"))
    require(not root.is_symlink(), "Contract root cannot be a symlink")
    receipt = root / "accepted.json"
    if op in ("check", "accept") and receipt.exists():
        require(not receipt.is_symlink(), "Receipt cannot be symlinked")
        receipt.unlink()  # A failed new attempt must not leave an old success marker.
    result = check(root)
    if op == "check" or result["status"] != "checked":
        return result
    if op == "accept":
        review = request.get("review")
        review_ok(review, result["fingerprint"])
        result.update(status="accepted", review=review)
        fd, name = tempfile.mkstemp(dir=root, prefix=".accept-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(canonical(result) + b"\n")
            os.replace(name, receipt)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return result
    require(receipt.is_file() and not receipt.is_symlink(), "No accepted contract")
    if request.get("expected_fingerprint") is not None:
        require(request["expected_fingerprint"] == result["fingerprint"],
                "Contract differs from the parent pipeline expected fingerprint")
    accepted = json.loads(receipt.read_bytes())
    require(accepted.get("version") == VERSION and accepted.get("status") == "accepted", "Invalid receipt")
    require(accepted.get("fingerprint") == result["fingerprint"] and accepted.get("files") == result["files"],
            "Contract changed after acceptance; rerun the workflow")
    review_ok(accepted.get("review"), result["fingerprint"])
    return {**result, "status": "accepted", "review": accepted["review"]}


def main():
    try:
        if len(sys.argv) == 2 and sys.argv[1].startswith("{"):
            request = json.loads(sys.argv[1])
        else:
            parser = argparse.ArgumentParser(description=__doc__)
            parser.add_argument("op", choices=["check", "accept", "verify"])
            parser.add_argument("--root", default="graphql-contract")
            parser.add_argument("--review")
            parser.add_argument("--expected-fingerprint", help="Parent pipeline contract identity required by verify")
            args = parser.parse_args()
            request = {"op": args.op, "root": args.root}
            if args.expected_fingerprint:
                require(args.op == "verify", "--expected-fingerprint is for verify")
                request["expected_fingerprint"] = args.expected_fingerprint
            if args.review:
                request["review"] = json.loads(Path(args.review).read_text())
        result = dispatch(request)
    except Exception as error:
        result = {"version": VERSION, "status": "blocked", "errors": [f"{type(error).__name__}: {error}"]}
    print(json.dumps(result, sort_keys=True))
    # Embedded Rhai calls need structured failures as tool content. CLI needs an exit gate.
    if not (len(sys.argv) == 2 and sys.argv[1].startswith("{")):
        sys.exit(0 if result["status"] in ("checked", "accepted") else 1)


if __name__ == "__main__":
    main()
