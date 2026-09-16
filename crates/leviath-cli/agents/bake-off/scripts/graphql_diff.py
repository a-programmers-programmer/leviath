#!/usr/bin/env python3
"""
graphql_diff — deterministic GraphQL schema diff/merge engine.

Parses GraphQL SDL to extract top-level type definitions, computes
type-level and field-level deltas between two schemas, and applies
merge decisions to produce a reconciled output schema.

Designed for the bakeoff merge level: instead of feeding two ~43KB
schemas to a flash lane (which exhausts the iteration budget), we
compute a compact diff and ask the lane to decide only on divergent
types. The deterministic assembler then applies those decisions.
"""

import copy
import re
import textwrap
from dataclasses import dataclass, field
from typing import Optional

# AST imports for the merge engine (graphql-core >= 3)
from graphql import parse as _gql_parse
from graphql.language import print_ast as _gql_print_ast
from graphql.language import visit as _gql_visit, Visitor as _GqlVisitor
from graphql.language.ast import (
    DocumentNode,
    NamedTypeNode,
    NameNode,
)

# ── parsing helpers (kept for diff/backward compat) ──────────────────────

# Match a top-level type/interface/enum/union/input/scalar/directive/schema block
# We capture the kind, the name, and the full body (including braces).
# This regex handles nested braces by counting depth.
_KIND_RE = re.compile(
    r'(?:^|\n)\s*'
    r'(type|interface|enum|union|input|scalar|directive|schema)\s+'
    r'([A-Za-z_]\w*)'
    r'([^{]*)'  # everything up to the opening brace (directives, implements, extends)
    r'(\{)',
    re.MULTILINE
)

# For scalar/directive/schema — single-line or extends/implements
_SCALAR_LIKE_RE = re.compile(
    r'(?:^|\n)\s*'
    r'(scalar|directive|schema)\s+'
    r'([A-Za-z_]\w*)'
    r'[^\n{]*\n',
    re.MULTILINE
)


