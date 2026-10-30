"""Typed-code adapter: declared callable ports and metadata, read statically.

This is the GQL-04 layer on top of GQL-02's accepted ``python_adapter``.  It
**reuses** that accepted extraction (never re-implements it) and adds exactly the
things the typed-code language server needs:

* ``callable.params`` / ``callable.returns`` / ``callable.signature`` for every
  callable, with canonicalised parameter types and nullability markers;
* ``callable.port`` for a class that *declares* a domain port in its base list;
* ``callable.confirmation`` for the confirmation wrapper on a value.

Static analysis only: :mod:`ast`, no import, no exec, no eval of application
code.  A construct that cannot be read statically becomes an
:class:`UnsupportedConstruct` — an explicit, locatable finding with an
obligation — and it marks the extraction ``blocked``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from leviath_reconcile.adapters import python_adapter
from leviath_reconcile.crosswalk import canonical_type
from leviath_reconcile.model import Fact, Projection, SourceRef

#: The port interfaces a typed-code class may declare in its base list.
PORT_INTERFACES = ("WorkflowStepInput", "WorkflowStepOutput")

#: The wrapper that carries a confirmation authority on a value.
CONFIRMATION_WRAPPER = "Confirmation"

#: Constructs the static reader refuses to guess about.  Marker -> why it is
#: unresolvable, and what the author must do to make the intent declarable.
DYNAMIC_MARKERS: Tuple[Tuple[str, str, str], ...] = (
    ("__getattr__", "attribute access is computed at run time", "declare the attribute explicitly"),
    ("__getattribute__", "attribute lookup is intercepted at run time", "declare the attribute explicitly"),
    ("globals()[", "module namespace is mutated at run time", "declare a module-level symbol"),
    ("setattr(", "attributes are created at run time", "declare the attribute in the class body"),
    ("eval(", "source is built at run time", "write the declaration literally"),
    ("exec(", "source is executed at run time", "write the declaration literally"),
)
#: ``from x import *`` is detected structurally (``names`` is ``None``), not by text.
STAR_IMPORT_CONSTRUCT = "import *"
STAR_IMPORT_REASON = "wildcard import hides which names are declared"
STAR_IMPORT_OBLIGATION = "import the names explicitly"


@dataclass(frozen=True)
class UnsupportedConstruct:
    """One dynamic construct the static reader will not guess about."""

    construct: str
    path: str
    line: int
    reason: str
    obligation: str

    def as_dict(self) -> dict:
        return {
            "construct": self.construct,
            "path": self.path,
            "line": self.line,
            "reason": self.reason,
            "obligation": self.obligation,
        }

    def message(self) -> str:
        return (
            f"{self.path}:{self.line}: unsupported dynamic construct {self.construct!r}: "
            f"{self.reason}; obligation: {self.obligation}"
        )


@dataclass
class TypedExtraction:
    """One typed-code artifact's extraction."""

    projection: Projection
    unsupported: List[UnsupportedConstruct] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    mode: str = "python-ast-static"

    @property
    def blocked(self) -> bool:
        """True when an obligation is outstanding: the projection is inadmissible."""
        return bool(self.unsupported)


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _find_dynamic(source: str, path: str) -> List[UnsupportedConstruct]:
    findings: List[UnsupportedConstruct] = []
    for marker, reason, obligation in DYNAMIC_MARKERS:
        start = 0
        while True:
            found = source.find(marker, start)
            if found == -1:
                break
            findings.append(
                UnsupportedConstruct(marker, path, _line_of(source, found), reason, obligation)
            )
            start = found + len(marker)
    return findings


def _find_star_imports(tree: ast.AST, path: str) -> List[UnsupportedConstruct]:
    findings: List[UnsupportedConstruct] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            findings.append(
                UnsupportedConstruct(
                    STAR_IMPORT_CONSTRUCT,
                    path,
                    node.lineno,
                    STAR_IMPORT_REASON,
                    STAR_IMPORT_OBLIGATION,
                )
            )
    return findings


