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


# ── merge engine ─────────────────────────────────────────────────────────

@dataclass
class MergeDecision:
    """A decision for one divergent type."""
    type_name: str
    action: str  # 'keep_a', 'keep_b', 'reconcile'
    reason: str = ""
    # For 'reconcile': which fields to include
    include_fields: list[str] = field(default_factory=list)
    # For 'reconcile': custom body text (if the lane writes a merged definition)
    custom_body: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════
# AST-BASED MERGE — the replacement for the broken string-pasting approach.
# ══════════════════════════════════════════════════════════════════════════

# Sentinel used as a map key for the schema definition (which has no name).
_SCHEMA_SENTINEL = object()


def _ast_name_map(doc: DocumentNode) -> dict:
    """Build {name_or_sentinel -> definition} from a parsed DocumentNode.

    Names are strings (from ``def.name.value``).  The ``schema`` definition
    (if any) is stored under ``_SCHEMA_SENTINEL`` because it has no name.
    """
    m = {}
    for d in doc.definitions:
        if hasattr(d, 'name') and d.name and d.name.value:
            m[d.name.value] = d
        elif d.kind == 'schema_definition':
            m[_SCHEMA_SENTINEL] = d
    return m


class _RenameVisitor(_GqlVisitor):
    """Visitor that rewrites NamedType references."""

    def __init__(self, renames: dict[str, str]):
        super().__init__()
        self.renames = renames

    def enter_named_type(self, node, key, parent, path, ancestors):
        if node.name.value in self.renames:
            return NamedTypeNode(name=NameNode(value=self.renames[node.name.value]))
        return None


def _apply_renames_ast(doc: DocumentNode, renames: dict[str, str]) -> DocumentNode:
    """Return a new DocumentNode with all named-type references rewritten."""
    if not renames:
        return doc
    return _gql_visit(doc, _RenameVisitor(renames))


def _definition_name(d):
    """Return the string name for a definition, or None for schema definitions."""
    if hasattr(d, 'name') and d.name and d.name.value:
        return d.name.value
    return None


def apply_merge(
    seed_a_text: str,
    seed_b_text: str,
    decisions: list[MergeDecision],
    renames: Optional[dict[str, str]] = None,
    detected_renames: Optional[dict[str, str]] = None,
) -> str:
    """Apply merge decisions to produce a reconciled schema (AST-based).

    The output is *always* valid SDL because it is produced by
    :func:`graphql.language.print_ast` from a properly-constructed
    :class:`graphql.language.ast.DocumentNode`.  No string concatenation,
    no orphaned prose, no duplicate definitions.

    Parameters
    ----------
    seed_a_text: Schema A text (base).
    seed_b_text: Schema B text.
    decisions:   List of :class:`MergeDecision` for divergent types.
    renames:     ``{old_name: new_name}`` for explicit renames.
    detected_renames:
        Auto-detected same-concept renames.  Merged into *renames*
        automatically (new name wins).

    Returns
    -------
    str
        Merged schema SDL.
    """
    # --- 1. Parse both schemas into ASTs ------------------------------------
    doc_a = _gql_parse(seed_a_text)
    doc_b = _gql_parse(seed_b_text)

    # --- 2. Name-indexed maps -----------------------------------------------
    a_by_name: dict = {}
    for d in doc_a.definitions:
        nm = _definition_name(d)
        key = nm if nm is not None else _SCHEMA_SENTINEL
        a_by_name[key] = d

    b_by_name: dict = {}
    for d in doc_b.definitions:
        nm = _definition_name(d)
        key = nm if nm is not None else _SCHEMA_SENTINEL
        b_by_name[key] = d

    a_names = {k for k in a_by_name if k is not _SCHEMA_SENTINEL}
    b_names = {k for k in b_by_name if k is not _SCHEMA_SENTINEL}

    # --- 3. Collapse renames -----------------------------------------------
    all_renames = dict(detected_renames or {})
    all_renames.update(renames or {})

    # Build decision map
    decision_map = {d.type_name: d for d in decisions}

    # --- 4. Assemble the merged definition list ----------------------------
    # We iterate through doc_a.definitions in original order.  For each
    # definition we decide whether to keep A's version, replace with B's
    # version, reconcile the two, or (for renamed types) drop it entirely
    # so the B-named version can be appended later.
    #
    # After processing A's list, we append:
    #   - B-only definitions (names in B but not in A, modulo renames)
    #   - Renamed-to definitions (the B version of a renamed type)

    merged_defs: list = []
    emitted: set = set()   # names already emitted (to prevent duplicates)
    renamed_from_a: set = set(all_renames.keys())  # A-names to skip

    for d in doc_a.definitions:
        name = _definition_name(d)
        key = name if name is not None else _SCHEMA_SENTINEL

        if name is not None and name in renamed_from_a:
            # Skip — this type is being renamed; B's version is appended later.
            continue

        if name is not None and name in decision_map:
            dec = decision_map[name]
            if dec.action == 'keep_b':
                if key in b_by_name:
                    d = copy.deepcopy(b_by_name[key])
                # fall through to emit
            elif dec.action == 'reconcile':
                d = _reconcile_definition_ast(
                    d, b_by_name.get(key), dec
                )
                # fall through to emit
            # else: keep_a — keep d as-is

        # Mark emitted
        if name is not None:
            emitted.add(name)
        if key is _SCHEMA_SENTINEL:
            emitted.add(_SCHEMA_SENTINEL)

        merged_defs.append(copy.deepcopy(d))

    # --- 5. Append B-only definitions (not in A, not rename targets) -----
    rename_targets = set(all_renames.values())
    for d in doc_b.definitions:
        name = _definition_name(d)
        key = name if name is not None else _SCHEMA_SENTINEL

        if key in emitted:
            continue
        if name is not None and name in rename_targets:
            # This is a renamed-to type — will be handled next.
            continue
        emitted.add(key)
        merged_defs.append(copy.deepcopy(d))

    # --- 6. Emit B versions of renamed types -------------------------------
    for old_name, new_name in all_renames.items():
        if new_name in b_by_name and new_name not in emitted:
            emitted.add(new_name)
            merged_defs.append(copy.deepcopy(b_by_name[new_name]))

    # --- 7. Build the final document and serialize -------------------------
    merged_doc = DocumentNode(definitions=tuple(merged_defs))

    # Apply renames across the whole AST (rewrite NamedType references)
    merged_doc = _apply_renames_ast(merged_doc, all_renames)

    return _gql_print_ast(merged_doc)