def _find_matching_brace(text: str, start: int) -> int:
    """Return the index of the closing brace matching the one at `start`."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"Unmatched brace starting at position {start}")


@dataclass
class TypeDef:
    """A parsed top-level type definition."""
    kind: str          # type, interface, enum, union, input, scalar, directive, schema
    name: str
    body: str          # full text of the definition including header
    start: int = 0     # byte offset in source
    end: int = 0       # byte offset in source

    # Parsed sub-structure (populated on demand)
    fields: dict[str, str] = field(default_factory=dict)   # name -> full field line(s)
    enum_values: list[str] = field(default_factory=list)
    union_members: list[str] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)
    field_order: list[str] = field(default_factory=list)   # preserve order


def parse_schema(text: str) -> list[TypeDef]:
    """Parse a GraphQL SDL string into a list of top-level TypeDefs."""
    types = []

    # First pass: find all top-level definitions with their spans
    # We need to handle both brace-delimited types and non-brace types (scalar, union, directive)
    # Strategy: find all declaration starts, then determine the span for each.

    # Build a combined pattern that captures all top-level kinds
    _ALL_KINDS = re.compile(
        r'(?:^|\n)\s*'
        r'(type|interface|enum|union|input|scalar|directive|schema)\s+'
        r'([A-Za-z_]\w*)',
        re.MULTILINE
    )

    matches = list(_ALL_KINDS.finditer(text))
    seen_names = set()  # dedupe: keep first occurrence of each name

    for i, m in enumerate(matches):
        kind = m.group(1)
        name = m.group(2)
        decl_start = m.start()

        # Skip duplicate type names (e.g. two `enum ProposalInputRule` in the same file)
        if name in seen_names:
            continue
        seen_names.add(name)

        # Determine the end of this definition
        if kind in ('scalar', 'directive'):
            # These are single-line: find the next newline after the declaration
            nl = text.find('\n', m.end())
            end = nl + 1 if nl >= 0 else len(text)
            body = text[decl_start:end]
            td = TypeDef(kind=kind, name=name, body=body, start=decl_start, end=end)
            types.append(td)
            continue

        if kind == 'union':
            # Union: "union Name = A | B | C" — single line, or
            # "union Name =\n    A\n  | B" — multi-line with continuation.
            # Find the = sign
            eq_idx = text.find('=', m.end())
            if eq_idx < 0:
                # Malformed, skip
                continue

            # Collect all lines that belong to this union definition.
            # Use text starting from decl_start (including leading whitespace/newlines).
            seg = text[decl_start:]
            lines = seg.split('\n')
            body_lines = []

            # Skip leading blank lines (preserve them for formatting).
            # There may be 0, 1, or more blank lines before the declaration.
            li = 0
            while li < len(lines) and lines[li].strip() == '':
                body_lines.append(lines[li])
                li += 1

            # The next non-empty line IS the union declaration (e.g.
            # "union ProposalChange = Proposal | ... | InvalidProposalInput").
            # We must include it unconditionally — the keyword check in the
            # continuation loop below would reject it because it starts with
            # "union", so we always add the declaration line before the loop.
            if li < len(lines):
                body_lines.append(lines[li])
                li += 1

            # Collect continuation lines (| prefix for multi-line style).
            for line in lines[li:]:
                stripped = line.strip()
                if stripped.startswith('|') or (stripped and not stripped.startswith('#')
                                                 and not stripped.startswith('"""')
                                                 and not stripped.startswith("'''")
                                                 and not (stripped.startswith('type')
                                                          or stripped.startswith('interface')
                                                          or stripped.startswith('enum')
                                                          or stripped.startswith('union')
                                                          or stripped.startswith('input')
                                                          or stripped.startswith('scalar')
                                                          or stripped.startswith('directive')
                                                          or stripped.startswith('schema')
                                                          or stripped.startswith('extend')
                                                          )):
                    body_lines.append(line)
                else:
                    break
            body = '\n'.join(body_lines)
            # Compute end position: decl_start + len(body), then include trailing \n if present
            end = decl_start + len(body)
            if end < len(text) and text[end] == '\n':
                end += 1
            td = TypeDef(kind=kind, name=name, body=body, start=decl_start, end=end)
            _populate_fields(td)
            types.append(td)
            continue

        # type, interface, enum, input, schema — all have braces
        brace_start = text.find('{', m.end())
        if brace_start < 0:
            # No brace found — might be a malformed declaration, skip
            continue

        try:
            brace_end = _find_matching_brace(text, brace_start)
        except ValueError:
            continue

        body = text[decl_start:brace_end + 1]
        td = TypeDef(kind=kind, name=name, body=body, start=decl_start, end=brace_end + 1)
        _populate_fields(td)
        types.append(td)

    return types


def _populate_fields(td: TypeDef):
    """Extract fields, enum values, union members, implements from a TypeDef body."""
    if td.kind == 'union':
        # Union: "union Name = A | B | C" (single-line) or
        # "union Name =\n    A\n  | B" (multi-line)
        eq_idx = td.body.find('=')
        if eq_idx >= 0:
            members_str = td.body[eq_idx + 1:]
            # Split on | and newlines to collect all member names
            parts = re.split(r'[\|\n]', members_str)
            td.union_members = [p.strip() for p in parts if p.strip()]
        return

    if td.kind in ('scalar', 'directive'):
        return

    # Find the inner content (between braces)
    inner_match = re.search(r'\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', td.body, re.DOTALL)
    if not inner_match:
        return
    inner = inner_match.group(1)

    if td.kind == 'enum':
        # Extract enum values: lines with just an identifier (possibly with @deprecated)
        for line in inner.split('\n'):
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            # Remove trailing comments
            val_match = re.match(r'([A-Z_][A-Z_0-9]*)', stripped)
            if val_match:
                td.enum_values.append(val_match.group(1))
        return

    if td.kind in ('type', 'interface', 'input', 'schema'):
        # Extract implements for type/interface
        header = td.body[:td.body.index('{')]
        impl_match = re.search(r'implements\s+(.*?)\s*$', header)
        if impl_match:
            td.implements = [i.strip() for i in impl_match.group(1).split('&') if i.strip()]

        # Parse fields: each field is a name with optional args, type, and directives
        # We use a simple line-based approach, merging multi-line fields
        lines = inner.split('\n')
        current_field_name = None
        current_field_lines = []
        for line in lines:
            stripped = line.strip()
            # Skip comments, docstrings, empty lines
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if current_field_name:
                    current_field_lines.append(line)
                continue

            # Check if this line starts a new field
            # A field starts with a name followed by ( or : or just name for directives
            field_match = re.match(r'([A-Za-z_]\w*)\s*([\(:])', stripped)
            if field_match:
                # Save previous field
                if current_field_name:
                    td.fields[current_field_name] = '\n'.join(current_field_lines)
                    td.field_order.append(current_field_name)
                current_field_name = field_match.group(1)
                current_field_lines = [line]
            elif current_field_name:
                current_field_lines.append(line)

        # Don't forget the last field
        if current_field_name:
            td.fields[current_field_name] = '\n'.join(current_field_lines)
            td.field_order.append(current_field_name)