def _annotation_text(node: Optional[ast.AST]) -> Optional[str]:
    if node is None:
        return None
    try:
        return " ".join(ast.unparse(node).split())
    except Exception:  # pragma: no cover - ast.unparse is total on parsed trees
        return None


def _base_name(annotation: str) -> str:
    head = annotation.split("|", 1)[0].strip()
    head = head.split("[", 1)[0].strip()
    return head.split(".")[-1].strip()


def _subscript_head(node: ast.AST) -> Optional[str]:
    """The head name of a subscript expression (``Confirmation[T, "..."]``)."""
    if not isinstance(node, ast.Subscript):
        return None
    value = node.value
    if isinstance(value, ast.Name):
        return value.id
    return getattr(value, "attr", None)


def confirmation_of(annotation: Optional[str], node: Optional[ast.AST] = None) -> Optional[str]:
    """The confirmation authority declared by a ``Confirmation[T, "..."]`` wrapper.

    Read from the syntax so the authority is a declared literal, never a value
    inferred by running the application.  Returns ``None`` when not wrapped.
    """
    if _subscript_head(node) == CONFIRMATION_WRAPPER:
        slice_node = node.slice
        elements = slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
        if len(elements) >= 2 and isinstance(elements[1], ast.Constant):
            return str(elements[1].value)
    if annotation and annotation.startswith(CONFIRMATION_WRAPPER + "["):
        inner = annotation[len(CONFIRMATION_WRAPPER) + 1: annotation.rfind("]")]
        parts = [part.strip() for part in inner.split(",")]
        if len(parts) >= 2:
            return parts[1].strip().strip('"').strip("'")
    return None


def unwrapped_type(annotation: Optional[str], node: Optional[ast.AST] = None) -> Optional[str]:
    """``Confirmation[T, "..."]`` -> ``T``; any other annotation unchanged."""
    if _subscript_head(node) == CONFIRMATION_WRAPPER:
        slice_node = node.slice
        elements = slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
        if elements:
            return _annotation_text(elements[0])
    if annotation and annotation.startswith(CONFIRMATION_WRAPPER + "["):
        inner = annotation[len(CONFIRMATION_WRAPPER) + 1: annotation.rfind("]")]
        return inner.split(",", 1)[0].strip()
    return annotation


def _allows_none(annotation: Optional[str], default: Optional[ast.AST]) -> bool:
    if annotation is not None:
        compact = annotation.replace(" ", "")
        if "|None" in compact or compact.startswith("Optional["):
            return True
    return isinstance(default, ast.Constant) and default.value is None


def _fact(key: str, attribute: str, value: str, path: str, line: int, col: int) -> Fact:
    name = key.rsplit(".", 1)[-1]
    return Fact(key, "code", attribute, value, SourceRef(path, line, col, col + max(len(name), 1)))


def _param_shape(arg: ast.arg, default: Optional[ast.AST]) -> Tuple[str, str]:
    """``(canonical type, 'name:Type' spelling with '?' when nullable)``."""
    written = _annotation_text(arg.annotation)
    canonical = canonical_type(written, "code") if written else "any"
    nullable = _allows_none(written, default)
    if nullable and not canonical.endswith("?"):
        canonical = canonical + "?"
    base, _, marker = canonical.partition("?")
    return canonical, f"{arg.arg}:{base}{'?' if (marker or nullable) else ''}"


def _callable_facts(key: str, node, path: str) -> List[Fact]:
    """Parameter/return/signature facts for one callable."""
    args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
    defaults: List[Optional[ast.AST]] = [None] * (len(args) - len(node.args.defaults)) + list(
        node.args.defaults
    )
    if node.args.vararg is not None:
        args.append(node.args.vararg)
        defaults.append(None)
    if node.args.kwarg is not None:
        args.append(node.args.kwarg)
        defaults.append(None)

    pieces: List[str] = []
    for arg, default in zip(args, defaults):
        if arg.arg in ("self", "cls"):
            continue
        _, shape = _param_shape(arg, default)
        pieces.append(shape)

    returns_written = _annotation_text(node.returns)
    returns_canonical = canonical_type(returns_written, "code") if returns_written else "any"
    return [
        _fact(key, "callable.params", ",".join(pieces), path, node.lineno, node.col_offset),
        _fact(key, "callable.returns", returns_canonical, path, node.lineno, node.col_offset),
        _fact(
            key,
            "callable.signature",
            f"({','.join(pieces)})->{returns_canonical}",
            path,
            node.lineno,
            node.col_offset,
        ),
    ]