def _reconcile_definition_ast(
    a_def,
    b_def,
    decision: MergeDecision,
):
    """Build a reconciled AST definition node by parsing decision language.

    Decision language conventions understood:
    - "adopt B's fields" / "B's evolved" → start from B, add A pieces
    - "restore X from A" → add A's field X to the chosen base
    - "implements Node (from A)" → use A's interfaces
    - "implements CommandProblem" → explicit interface change
    - "keep A's implements Node" → keep A's interface list
    - Otherwise: A is base, pull individual include_fields from B.

    Returns a (possibly new) AST node — the original is never mutated.
    """
    if b_def is None:
        return a_def

    reason = (decision.reason or '')
    include_items = decision.include_fields or ()

    # Also consult include_items for hints like "adopt B's fields"
    include_text = ' '.join(include_items) if include_items else ''
    combined_lower = (reason + ' ' + include_text).lower()

    # ── Determine base side ──────────────────────────────────────────────
    base_is_b = False
    reason_lower = combined_lower
    if 'adopt b' in reason_lower or "b's fields" in reason_lower or "b's evolved" in reason_lower:
        base_is_b = True
    if ('restore' in reason_lower and 'from a' in reason_lower) or \
       ('restore' in reason_lower and 'from A' in reason):
        base_is_b = True

    base = copy.deepcopy(b_def if base_is_b else a_def)
    other = a_def if base_is_b else b_def

    # ── Parse interface hints ────────────────────────────────────────────
    a_iface_names = {i.name.value for i in a_def.interfaces} if hasattr(a_def, 'interfaces') and a_def.interfaces else set()
    b_iface_names = {i.name.value for i in b_def.interfaces} if hasattr(b_def, 'interfaces') and b_def.interfaces else set()

    # Extract explicit "implements <Name>" from reason
    impl_matches = re.findall(r'implement\w*\s+([A-Z]\w*)', reason + ' ' + include_text)
    explicit_ifaces = set(impl_matches)

    final_ifaces = None  # None = don't touch

    if explicit_ifaces:
        # "implements CommandProblem" or "implements Node"
        final_ifaces = explicit_ifaces
    elif 'keep a' in reason_lower and 'implements' in reason_lower:
        final_ifaces = a_iface_names
    elif base_is_b and a_iface_names - b_iface_names:
        # B is base but A had interfaces B dropped — restore A's extras
        # if reason mentions "Node" or "implements"
        if 'node' in reason_lower or 'implements' in reason_lower:
            final_ifaces = b_iface_names | a_iface_names
    else:
        # Default: keep base's interfaces
        final_ifaces = set(getattr(base, 'interfaces', ()) and {i.name.value for i in base.interfaces} or ())

    if final_ifaces is not None and hasattr(base, 'interfaces'):
        new_ifaces = []
        for iface_name in final_ifaces:
            found = None
            for src in (a_def, b_def):
                if hasattr(src, 'interfaces') and src.interfaces:
                    for i in src.interfaces:
                        if i.name.value == iface_name:
                            found = i
                            break
                if found:
                    break
            if found:
                new_ifaces.append(found)
            else:
                new_ifaces.append(NamedTypeNode(name=NameNode(value=iface_name)))
        base.interfaces = tuple(new_ifaces)

    # ── Handle fields ────────────────────────────────────────────────────
    if hasattr(base, 'fields'):
        a_field_map = {f.name.value: f for f in a_def.fields} if hasattr(a_def, 'fields') else {}
        b_field_map = {f.name.value: f for f in b_def.fields} if hasattr(b_def, 'fields') else {}
        base_field_names = {f.name.value for f in base.fields}

        fields_to_add = set()

        # Parse include_fields for actual field names
        for item in include_items:
            clean = re.sub(r'\(.*', '', item).strip()  # strip parentheticals
            clean = re.sub(r'\s+plus\s+.*', '', clean, flags=re.IGNORECASE).strip()
            clean = re.sub(r';.*', '', clean).strip()
            clean = re.sub(r'^adopt\s+', '', clean, flags=re.IGNORECASE).strip()
            if not clean:
                continue
            # Skip prose / interface names
            if clean[0].isupper() and clean not in a_field_map and clean not in b_field_map:
                continue  # likely an interface name or prose
            if clean.startswith('implements'):
                continue
            # Check if it's a valid field name from either side
            if clean in a_field_map or clean in b_field_map:
                if clean not in base_field_names:
                    fields_to_add.add(clean)

        # "restore X from A"
        restore_matches = re.findall(r'restore\w*\s+(\w+)\s+from\s+A', reason, re.IGNORECASE)
        for fname in restore_matches:
            if fname in a_field_map and fname not in base_field_names:
                fields_to_add.add(fname)

        # If base_is_b and A has extra fields mentioned in reason, add them
        if base_is_b:
            a_extra = set(a_field_map) - set(b_field_map)
            for fname in a_extra:
                if fname.lower() in reason_lower:
                    fields_to_add.add(fname)

        new_fields = list(base.fields)
        added = set(base_field_names)
        for fname in fields_to_add:
            if fname not in added:
                src = a_field_map.get(fname) or b_field_map.get(fname)
                if src:
                    new_fields.append(copy.deepcopy(src))
                    added.add(fname)
        base.fields = tuple(new_fields)

    return base