def extract_type_names(text: str) -> set[str]:
    """Return the set of top-level type names in a GraphQL schema."""
    types = parse_schema(text)
    return {t.name for t in types}


# ── diff engine ──────────────────────────────────────────────────────────

@dataclass
class FieldDelta:
    """Describes a field-level difference for one type."""
    type_name: str
    added: list[str] = field(default_factory=list)      # fields in B not in A
    removed: list[str] = field(default_factory=list)    # fields in A not in B
    changed: list[str] = field(default_factory=list)    # fields in both but different body
    enum_added: list[str] = field(default_factory=list)
    enum_removed: list[str] = field(default_factory=list)
    union_added: list[str] = field(default_factory=list)
    union_removed: list[str] = field(default_factory=list)
    implements_added: list[str] = field(default_factory=list)
    implements_removed: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed or
                    self.enum_added or self.enum_removed or
                    self.union_added or self.union_removed or
                    self.implements_added or self.implements_removed)


@dataclass
class SchemaDiff:
    """Complete diff between two GraphQL schemas."""
    only_a: list[str] = field(default_factory=list)       # types only in A
    only_b: list[str] = field(default_factory=list)       # types only in B
    both: list[str] = field(default_factory=list)         # types in both (no field diffs)
    divergent: dict[str, FieldDelta] = field(default_factory=dict)  # types with field-level diffs
    a_types: dict[str, TypeDef] = field(default_factory=dict)
    b_types: dict[str, TypeDef] = field(default_factory=dict)

    @property
    def divergent_names(self) -> list[str]:
        return list(self.divergent.keys())

    @property
    def is_empty(self) -> bool:
        return not (self.only_a or self.only_b or self.divergent)

    def summary(self) -> str:
        """Compact human-readable summary suitable for a flash lane task."""
        lines = []
        if self.only_a:
            lines.append(f"Types only in A: {', '.join(sorted(self.only_a))}")
        if self.only_b:
            lines.append(f"Types only in B: {', '.join(sorted(self.only_b))}")
        if self.both:
            lines.append(f"Identical types: {len(self.both)}")
        if self.divergent:
            lines.append(f"\nDivergent types ({len(self.divergent)}):")
            for name, delta in sorted(self.divergent.items()):
                parts = []
                if delta.removed:
                    parts.append(f"fields-removed={delta.removed}")
                if delta.added:
                    parts.append(f"fields-added={delta.added}")
                if delta.changed:
                    parts.append(f"fields-changed={delta.changed}")
                if delta.enum_added:
                    parts.append(f"enum-values-added={delta.enum_added}")
                if delta.enum_removed:
                    parts.append(f"enum-values-removed={delta.enum_removed}")
                if delta.union_added:
                    parts.append(f"union-members-added={delta.union_added}")
                if delta.union_removed:
                    parts.append(f"union-members-removed={delta.union_removed}")
                if delta.implements_added:
                    parts.append(f"implements-added={delta.implements_added}")
                if delta.implements_removed:
                    parts.append(f"implements-removed={delta.implements_removed}")
                lines.append(f"  {name}: {'; '.join(parts)}")
        return '\n'.join(lines)


