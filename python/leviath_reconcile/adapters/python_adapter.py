"""Code projection: typed Python source -> facts, extracted statically.

**No import, no exec, no eval, no application side effect.**  The adapter reads
with :mod:`ast` alone, which is the constraint GQL-04 inherits ("do not execute
arbitrary application imports to infer the schema").  When a construct cannot be
read statically, the adapter reports it as a warning instead of guessing; a
warning is preserved in the extraction, so the caller can require confirmation
before a schema is derived from it.

The symbol-key namespace and fact vocabulary are identical to the schema
adapter's, which is what makes the two projections comparable:

``Class.attribute``  a class attribute (its counterpart is an SDL field)
``Class.method``     a callable member
``function``         a module-level function
``Class``            a module-level class (its counterpart is an SDL type)

Facts emitted per symbol:

``existence``        ``class`` | ``field`` | ``method`` | ``function``
``name``             the local name
``type.kind``        the annotation as written, plus ``=`` / ``?`` requirement
``location.path``    file the symbol was read from
``location.line``    1-based line
``location.parent``  containing class (same value as the key's prefix)
``location.order``   ordinal among siblings
``callable.port``    annotation naming a domain port interface
``annotation.doc``   docstring, if any
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..model import Fact, Projection, SourceRef

#: Annotation names that denote a domain port (R1 port direction).  A code
#: attribute annotated with one of these is the code projection's counterpart of
#: an SDL field whose type is the same port interface.
PORT_ANNOTATIONS = ("WorkflowStepInput", "WorkflowStepOutput")

#: Constructs the static reader deliberately does not resolve.  Hitting one is a
#: warning, never a silent omission.
DYNAMIC_MARKERS = ("__getattr__", "__getattribute__", "setattr(", "globals()[", "eval(", "exec(")


@dataclass
class Extraction:
    """The result of reading one artifact: facts plus any findings."""

    projection: Projection
    warnings: List[str] = field(default_factory=list)
    mode: str = "python-ast"


def _annotation_text(node: Optional[ast.AST]) -> Optional[str]:
    """The annotation as written, normalised to a single line."""
    if node is None:
        return None
    try:
        text = ast.unparse(node)
    except Exception:  # pragma: no cover - ast.unparse is total on parsed trees
        return None
    return " ".join(text.split())


def _allows_none(annotation: Optional[str], default: Optional[ast.AST]) -> bool:
    """Whether a Python attribute is nullable, the counterpart of SDL's missing ``!``.

    An attribute is nullable when its annotation admits ``None``
    (``str | None``, ``Optional[str]``) or its default is the ``None`` literal.
    A bare annotation — the counterpart of SDL's ``!`` — is **not** nullable, and
    neither is an attribute whose default is a real value.
    """
    if annotation is not None:
        compact = annotation.replace(" ", "")
        if "|None" in compact or compact.startswith("Optional["):
            return True
    return isinstance(default, ast.Constant) and default.value is None


def _base_name(annotation: str) -> str:
    """The base name of an annotation, dropping subscripts and unions."""
    head = annotation.split("|", 1)[0].strip()
    head = head.split("[", 1)[0].strip()
    return head.split(".")[-1].strip()


def _fact(
    key: str,
    attribute: str,
    value: str,
    path: str,
    line: int,
    col: int,
    note: str = "",
) -> Fact:
    return Fact(
        key,
        "code",
        attribute,
        value,
        SourceRef(path, line, col, col + max(len(key.rsplit(".", 1)[-1]), 1), note),
    )


def _member_facts(
    key: str,
    parent: Optional[str],
    path: str,
    line: int,
    col: int,
    order: int,
    kind: str,
    annotation: Optional[str],
    doc: Optional[str],
    nullable: bool = False,
    is_method: bool = False,
) -> List[Fact]:
    name = key.rsplit(".", 1)[-1]
    facts = [
        _fact(key, "existence", kind, path, line, col),
        _fact(key, "name", name, path, line, col),
        _fact(key, "location.path", path, path, line, col),
        _fact(key, "location.line", str(line), path, line, col),
        _fact(key, "location.order", str(order), path, line, col),
        # The media-specific declaration spelling is kept as provenance.  It is a
        # local attribute, so it never competes across projections; the
        # media-neutral role lives in ``existence`` (see crosswalk).
        _fact(key, "annotation.declaration", kind, path, line, col),
    ]
    if parent:
        facts.append(_fact(key, "location.parent", parent, path, line, col))
    if annotation:
        marker = "?" if nullable else ""
        facts.append(_fact(key, "type.kind", f"{annotation}{marker}", path, line, col))
        base = _base_name(annotation)
        if base in PORT_ANNOTATIONS:
            facts.append(_fact(key, "callable.port", base, path, line, col))
    if doc:
        facts.append(_fact(key, "annotation.doc", doc, path, line, col))
    if is_method:
        facts.append(_fact(key, "callable.signature", _signature_shape(annotation), path, line, col))
    return facts


def _signature_shape(annotation: Optional[str]) -> str:
    return annotation or "()"


def _is_enum_class(node: ast.ClassDef) -> bool:
    """Whether a class declaration is an ``enum.Enum`` subclass.

    Read statically from the base list: ``Enum``, ``IntEnum``, ``StrEnum`` or a
    dotted ``enum.Enum``.  An enum member is one named *value* of an enumerated
    type — the same domain fact as an SDL enum value — so it must not be read as
    a class attribute, or the two media would disagree about every real enum in
    the accepted contract.
    """
    for base in node.bases:
        name = _base_name(_annotation_text(base) or "")
        if name in ("Enum", "IntEnum", "Flag", "IntFlag", "StrEnum"):
            return True
    return False


def _is_protocol_class(node: ast.ClassDef) -> bool:
    """Whether a class declaration is a ``typing.Protocol`` shape.

    An SDL ``interface`` is a *shape several types implement*, not a concrete
    record.  ``Protocol`` is the Python construct with that meaning, so a class
    deriving from it declares the same media-neutral role (``interface``) rather
    than reading as a concrete ``class`` and disagreeing with its SDL counterpart
    on every interface in the accepted contract.
    """
    for base in node.bases:
        if _base_name(_annotation_text(base) or "") == "Protocol":
            return True
    return False


def _class_declaration(node: ast.ClassDef) -> str:
    """The declaration kind of a class: ``enum`` | ``interface`` | ``class``."""
    if _is_enum_class(node):
        return "enum"
    if _is_protocol_class(node):
        return "interface"
    return "class"


def _alias_declaration(target: str, value: ast.AST) -> Optional[str]:
    """The declaration kind when a module-level assignment declares a *type*.

    In typed Python a module-level assignment whose value is a type expression
    **is** the declaration of a type name:

    * ``Confidential = str`` / ``URI = AnyUrl`` — a type alias, the counterpart
      of an SDL ``scalar``;
    * ``Outcome = A | B | C`` — a union alias, the counterpart of an SDL
      ``union``;
    * ``Confidential = NewType("Confidential", str)`` — the classic named-scalar
      idiom.

    A value assignment (``MAX = 5``) is none of these, so it keeps reading as a
    module-level ``field``.  Returning ``None`` means "not a type declaration".
    The bare-name case requires a type-like (capitalised) target, so a plain
    ``logger = _LOGGER`` binding is not mistaken for a type declaration.
    """
    if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
        return "union_alias"
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        if name in ("NewType", "TypedDict"):
            return "alias"
        return None
    if isinstance(value, (ast.Name, ast.Attribute, ast.Subscript)) and target[:1].isupper():
        return "alias"
    return None


def _enum_member_facts(
    class_key: str, path: str, node: ast.ClassDef, order: int
) -> List[Fact]:
    """Facts for the members of an ``Enum`` class: one ``enum_value`` each."""
    members = [
        child
        for child in node.body
        if isinstance(child, ast.Assign)
        and len(child.targets) == 1
        and isinstance(child.targets[0], ast.Name)
    ]
    facts: List[Fact] = []
    for position, child in enumerate(members):
        name = child.targets[0].id
        if name.startswith("_"):
            continue
        key = f"{class_key}.{name}"
        facts.extend(
            [
                _fact(key, "existence", "enum_value", path, child.lineno, child.col_offset),
                _fact(key, "name", name, path, child.lineno, child.col_offset),
                _fact(key, "location.path", path, path, child.lineno, child.col_offset),
                _fact(key, "location.line", str(child.lineno), path, child.lineno, child.col_offset),
                _fact(key, "location.order", str(position), path, child.lineno, child.col_offset),
                _fact(key, "location.parent", class_key, path, child.lineno, child.col_offset),
                _fact(key, "annotation.declaration", "enum_value", path, child.lineno, child.col_offset),
            ]
        )
    return facts


def extract_python(source: str, path: str = "module.py") -> Extraction:
    """Extract the shared representation from Python source with :mod:`ast`."""
    tree = ast.parse(source, filename=path)
    facts: List[Fact] = []
    warnings: List[str] = []

    for marker in DYNAMIC_MARKERS:
        if marker in source:
            warnings.append(
                f"{path}: dynamic construct {marker!r} is not resolvable statically; "
                "its runtime shape is not represented"
            )

    for order, node in enumerate(tree.body):
        if isinstance(node, ast.ClassDef):
            doc = ast.get_docstring(node)
            class_key = node.name
            facts.extend(
                _member_facts(
                    class_key,
                    None,
                    path,
                    node.lineno,
                    node.col_offset,
                    order,
                    _class_declaration(node),
                    None,
                    doc,
                )
            )
            members = [
                child
                for child in node.body
                if isinstance(child, (ast.AnnAssign, ast.Assign, ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if _is_enum_class(node):
                # An enum class's body is its value set, not its field set.
                facts.extend(_enum_member_facts(class_key, path, node, order))
                continue
            for position, child in enumerate(members):
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    facts.extend(
                        _member_facts(
                            f"{class_key}.{child.target.id}",
                            class_key,
                            path,
                            child.lineno,
                            child.col_offset,
                            position,
                            "field",
                            _annotation_text(child.annotation),
                            None,
                            nullable=_allows_none(_annotation_text(child.annotation), child.value),
                        )
                    )
                elif isinstance(child, ast.Assign) and len(child.targets) == 1 and isinstance(child.targets[0], ast.Name):
                    target = child.targets[0].id
                    annotation = _annotation_text(child.type_comment) if getattr(child, "type_comment", None) else None
                    facts.extend(
                        _member_facts(
                            f"{class_key}.{target}",
                            class_key,
                            path,
                            child.lineno,
                            child.col_offset,
                            position,
                            "field",
                            annotation,
                            None,
                            nullable=_allows_none(annotation, child.value),
                        )
                    )
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    annotation = _annotation_text(child.returns)
                    facts.extend(
                        _member_facts(
                            f"{class_key}.{child.name}",
                            class_key,
                            path,
                            child.lineno,
                            child.col_offset,
                            position,
                            "method",
                            annotation,
                            ast.get_docstring(child),
                            nullable=True,
                            is_method=True,
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.extend(
                _member_facts(
                    node.name,
                    None,
                    path,
                    node.lineno,
                    node.col_offset,
                    order,
                    "function",
                    _annotation_text(node.returns),
                    ast.get_docstring(node),
                    nullable=True,
                    is_method=True,
                )
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            facts.extend(
                _member_facts(
                    node.target.id,
                    None,
                    path,
                    node.lineno,
                    node.col_offset,
                    order,
                    "field",
                    _annotation_text(node.annotation),
                    None,
                    nullable=_allows_none(_annotation_text(node.annotation), node.value),
                )
            )
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            if target.startswith("__"):
                continue
            declaration = _alias_declaration(target, node.value) or "field"
            facts.extend(
                _member_facts(
                    target, None, path, node.lineno, node.col_offset, order, declaration, None, None
                )
            )

    identity = hashlib.sha256((path + "\x00" + source).encode("utf-8")).hexdigest()
    projection = Projection(name="code", document_identity=identity, facts=tuple(facts))
    return Extraction(projection=projection, warnings=warnings, mode="python-ast")


def load_python(path: str | Path) -> Extraction:
    target = Path(path)
    return extract_python(target.read_text(encoding="utf-8"), str(target))


def dedupe(facts: Iterable[Fact]) -> Tuple[Fact, ...]:
    """Keep the first fact per (symbol, projection, attribute); deterministic."""
    out: Dict[Tuple[str, str, str], Fact] = {}
    for fact in facts:
        out.setdefault(fact.key(), fact)
    return tuple(out[key] for key in sorted(out))