# ── decision parsing ─────────────────────────────────────────────────────

def parse_decisions(text: str) -> tuple[list[MergeDecision], dict[str, str]]:
    """
    Parse merge decisions from a lane's output text.

    Expected format (one per line or block):
      TYPE <name>: keep_a | keep_b | reconcile
      REASON: <text>
      FIELDS: <comma-separated list>  (for reconcile)
      RENAME <old> -> <new>: keep_a | keep_b
      REASON: <text>

    Returns (decisions, renames) where renames is {old_name: new_name} for keep_b renames.
    """
    decisions = []
    renames = {}
    current = None
    current_rename_old = None
    current_rename_new = None
    current_rename_action = None

    for line in text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        # Check for RENAME line first
        rename_match = re.match(r'RENAME\s+(\S+)\s*->\s*(\S+)\s*:\s*(keep_a|keep_b)', line, re.IGNORECASE)
        if rename_match:
            # Save any pending TYPE decision
            if current:
                decisions.append(current)
                current = None
            old_name = rename_match.group(1)
            new_name = rename_match.group(2)
            action = rename_match.group(3).lower()
            if action == 'keep_b':
                renames[old_name] = new_name
            continue

        type_match = re.match(r'TYPE\s+(\S+)\s*:\s*(keep_a|keep_b|reconcile)', line, re.IGNORECASE)
        if type_match:
            if current:
                decisions.append(current)
            current = MergeDecision(
                type_name=type_match.group(1),
                action=type_match.group(2).lower(),
            )
            continue

        if current:
            reason_match = re.match(r'REASON\s*:\s*(.*)', line, re.IGNORECASE)
            if reason_match:
                current.reason = reason_match.group(1)
                continue

            fields_match = re.match(r'FIELDS\s*:\s*(.*)', line, re.IGNORECASE)
            if fields_match:
                current.include_fields = [f.strip() for f in fields_match.group(1).split(',') if f.strip()]
                continue

    if current:
        decisions.append(current)

    return decisions, renames


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
    bo = '/data/work/leviath/study/bakeoff'
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