def diff_types(a_text: str, b_text: str) -> SchemaDiff:
    """Compute type-level and field-level diff between two GraphQL schemas."""
    a_types = parse_schema(a_text)
    b_types = parse_schema(b_text)

    a_map = {t.name: t for t in a_types}
    b_map = {t.name: t for t in b_types}

    a_names = set(a_map.keys())
    b_names = set(b_map.keys())

    diff = SchemaDiff(
        only_a=sorted(a_names - b_names),
        only_b=sorted(b_names - a_names),
        a_types=a_map,
        b_types=b_map,
    )

    common = a_names & b_names
    for name in sorted(common):
        at = a_map[name]
        bt = b_map[name]
        delta = _compute_field_delta(at, bt)
        if delta.is_empty:
            diff.both.append(name)
        else:
            diff.divergent[name] = delta

    return diff


def _compute_field_delta(at: TypeDef, bt: TypeDef) -> FieldDelta:
    """Compute field-level delta between two versions of the same type."""
    delta = FieldDelta(type_name=at.name)

    if at.kind != bt.kind:
        # Kind changed — treat as full replacement
        delta.changed.append("__KIND_CHANGED__")
        return delta

    # Compare implements
    delta.implements_added = [i for i in bt.implements if i not in at.implements]
    delta.implements_removed = [i for i in at.implements if i not in bt.implements]

    # Compare enum values
    if at.kind == 'enum':
        delta.enum_added = [v for v in bt.enum_values if v not in at.enum_values]
        delta.enum_removed = [v for v in at.enum_values if v not in bt.enum_values]
        return delta

    # Compare union members
    if at.kind == 'union':
        delta.union_added = [m for m in bt.union_members if m not in at.union_members]
        delta.union_removed = [m for m in at.union_members if m not in bt.union_members]
        return delta

    # Compare fields for type/interface/input
    a_fields = set(at.fields.keys())
    b_fields = set(bt.fields.keys())

    delta.added = sorted(b_fields - a_fields)
    delta.removed = sorted(a_fields - b_fields)

    for fname in sorted(a_fields & b_fields):
        # Normalize whitespace for comparison
        a_body = _normalize_field(at.fields[fname])
        b_body = _normalize_field(bt.fields[fname])
        if a_body != b_body:
            delta.changed.append(fname)

    return delta


def _normalize_field(field_text: str) -> str:
    """Normalize a field definition for comparison — strip comments and docstrings."""
    # First, remove all docstring blocks ("""...""")
    # Simple approach: remove lines that are part of docstrings
    in_docstring = False
    clean_lines = []
    for line in field_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if in_docstring:
                in_docstring = False  # closing
            elif stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                # Single-line docstring
                pass
            else:
                in_docstring = True  # opening
            continue
        if in_docstring:
            continue
        if stripped.startswith('#'):
            continue
        if stripped:
            clean_lines.append(stripped)
    return ' '.join(clean_lines)


# ══════════════════════════════════════════════════════════════════════════
# AST-BASED MERGE — the replacement for the broken string-pasting approach.
# ══════════════════════════════════════════════════════════════════════════

# Sentinel used as a map key for the schema definition (which has no name).
_SCHEMA_SENTINEL = object()


class _RenameVisitor(_GqlVisitor):
    """Visitor that rewrites NamedType references."""

    def __init__(self, renames: dict):
        super().__init__()
        self.renames = renames

    def enter_named_type(self, node, key, parent, path, ancestors):
        if node.name.value in self.renames:
            return NamedTypeNode(name=NameNode(value=self.renames[node.name.value]))
        return None


def _apply_renames_ast(doc: DocumentNode, renames: dict) -> DocumentNode:
    """Return a new DocumentNode with all named-type references rewritten."""
    if not renames:
        return doc
    return _gql_visit(doc, _RenameVisitor(renames))


def _definition_name(d):
    """Return the string name for a definition, or None for schema definitions."""
    if hasattr(d, 'name') and d.name and d.name.value:
        return d.name.value
    return None


