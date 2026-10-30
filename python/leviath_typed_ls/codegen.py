"""Codegen: a schema edit -> concrete, applicable edits to typed Python source.

The other half of the round trip.  Where :mod:`projection` answers "what schema
does this Python imply?", this module answers "which exact characters must change
in this Python file to match that schema?" — and it answers with spans, not with
prose, so an editor can apply them and a test can assert them.

Everything is derived from the two fact sets (the code facts relabelled as schema
facts, and the edited schema facts) through the accepted crosswalk, so the
translation cannot drift from the reconciliation engine.  An edit whose recorded
``old_text`` no longer matches the source is a loud :class:`CodegenConflict`
rather than a silent mis-write.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import projection as projection_mod
from .typed_code_adapter import CONFIRMATION_WRAPPER
from leviath_reconcile.crosswalk import canonical_type

#: Fact attributes that a code edit can realise.
EDITABLE = ("type.kind", "callable.confirmation", "callable.params")


@dataclass(frozen=True)
class CodeEdit:
    """One applicable replacement in a typed-code artifact."""

    path: str
    line: int
    col_start: int
    col_end: int
    old_text: str
    new_text: str
    symbol_key: str
    attribute: str
    rationale: str

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "col_start": self.col_start,
            "col_end": self.col_end,
            "old_text": self.old_text,
            "new_text": self.new_text,
            "symbol_key": self.symbol_key,
            "attribute": self.attribute,
            "rationale": self.rationale,
        }


class CodegenConflict(RuntimeError):
    """An edit no longer matches the source it was computed against."""


def _index(facts: Iterable) -> Dict[Tuple[str, str], str]:
    return {(f.symbol_key, f.attribute): f.value for f in facts}


def _canonical_index(facts: Iterable, projection: str) -> Dict[Tuple[str, str], str]:
    """Attribute values normalised onto the media-neutral canonical spelling.

    The two fact sets spell the same type in two languages (``Confidential!`` vs
    ``confidential``), so comparing them verbatim would make every field look
    changed *and* would emit SDL spellings into Python source.  Canonicalising
    here is what makes the comparison meaningful and the edit applicable.
    """
    out: Dict[Tuple[str, str], str] = {}
    for fact in facts:
        value = fact.value
        if fact.attribute == "type.kind":
            value = canonical_type(value, projection)
        out[(fact.symbol_key, fact.attribute)] = value
    return out


def _source_lines(source: str) -> List[str]:
    return source.splitlines()


def _span_text(source: str, line: int, col_start: int, col_end: int) -> str:
    lines = _source_lines(source)
    if not 1 <= line <= len(lines):
        return ""
    return lines[line - 1][col_start:col_end]


@dataclass
class _NodeRef:
    """Where a symbol is declared in the source, and how."""

    kind: str                     # "field" | "method"
    line: int
    name_col: int
    name_end_col: int
    annotation_col: int
    annotation_end_col: int
    default_start_col: Optional[int]
    arg_nodes: Dict[str, ast.arg]
    node: ast.AST


def _node_index(tree: ast.AST) -> Dict[str, _NodeRef]:
    """symbol key -> declaration location, for fields and methods."""
    out: Dict[str, _NodeRef] = {}

    def add(key: str, child: ast.AST) -> None:
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            out[key] = _NodeRef(
                kind="field",
                line=child.lineno,
                name_col=child.target.col_offset,
                name_end_col=child.target.end_col_offset or child.target.col_offset,
                annotation_col=child.annotation.col_offset,
                annotation_end_col=child.annotation.end_col_offset or child.annotation.col_offset,
                default_start_col=None,
                arg_nodes={},
                node=child,
            )
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = list(child.args.posonlyargs) + list(child.args.args) + list(child.args.kwonlyargs)
            out[key] = _NodeRef(
                kind="method",
                line=child.lineno,
                name_col=child.col_offset,
                name_end_col=child.col_offset,
                annotation_col=child.annotations if False else 0,
                annotation_end_col=0,
                default_start_col=None,
                arg_nodes={arg.arg: arg for arg in args},
                node=child,
            )

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.AnnAssign, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = (
                        child.name
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                        else child.target.id
                    )
                    add(f"{node.name}.{name}", child)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            add(node.target.id, node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, node)
    return out


def _code_annotation(canonical: str, authority: Optional[str]) -> str:
    """Canonical type (+ optional confirmation authority) -> Python annotation."""
    if not authority:
        return projection_mod.schema_facts_to_code_type(canonical)
    base, required, is_list = projection_mod._split_canonical(canonical)
    name = projection_mod.CODE_TYPE_NAMES.get(base, base)
    if is_list:
        name = f"list[{name}]"
    wrapped = f'{CONFIRMATION_WRAPPER}[{name}, "{authority}"]'
    return wrapped if required else f"{wrapped} | None"


def _field_edits(
    source: str,
    path: str,
    key: str,
    ref: _NodeRef,
    before: Dict[Tuple[str, str], str],
    after: Dict[Tuple[str, str], str],
) -> List[CodeEdit]:
    before_type = before.get((key, "type.kind"))
    after_type = after.get((key, "type.kind"))
    before_conf = before.get((key, "callable.confirmation"))
    after_conf = after.get((key, "callable.confirmation"))
    if after_type is None and after_conf is None:
        return []
    if before_type == after_type and before_conf == after_conf:
        return []

    canonical = after_type if after_type is not None else before_type
    if canonical is None:
        return []
    authority = None
    if after_conf:
        authority = after_conf.split("=", 1)[-1]
    annotation = _code_annotation(canonical, authority)
    line_text = _source_lines(source)[ref.line - 1]
    old_text = line_text[ref.name_col: ref.annotation_end_col]
    edits = [
        CodeEdit(
            path=path,
            line=ref.line,
            col_start=ref.name_col,
            col_end=ref.annotation_end_col,
            old_text=old_text,
            new_text=f"{key.rsplit('.', 1)[-1]}: {annotation}",
            symbol_key=key,
            attribute="type.kind" if before_type != after_type else "callable.confirmation",
            rationale=f"schema declares {key} as {canonical}"
            + (f" gated by {authority}" if authority else ""),
        )
    ]
    # A required declaration must not keep a `= None` default, or the code would
    # re-introduce the nullability the schema just removed.
    default = getattr(ref.node, "value", None)
    _, required, _ = projection_mod._split_canonical(canonical)
    if required and isinstance(default, ast.Constant) and default.value is None:
        edits.append(
            CodeEdit(
                path=path,
                line=default.lineno,
                col_start=ref.annotation_end_col,
                col_end=default.end_col_offset or ref.annotation_end_col,
                old_text=line_text[ref.annotation_end_col: default.end_col_offset or ref.annotation_end_col],
                new_text="",
                symbol_key=key,
                attribute="type.kind",
                rationale=f"{key} is required, so its None default is removed",
            )
        )
    return edits


def _param_edits(
    source: str,
    path: str,
    key: str,
    ref: _NodeRef,
    before: Dict[Tuple[str, str], str],
    after: Dict[Tuple[str, str], str],
) -> List[CodeEdit]:
    before_params = before.get((key, "callable.params"))
    after_params = after.get((key, "callable.params"))
    if after_params is None:
        return []

    def parse(value: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for piece in filter(None, value.split(",")):
            name, _, spelled = piece.partition(":")
            out[name] = spelled
        return out

    before_map = parse(before_params or "")
    after_map = parse(after_params)
    lines = _source_lines(source)
    edits: List[CodeEdit] = []
    for name, spelled in after_map.items():
        if before_map.get(name) == spelled:
            continue
        arg = ref.arg_nodes.get(name)
        if arg is None:
            continue
        end = arg.end_col_offset or arg.col_offset
        canonical = projection_mod.crosswalk_canonical(spelled)
        nullable = spelled.endswith("?")
        annotation = _code_annotation(canonical, None)
        if nullable and not annotation.endswith("| None"):
            annotation = f"{annotation} | None"
        line_text = lines[arg.lineno - 1]
        edits.append(
            CodeEdit(
                path=path,
                line=arg.lineno,
                col_start=arg.col_offset,
                col_end=end,
                old_text=line_text[arg.col_offset:end],
                new_text=f"{name}: {annotation}",
                symbol_key=key,
                attribute="callable.params",
                rationale=f"schema declares {key}({name}) as {spelled}",
            )
        )
    return edits


def schema_edit_to_code_edits(
    source: str,
    path: str,
    before_facts: Iterable,
    after_facts: Iterable,
) -> List[CodeEdit]:
    """Concrete code edits that make ``source`` match the edited schema facts.

    ``before_facts`` is the current code projection relabelled as schema facts
    (see :func:`projection.code_facts_to_schema_facts`); ``after_facts`` is the
    edited schema projection.  Only symbols the schema still declares are
    realised, and edits are returned sorted by position so they apply cleanly.
    """
    before = _canonical_index(before_facts, "code")
    after = _canonical_index(after_facts, "schema")
    tree = ast.parse(source, filename=path)
    nodes = _node_index(tree)

    symbols = sorted({key for (key, attribute) in after if attribute in EDITABLE})
    edits: List[CodeEdit] = []
    for key in symbols:
        ref = nodes.get(key)
        if ref is None:
            continue
        if ref.kind == "method":
            edits.extend(_param_edits(source, path, key, ref, before, after))
        else:
            edits.extend(_field_edits(source, path, key, ref, before, after))
    return sorted(edits, key=lambda e: (e.line, e.col_start, e.col_end))


def apply_edits(source: str, edits: Sequence[CodeEdit]) -> str:
    """Apply edits to ``source``, verifying each span still holds ``old_text``."""
    lines = source.splitlines(keepends=True)
    for edit in sorted(edits, key=lambda e: (e.line, e.col_start), reverse=True):
        index = edit.line - 1
        if not 0 <= index < len(lines):
            raise CodegenConflict(f"edit at line {edit.line} is outside the source")
        text = lines[index]
        current = text[edit.col_start: edit.col_end]
        if current != edit.old_text:
            raise CodegenConflict(
                f"edit for {edit.symbol_key} expected {edit.old_text!r} at "
                f"{edit.path}:{edit.line}:{edit.col_start}, found {current!r}"
            )
        lines[index] = text[: edit.col_start] + edit.new_text + text[edit.col_end:]
    return "".join(lines)