def _field_confirmation_facts(
    class_key: str, child: ast.AnnAssign, path: str
) -> List[Fact]:
    """A ``callable.confirmation`` fact when a class field is confirmation-wrapped."""
    if not isinstance(child.target, ast.Name):
        return []
    authority = confirmation_of(_annotation_text(child.annotation), child.annotation)
    if not authority:
        return []
    return [
        _fact(
            f"{class_key}.{child.target.id}",
            "callable.confirmation",
            f"by={authority}",
            path,
            child.lineno,
            child.col_offset,
        )
    ]


def _unwrap_field_facts(facts: List[Fact], path: str) -> List[Fact]:
    """Replace a wrapped field's ``type.kind`` with its unwrapped, required type.

    ``token: Confirmation[Confidential, "STEP_RUNNER"]`` declares a *required*
    ``Confidential`` value, so the projection must read it that way.  Leaving the
    wrapper spelling in ``type.kind`` would make every wrapped value look like a
    type divergence against its SDL counterpart.
    """
    out: List[Fact] = []
    for fact in facts:
        if fact.attribute == "type.kind" and CONFIRMATION_WRAPPER + "[" in fact.value:
            inner = unwrapped_type(fact.value)
            canonical = canonical_type(inner, "code") if inner else "any"
            out.append(
                Fact(
                    fact.symbol_key,
                    fact.projection,
                    fact.attribute,
                    canonical,
                    fact.source,
                    fact.confidence,
                )
            )
        else:
            out.append(fact)
    return out


def extract_typed(source: str, path: str = "module.py") -> TypedExtraction:
    """Extract one typed-Python artifact statically.

    Reuses GQL-02's accepted ``python_adapter`` for the shared vocabulary, then
    adds the callable-signature, declared-port and confirmation facts.  Never
    imports, never executes, never evaluates the artifact.
    """
    base = python_adapter.extract_python(source, path)
    facts: List[Fact] = [
        f for f in base.projection.facts if f.attribute != "callable.signature"
    ]
    facts = _unwrap_field_facts(facts, path)
    warnings: List[str] = list(base.warnings)
    unsupported: List[UnsupportedConstruct] = []

    tree = ast.parse(source, filename=path)
    unsupported.extend(_find_dynamic(source, path))
    unsupported.extend(_find_star_imports(tree, path))

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for base_node in node.bases:
                name = _base_name(_annotation_text(base_node) or "")
                if name in PORT_INTERFACES:
                    facts.append(
                        _fact(node.name, "callable.port", name, path, node.lineno, node.col_offset)
                    )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    key = f"{node.name}.{child.name}"
                    facts.extend(_callable_facts(key, child, path))
                    args = list(child.args.posonlyargs) + list(child.args.args)
                    defaults = [None] * (len(args) - len(child.args.defaults)) + list(
                        child.args.defaults
                    )
                    for arg, _ in zip(args, defaults):
                        authority = confirmation_of(
                            _annotation_text(arg.annotation), arg.annotation
                        )
                        if authority:
                            facts.append(
                                _fact(
                                    key,
                                    f"callable.confirmation.{arg.arg}",
                                    f"by={authority}",
                                    path,
                                    arg.lineno,
                                    arg.col_offset,
                                )
                            )
                elif isinstance(child, ast.AnnAssign):
                    facts.extend(_field_confirmation_facts(node.name, child, path))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.extend(_callable_facts(node.name, node, path))

    projection = Projection(
        name="code", document_identity=base.projection.document_identity, facts=tuple(facts)
    )
    return TypedExtraction(
        projection=projection, unsupported=unsupported, warnings=warnings, mode="python-ast-static"
    )


def load_typed(path: str | Path) -> TypedExtraction:
    target = Path(path)
    return extract_typed(target.read_text(encoding="utf-8"), str(target))