# ── merge engine ─────────────────────────────────────────────────────────

@dataclass
class MergeDecision:
    """A structured decision for ONE divergent type.

    `sources` maps every decided key of the merged type - field names, enum
    values or union members, depending on the type's kind - to the candidate it
    comes from: ``'a'`` or ``'b'``. `implements` does the same for interface
    names. Both maps are total over the permitted key set; totality and every
    other rule live in :mod:`merge_decisions`, which is the only authority on
    what a legal decision is.

    There is deliberately no way to express a decision in prose here. The
    pre-GQL-09 engine guessed a base side and a field set out of the English in
    `reason` ("adopt B's fields", "restore X from A"); that produced merges no
    reviewer could reproduce from the decision record.
    """

    type_name: str
    action: str                                    # keep_a | keep_b | reconcile
    reason: str = ""
    sources: dict = field(default_factory=dict)
    implements: dict = field(default_factory=dict)


#: AST definition kind -> the attribute holding the keys a decision chooses
#: between. graphql-core names these `object_type_definition`, etc.
_AST_GROUP_ATTR = {
    'object_type_definition': 'fields',
    'interface_type_definition': 'fields',
    'input_object_type_definition': 'fields',
    'enum_type_definition': 'values',
    'union_type_definition': 'types',
}


def group_keys(td: TypeDef) -> list[str]:
    """The decided key names of a *regex-parsed* TypeDef (diff-side view)."""
    if td.kind in ('type', 'interface', 'input'):
        return list(td.fields.keys())
    if td.kind == 'enum':
        return list(td.enum_values)
    if td.kind == 'union':
        return list(td.union_members)
    return []


def _norm_body(text: str) -> str:
    """Whitespace/comment-insensitive body text, for structural rename proposals."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        lines.append(stripped)
    return ' '.join(lines)


def propose_renames(diff: 'SchemaDiff') -> list[tuple[str, str]]:
    """Propose (only-A name -> only-B name) pairs **structurally**.

    A pair is proposed when the two definitions have the same kind and the same
    normalized body, so the only difference is the name. This replaces the
    hard-coded domain guess list the pre-GQL-09 driver carried
    ("ResourceKind -> ProposalResource" and friends): a rename is now a checked
    structural fact, and a proposed pair still needs an explicit decision.
    """
    proposals = []
    for a_name in diff.only_a:
        a_def = diff.a_types[a_name]
        if a_def.kind in ('scalar', 'schema', 'directive'):
            continue
        a_body = _norm_body(a_def.body.replace(a_name, '@NAME@'))
        for b_name in diff.only_b:
            b_def = diff.b_types[b_name]
            if a_def.kind != b_def.kind:
                continue
            b_body = _norm_body(b_def.body.replace(b_name, '@NAME@'))
            if a_body == b_body:
                proposals.append((a_name, b_name))
    return sorted(proposals)


def _ast_keys(node, attr: str) -> list[str]:
    out = []
    for item in getattr(node, attr, None) or ():
        name = getattr(getattr(item, 'name', None), 'value', None)
        if name and name not in out:
            out.append(name)
    return out


def _ast_key_order(attr: str, a_def, b_def) -> list[str]:
    """A's order first, then B-only keys in B's order - the deterministic merge order."""
    keys = _ast_keys(a_def, attr)
    keys += [k for k in _ast_keys(b_def, attr) if k not in keys]
    return keys


def _reconcile_definition(a_def, b_def, decision: MergeDecision):
    """Build the reconciled AST node for one type from the decision's sources.

    Deterministic by construction: the key order is A-then-B-extra, and each key
    is taken from exactly the side the decision names. Nothing is inferred.
    """
    if b_def is None:
        return a_def
    attr = _AST_GROUP_ATTR.get(a_def.kind)
    base = copy.deepcopy(a_def)
    if attr is None:                     # scalar / schema: nothing keyed to merge
        return base

    a_nodes = {k: n for k, n in zip(_ast_keys(a_def, attr),
                                    getattr(a_def, attr) or ())}
    b_nodes = {k: n for k, n in zip(_ast_keys(b_def, attr),
                                    getattr(b_def, attr) or ())}
    merged = []
    for key in _ast_key_order(attr, a_def, b_def):
        source = decision.sources.get(key)
        if source == 'a' and key in a_nodes:
            merged.append(copy.deepcopy(a_nodes[key]))
        elif source == 'b' and key in b_nodes:
            merged.append(copy.deepcopy(b_nodes[key]))
        else:
            raise ValueError(
                f"reconcile decision for {decision.type_name!r} names source "
                f"{source!r} for {key!r}, which is not available on that side"
            )
    setattr(base, attr, tuple(merged))

    if attr == 'fields' and hasattr(base, 'interfaces'):
        a_ifaces = list(getattr(a_def, 'interfaces', None) or ())
        b_ifaces = list(getattr(b_def, 'interfaces', None) or ())
        by_name = {}
        for node in b_ifaces + a_ifaces:                 # A wins the same name
            by_name[node.name.value] = node
        kept = []
        for name in [n.name.value for n in a_ifaces] + \
                    [n.name.value for n in b_ifaces if n.name.value not in
                     {i.name.value for i in a_ifaces}]:
            source = decision.implements.get(name)
            if source in ('a', 'b'):
                kept.append(copy.deepcopy(by_name[name]))
            else:
                raise ValueError(
                    f"reconcile decision for {decision.type_name!r} has no source "
                    f"for interface {name!r}"
                )
        base.interfaces = tuple(kept)
    return base


def apply_merge(
    seed_a_text: str,
    seed_b_text: str,
    decisions: list,
    renames: Optional[dict] = None,
) -> str:
    """Apply validated decisions to produce a reconciled schema (AST-based).

    The output is always valid SDL because it is produced by
    :func:`graphql.language.print_ast` from a properly-constructed document. No
    string concatenation, no orphaned prose, no duplicate definitions.

    This function trusts its input: the decisions must already have passed
    :func:`merge_decisions.validate`. Callers that hold a decision *document*
    should call :func:`merge_decisions.merge` instead, which validates first.

    Parameters
    ----------
    seed_a_text / seed_b_text: the two candidates.
    decisions: the validated decisions for the divergent types.
    renames:   ``{old_name: new_name}`` for the renames decided as renames.
    """
    doc_a = _gql_parse(seed_a_text)
    doc_b = _gql_parse(seed_b_text)

    # --- 2. Name-indexed maps -----------------------------------------------
    a_by_name: dict = {}
    for d in doc_a.definitions:
        a_by_name[_definition_name(d) or _SCHEMA_SENTINEL] = d
    b_by_name: dict = {}
    for d in doc_b.definitions:
        b_by_name[_definition_name(d) or _SCHEMA_SENTINEL] = d

    all_renames = dict(renames or {})
    decision_map = {d.type_name: d for d in decisions}

    # --- 4. Assemble the merged definition list ----------------------------
    merged_defs: list = []
    emitted: set = set()
    renamed_from_a: set = set(all_renames.keys())

    for d in doc_a.definitions:
        name = _definition_name(d)
        key = name if name is not None else _SCHEMA_SENTINEL

        if name is not None and name in renamed_from_a:
            # Being renamed: B's definition is appended in step 6.
            continue

        if name is not None and name in decision_map:
            dec = decision_map[name]
            if dec.action == 'keep_b' and key in b_by_name:
                d = copy.deepcopy(b_by_name[key])
            elif dec.action == 'reconcile':
                d = _reconcile_definition(d, b_by_name.get(key), dec)

        emitted.add(key)
        merged_defs.append(copy.deepcopy(d))

    # --- 5. Append B-only definitions (not in A, not rename targets) ------
    rename_targets = set(all_renames.values())
    for d in doc_b.definitions:
        name = _definition_name(d)
        key = name if name is not None else _SCHEMA_SENTINEL
        if key in emitted:
            continue
        if name is not None and name in rename_targets:
            continue
        emitted.add(key)
        merged_defs.append(copy.deepcopy(d))

    # --- 6. Emit B versions of renamed types -------------------------------
    for old_name, new_name in sorted(all_renames.items()):
        if new_name in b_by_name and new_name not in emitted:
            emitted.add(new_name)
            merged_defs.append(copy.deepcopy(b_by_name[new_name]))

    # --- 7. Build the final document and serialize -------------------------
    merged_doc = DocumentNode(definitions=tuple(merged_defs))
    merged_doc = _apply_renames_ast(merged_doc, all_renames)
    return _gql_print_ast(merged_doc)


# ── compatibility route ──────────────────────────────────────────────────

def parse_decisions(text: str, a_text: Optional[str] = None,
                    b_text: Optional[str] = None):
    """Deprecated shim. Structured v1 JSON only; prose is rejected.

    Kept so an existing caller importing this name gets a *clear* rejection
    instead of a silently wrong merge. New code should use
    :mod:`merge_decisions` directly.
    """
    from merge_decisions import parse_decisions as _parse

    return _parse(text, a_text, b_text)

# ── test helpers ─────────────────────────────────────────────────────────

def _make_sample_schema_a() -> str:
    return textwrap.dedent("""\
    schema { query: Query }

    type Query {
      hello: String!
      version: Int!
    }

    type User {
      id: ID!
      name: String!
      email: String!
    }

    enum Role {
      ADMIN
      USER
    }

    interface Node {
      id: ID!
    }

    type OldOnly {
      field1: String
    }
    """)

def _make_sample_schema_b() -> str:
    return textwrap.dedent("""\
    schema { query: Query }

    type Query {
      hello: String!
      version: Int!
      goodbye: String
    }

    type User {
      id: ID!
      name: String!
      email: String
      avatar: String
    }

    enum Role {
      ADMIN
      USER
      GUEST
    }

    interface Node {
      id: ID!
      createdAt: DateTime!
    }

    type NewOnly {
      field2: Int
    }

    scalar DateTime
    """)


# ── tests ────────────────────────────────────────────────────────────────

def test_extract_types():
    a = _make_sample_schema_a()
    names = extract_type_names(a)
    assert names == {'Query', 'User', 'Role', 'Node', 'OldOnly'}, f"Got: {names}"
    print("PASS test_extract_types")


def test_diff_types():
    a = _make_sample_schema_a()
    b = _make_sample_schema_b()
    diff = diff_types(a, b)

    assert set(diff.only_a) == {'OldOnly'}, f"only_a: {diff.only_a}"
    assert set(diff.only_b) == {'NewOnly', 'DateTime'}, f"only_b: {diff.only_b}"
    assert 'Query' in diff.divergent
    assert 'User' in diff.divergent
    assert 'Role' in diff.divergent
    assert 'Node' in diff.divergent

    # Query: goodbye added
    assert diff.divergent['Query'].added == ['goodbye']
    # User: email changed (String! -> String), avatar added
    assert 'email' in diff.divergent['User'].changed or 'avatar' in diff.divergent['User'].added
    # Role: GUEST added
    assert diff.divergent['Role'].enum_added == ['GUEST']
    # Node: createdAt added
    assert diff.divergent['Node'].added == ['createdAt']

    print("PASS test_diff_types")
    print("Diff summary:")
    print(diff.summary())


def test_apply_merge_keep_a():
    a = _make_sample_schema_a()
    b = _make_sample_schema_b()
    decisions = [
        MergeDecision(type_name='Query', action='keep_a'),
        MergeDecision(type_name='User', action='keep_a'),
        MergeDecision(type_name='Role', action='keep_a'),
        MergeDecision(type_name='Node', action='keep_a'),
    ]
    merged = apply_merge(a, b, decisions)
    # Should contain OldOnly (from A) but not NewOnly or DateTime (from B, no decision)
    assert 'OldOnly' in merged
    # B-only types are appended automatically
    assert 'NewOnly' in merged
    assert 'DateTime' in merged
    # Query should NOT have goodbye (kept A)
    assert 'goodbye' not in merged
    # Also validate with graphql-core
    from graphql import build_schema
    build_schema(merged)
    print("PASS test_apply_merge_keep_a")


def test_apply_merge_keep_b():
    a = _make_sample_schema_a()
    b = _make_sample_schema_b()
    decisions = [
        MergeDecision(type_name='Query', action='keep_b'),
        MergeDecision(type_name='User', action='keep_b'),
        MergeDecision(type_name='Role', action='keep_b'),
        MergeDecision(type_name='Node', action='keep_b'),
    ]
    merged = apply_merge(a, b, decisions)
    # Query should have goodbye (kept B)
    assert 'goodbye' in merged
    # Role should have GUEST
    assert 'GUEST' in merged
    # Node should have createdAt
    assert 'createdAt' in merged
    # Also validate with graphql-core
    from graphql import build_schema
    build_schema(merged)
    print("PASS test_apply_merge_keep_b")


def test_parse_decisions():
    text = """TYPE Query: keep_a
    REASON: A's Query is simpler
    TYPE User: keep_b
    REASON: B's User has avatar
    TYPE Role: reconcile
    REASON: Both have value
    FIELDS: ADMIN, USER, GUEST
    RENAME ProposalChange -> ProposalResult: keep_b
    REASON: B's name is better
    """
    decisions, renames = parse_decisions(text)
    assert len(decisions) == 3
    assert decisions[0].type_name == 'Query'
    assert decisions[0].action == 'keep_a'
    assert decisions[1].type_name == 'User'
    assert decisions[1].action == 'keep_b'
    assert decisions[2].type_name == 'Role'
    assert decisions[2].action == 'reconcile'
    assert decisions[2].include_fields == ['ADMIN', 'USER', 'GUEST']
    assert renames == {'ProposalChange': 'ProposalResult'}
    print("PASS test_parse_decisions")


def test_real_candidates():
    """Test diff on the actual v0_L0L0 and v0_L0L1 candidate schemas."""
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import harnessql_paths
    bo = harnessql_paths.legacy_study() or harnessql_paths.agent_dir() / "study"
    bo = os.path.join(str(bo), 'bakeoff')
    a_path = os.path.join(bo, 'v0_L0L0.graphql')
    b_path = os.path.join(bo, 'v0_L0L1.graphql')

    if not os.path.exists(a_path) or not os.path.exists(b_path):
        print("SKIP test_real_candidates: candidate files not found")
        return

    a_text = open(a_path).read()
    b_text = open(b_path).read()

    diff = diff_types(a_text, b_text)

    print(f"\nReal candidate diff:")
    print(f"  Types only in A: {diff.only_a}")
    print(f"  Types only in B: {diff.only_b}")
    print(f"  Identical types: {len(diff.both)}")
    print(f"  Divergent types: {len(diff.divergent)}")
    for name, delta in sorted(diff.divergent.items()):
        print(f"    {name}: added={delta.added} removed={delta.removed} changed={delta.changed} "
              f"enum+={delta.enum_added} enum-={delta.enum_removed} "
              f"union+={delta.union_added} union-={delta.union_removed} "
              f"impl+={delta.implements_added} impl-={delta.implements_removed}")

    # The diff should be compact — much smaller than the full schemas
    summary = diff.summary()
    print(f"\nDiff summary length: {len(summary)} bytes (vs {len(a_text)} + {len(b_text)} = {len(a_text)+len(b_text)} bytes for raw schemas)")
    print(f"Compression ratio: {(len(a_text)+len(b_text))/max(len(summary),1):.1f}x")

    # Verify we can reconstruct with a keep_a merge
    decisions = [
        MergeDecision(type_name=name, action='keep_a', reason='test')
        for name in diff.divergent_names
    ]
    merged = apply_merge(a_text, b_text, decisions)
    print(f"Merged schema size: {len(merged)} bytes")
    assert len(merged) > 0
    print("PASS test_real_candidates")


if __name__ == '__main__':
    test_extract_types()
    test_diff_types()
    test_apply_merge_keep_a()
    test_apply_merge_keep_b()
    test_parse_decisions()
    test_real_candidates()
    print("\n=== ALL TESTS PASSED ===")