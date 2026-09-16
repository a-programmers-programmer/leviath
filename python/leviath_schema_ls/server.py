"""A write-first GraphQL SDL language server for Leviath.

The server is a small, dependency-light JSON-RPC loop over stdin/stdout.  It
does not use an LSP framework, so the protocol is visible and testable.

What it does
------------
``initialize``
    Advertises capabilities and, optionally, loads a *baseline* pair (schema
    SDL + typed-Python code) plus the current code projection from
    ``initializationOptions``.  The baseline is the last agreed version the
    shared engine diffs against.
``textDocument/didOpen`` / ``didChange``
    Stores the document text and its version.  A version that is not newer than
    the stored one is a **stale edit** and is refused: the request gets a
    JSON-RPC error (code ``-32002``); the notification gets a
    ``window/showMessage`` error.
``textDocument/diagnostic``
    Returns **source-ranged** diagnostics: GraphQL semantic errors found in the
    SDL text, plus reconciliation conflicts reported by the shared engine
    (:mod:`leviath_reconcile`), each carrying the SDL line/column span that
    produced it and the confirmation options a principal can choose.
``textDocument/codeAction``
    Returns versioned, text-only quick fixes.  Every action is a pure
    ``TextEdit``; the server never returns a ``command`` and never executes
    anything.  No diagnostic performs a runtime action and no sensitive value is
    unwrapped.

The reconciliation engine is imported from GQL-02 and is never modified.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --- locate the accepted GQL-02 engine (read-only import) -------------------
_RECONCILE_ROOT = os.environ.get(
    "LEVIATH_RECONCILE_PATH", "/data/work/graphql-realization/gql-02/repo/python"
)
if os.path.isdir(_RECONCILE_ROOT) and _RECONCILE_ROOT not in sys.path:
    sys.path.insert(0, _RECONCILE_ROOT)

from leviath_reconcile.adapters.python_adapter import extract_python  # noqa: E402
from leviath_reconcile.adapters.schema_adapter import extract_sdl  # noqa: E402
from leviath_reconcile.baseline import Baseline  # noqa: E402
from leviath_reconcile.model import FactSet  # noqa: E402
from leviath_reconcile.reconcile import Reconciler  # noqa: E402

SERVER_NAME = "leviath-schema-ls"
SERVER_VERSION = "0.1.0"

#: JSON-RPC error code used for a superseded (stale) document version.
STALE_VERSION_CODE = -32002

DIAGNOSTIC_SOURCE = "leviath"

#: Built-in GraphQL scalar names that never need a declaration.
BUILTIN_SCALARS = frozenset({"Int", "Float", "String", "Boolean", "ID"})

#: Type-level declaration kinds that introduce a named type.
TYPE_LEVEL_KINDS = frozenset({"type", "enum", "interface", "union", "scalar", "input"})

#: SDL declaration kind -> the media-neutral role the crosswalk uses.  Used only
#: to build the baseline fact set, never to change the engine.
_ROLE_DECLARATION = {
    "type": "type",
    "enum": "enum",
    "interface": "interface",
    "union": "union",
    "scalar": "scalar",
    "input": "input",
    "field": "field",
    "enum_value": "enum_value",
    "union_member": "union_member",
}


# ---------------------------------------------------------------------------
# Protocol plumbing.
# ---------------------------------------------------------------------------


class StaleEdit(Exception):
    """A document version that the server has already superseded."""


class ProtocolError(Exception):
    """A malformed or out-of-order JSON-RPC message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Document:
    """One open SDL document: its text and its current version."""

    uri: str
    text: str
    version: int
    language_id: str = "graphql"


def _read_message(stream) -> Optional[dict]:
    """Read one ``Content-Length``-framed JSON-RPC message, or ``None`` at EOF."""
    headers: Dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if b":" in line:
            name, _, value = line.partition(b":")
            headers[name.decode("ascii").strip().lower()] = value.decode("ascii").strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    payload = stream.read(length)
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


def _write_message(stream, message: dict) -> None:
    """Write one ``Content-Length``-framed JSON-RPC message."""
    body = json.dumps(message).encode("utf-8")
    stream.write(b"Content-Length: %d\r\n\r\n" % len(body))
    stream.write(body)
    stream.flush()


# ---------------------------------------------------------------------------
# Source ranges.
# ---------------------------------------------------------------------------


def _range_from_source(source: Optional[dict], fallback_line: int = 0) -> dict:
    """An LSP range (0-based line, 0-based character) from a source map entry."""
    if not source:
        return {
            "start": {"line": fallback_line, "character": 0},
            "end": {"line": fallback_line, "character": 1},
        }
    # Source map entries are 1-based (line and columns); LSP positions are
    # 0-based on both axes, so one is subtracted from each.
    line = max(0, int(source.get("line", 1)) - 1)
    start = max(0, int(source.get("col_start", 1)) - 1)
    end = max(start + 1, int(source.get("col_end", start + 2)) - 1)
    return {"start": {"line": line, "character": start}, "end": {"line": line, "character": end}}


def _range_contains(outer: dict, inner: dict) -> bool:
    """True when ``inner`` sits inside ``outer`` (LSP positions are ordered)."""
    def key(pos: dict) -> Tuple[int, int]:
        return (int(pos["line"]), int(pos["character"]))

    return key(outer["start"]) <= key(inner["start"]) and key(inner["end"]) <= key(outer["end"])


# ---------------------------------------------------------------------------
# The server.
# ---------------------------------------------------------------------------


@dataclass
class SchemaServer:
    """Stateful LSP handler.  Pure functions over the stored document text."""

    documents: Dict[str, Document] = field(default_factory=dict)
    #: The last agreed version: schema SDL + typed-Python code, as read.
    baseline: Optional[Baseline] = None
    #: The current code projection text and path (the other reconciliation side).
    code_text: str = ""
    code_path: str = "model.py"
    shutdown_requested: bool = False
    #: Notifications the server pushed (used by the protocol test to observe
    #: rejections that have no request id).
    notifications: List[dict] = field(default_factory=list)

    # -- document store -----------------------------------------------------
    def open_document(self, uri: str, text: str, version: int, language_id: str = "graphql") -> None:
        self.documents[uri] = Document(uri=uri, text=text, version=version, language_id=language_id)

    def change_document(self, uri: str, text: str, version: int) -> None:
        """Apply a full-text change, refusing a version that is not newer.

        Refusing a superseded version is what stops a stale editor buffer from
        overwriting newer work: the edit is rejected before it touches state.
        """
        current = self.documents.get(uri)
        if current is None:
            raise ProtocolError(-32001, f"document not open: {uri}")
        if version <= current.version:
            raise StaleEdit(
                f"stale document version {version} for {uri}: version {current.version} is current"
            )
        current.text = text
        current.version = version

    def document(self, uri: str) -> Document:
        doc = self.documents.get(uri)
        if doc is None:
            raise ProtocolError(-32001, f"document not open: {uri}")
        return doc

    # -- baseline -----------------------------------------------------------
    def load_baseline(self, options: dict) -> None:
        """Build the agreed baseline pair from ``initializationOptions``.

        ``options`` may carry::

            {"baseline": {"schema": "<SDL>", "code": "<python>"},
             "code": {"path": "model.py", "text": "<python>"}}

        A missing baseline leaves the server in "SDL-only" mode: diagnostics
        then report GraphQL semantic errors only.
        """
        options = options or {}
        code = options.get("code") or {}
        if code:
            self.code_text = str(code.get("text", ""))
            self.code_path = str(code.get("path", self.code_path))
        baseline = options.get("baseline") or {}
        schema_sdl = baseline.get("schema")
        if not schema_sdl:
            return
        schema_extraction = extract_sdl(str(schema_sdl), "schema.graphql")
        code_text = str(baseline.get("code", self.code_text))
        code_extraction = extract_python(code_text, self.code_path)
        self.baseline = Baseline(
            revision=1,
            contract="workflow-model v1",
            documents={
                "schema": schema_extraction.projection.document_identity,
                "code": code_extraction.projection.document_identity,
            },
            body=FactSet(tuple(schema_extraction.projection.facts) + tuple(code_extraction.projection.facts)),
        )

    # -- semantic checks (source-ranged) ------------------------------------
    def _sdl_semantic_diagnostics(self, text: str) -> Tuple[List[dict], List[Any], List[str]]:
        """GraphQL semantic errors in the SDL text, each with a source range.

        Returns ``(diagnostics, facts, type_names)``.  A document that does not
        parse yields exactly one error at the reported location and no facts.
        """
        diagnostics: List[dict] = []
        try:
            extraction = extract_sdl(text, "schema.graphql")
        except Exception as exc:  # noqa: BLE001 - reported as a diagnostic, not raised
            locations = getattr(exc, "locations", None) or []
            line = 0
            character = 0
            if locations:
                line = max(0, int(locations[0].line) - 1)
                character = max(0, int(locations[0].column) - 1)
            diagnostics.append(
                {
                    "range": {
                        "start": {"line": line, "character": character},
                        "end": {"line": line, "character": character + 1},
                    },
                    "severity": 1,
                    "code": "leviath/parse-error",
                    "source": DIAGNOSTIC_SOURCE,
                    "message": f"SDL does not parse: {exc}",
                    "data": {"kind": "parse-error"},
                }
            )
            return diagnostics, [], []

        facts = list(extraction.projection.facts)
        type_names = {
            fact.symbol_key
            for fact in facts
            if fact.attribute == "existence" and fact.value in TYPE_LEVEL_KINDS
        }

        # 1. duplicate declaration of one symbol (same canonical key twice).
        seen: Dict[Tuple[str, str], int] = {}
        for fact in facts:
            if fact.attribute != "existence":
                continue
            seen[(fact.symbol_key, fact.value)] = seen.get((fact.symbol_key, fact.value), 0) + 1
        for (symbol, kind), count in sorted(seen.items()):
            if count > 1:
                span = next(
                    f.source
                    for f in facts
                    if f.symbol_key == symbol and f.attribute == "existence"
                )
                diagnostics.append(
                    {
                        "range": _range_from_source(span.as_dict() if span else None),
                        "severity": 1,
                        "code": "leviath/duplicate-declaration",
                        "source": DIAGNOSTIC_SOURCE,
                        "message": f"{symbol} is declared {count} times (kind {kind})",
                        "data": {"kind": "duplicate-declaration", "symbolKey": symbol},
                    }
                )

        # 2. a field whose type is not declared and is not a built-in scalar.
        for fact in facts:
            if not fact.attribute.startswith("type.kind"):
                continue
            base = fact.value
            for token in ("[", "]", "!"):
                base = base.replace(token, "")
            if not base or base in BUILTIN_SCALARS or base in type_names:
                continue
            diagnostics.append(
                {
                    "range": _range_from_source(fact.source.as_dict() if fact.source else None),
                    "severity": 1,
                    "code": "leviath/unknown-type",
                    "source": DIAGNOSTIC_SOURCE,
                    "message": f"{fact.symbol_key} references undeclared type {base!r}",
                    "data": {"kind": "unknown-type", "symbolKey": fact.symbol_key, "type": base},
                }
            )

        return diagnostics, facts, sorted(type_names)

    def _conflict_diagnostics(self, text: str) -> List[dict]:
        """Workflow semantic errors and confirmation requirements from GQL-02.

        The shared engine reconciles the current SDL against the current code
        projection over the agreed baseline.  Every conflict becomes a
        diagnostic whose range is the SDL span that produced it, and whose
        ``data.options`` are the confirmation options a principal can choose.
        """
        if self.baseline is None or not self.code_text:
            return []
        try:
            schema_projection = extract_sdl(text, "schema.graphql").projection
            code_projection = extract_python(self.code_text, self.code_path).projection
        except Exception:  # noqa: BLE001 - a broken side is reported by the SDL checks
            return []
        outcome = Reconciler(self.baseline).reconcile(schema_projection, code_projection)
        diagnostics: List[dict] = []
        for conflict in outcome.plan.conflicts:
            side = conflict.right if conflict.right.get("projection") == "schema" else conflict.left
            other = conflict.left if side is conflict.right else conflict.right
            source = side.get("source")
            schema_name = side.get("value")
            code_name = other.get("value")
            data = {
                "kind": "reconcile-conflict",
                "conflictCode": conflict.code,
                "symbolKey": conflict.symbol_key,
                "attribute": conflict.attribute,
                "options": list(conflict.options),
                "schemaValue": schema_name,
                "otherProjection": other.get("projection"),
                "otherValue": code_name,
                "requiresConfirmation": True,
            }
            if conflict.attribute == "name" and isinstance(code_name, str) and isinstance(schema_name, str):
                data["replacement"] = code_name
                data["replacementRange"] = _range_from_source(source)
            diagnostics.append(
                {
                    "range": _range_from_source(source),
                    "severity": 2,
                    "code": f"leviath/reconcile-{conflict.code}",
                    "source": DIAGNOSTIC_SOURCE,
                    "message": (
                        f"{conflict.message}; confirmation required "
                        f"({len(conflict.options)} option(s)): {conflict.options[0] if conflict.options else 'none'}"
                    ),
                    "data": data,
                }
            )
        return diagnostics

    def diagnostics(self, uri: str) -> Tuple[str, List[dict]]:
        """The full diagnostic set for a document, plus a content-based result id."""
        doc = self.document(uri)
        diagnostics, _facts, _types = self._sdl_semantic_diagnostics(doc.text)
        diagnostics.extend(self._conflict_diagnostics(doc.text))
        diagnostics.sort(key=lambda d: (d["range"]["start"]["line"], d["range"]["start"]["character"], d["code"]))
        # A content-based result id: the version plus a digest of the text, so a
        # client can tell "unchanged" from "changed" without re-reading content.
        digest = hashlib.sha256(doc.text.encode("utf-8")).hexdigest()[:16]
        result_id = f"v{doc.version}:{digest}"
        return result_id, diagnostics

    # -- code actions -------------------------------------------------------
    def code_actions(self, uri: str, params: dict) -> List[dict]:
        """Text-only quick fixes for the diagnostics in the request context.

        Only diagnostics that carry a confirmation requirement produce an
        action, and every action is a pure ``TextEdit`` applied by the client.
        The server returns no ``command``, so applying an action cannot run
        anything.
        """
        self.document(uri)
        context = params.get("context") or {}
        incoming = context.get("diagnostics")
        if not incoming:
            _result_id, incoming = self.diagnostics(uri)
        actions: List[dict] = []
        for diagnostic in incoming:
            data = diagnostic.get("data") or {}
            if data.get("kind") != "reconcile-conflict":
                continue
            replacement = data.get("replacement")
            replacement_range = data.get("replacementRange")
            if not isinstance(replacement, str) or not replacement_range:
                continue
            symbol = data.get("symbolKey", "?")
            actions.append(
                {
                    "title": f"Use {replacement!r} for {symbol} (keep the {data.get('otherProjection')} version)",
                    "kind": "quickfix",
                    "diagnostics": [diagnostic],
                    "isPreferred": True,
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": replacement_range,
                                    "newText": replacement,
                                }
                            ]
                        }
                    },
                    # Provenance only: the client applies the edit, nothing runs.
                    "data": {
                        "symbolKey": symbol,
                        "conflictCode": data.get("conflictCode"),
                        "requiresConfirmation": True,
                        "options": data.get("options", []),
                    },
                }
            )
        return actions

    # -- dispatch -----------------------------------------------------------
    def handle(self, message: dict) -> Optional[dict]:
        """Handle one JSON-RPC message; return a response, or ``None``."""
        method = message.get("method")
        params = message.get("params") or {}
        request_id = message.get("id")
        if method is None:
            return None
        if request_id is None:
            # A notification: a stale edit is reported, never applied.
            try:
                self._handle_notification(method, params)
            except StaleEdit as exc:
                self.notifications.append(
                    {
                        "jsonrpc": "2.0",
                        "method": "window/showMessage",
                        "params": {"type": 1, "message": str(exc)},
                    }
                )
            except ProtocolError as exc:
                self.notifications.append(
                    {
                        "jsonrpc": "2.0",
                        "method": "window/showMessage",
                        "params": {"type": 1, "message": str(exc)},
                    }
                )
            return None
        try:
            result = self._handle_request(method, params)
        except StaleEdit as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": STALE_VERSION_CODE, "message": str(exc)}}
        except ProtocolError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001 - reported as a JSON-RPC internal error
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }
        if result is None:
            return {"jsonrpc": "2.0", "id": request_id, "result": None}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _handle_notification(self, method: str, params: dict) -> None:
        if method == "textDocument/didOpen":
            item = params["textDocument"]
            self.open_document(
                item["uri"], item.get("text", ""), int(item.get("version", 0)), item.get("languageId", "graphql")
            )
        elif method == "textDocument/didChange":
            item = params["textDocument"]
            changes = params.get("contentChanges") or []
            text = changes[-1]["text"] if changes else ""
            self.change_document(item["uri"], text, int(item.get("version", 0)))
        elif method in ("initialized", "exit", "$/cancelRequest", "textDocument/didClose"):
            return None
        else:
            return None

    def _handle_request(self, method: str, params: dict) -> Any:
        if method == "initialize":
            self.load_baseline(params.get("initializationOptions") or {})
            return {
                "capabilities": {
                    "textDocumentSync": {"openClose": True, "change": 1},
                    "diagnosticProvider": {
                        "interpreterId": "leviath-schema-ls",
                        "identifier": "leviath",
                        "interFileDependencies": False,
                        "workspaceDiagnostics": False,
                    },
                    "codeActionProvider": {"codeActionKinds": ["quickfix"]},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        if method == "shutdown":
            self.shutdown_requested = True
            return None
        if method == "textDocument/didChange":
            # A stale edit sent as a *request* gets an explicit JSON-RPC error,
            # which is what lets a client see the rejection.
            item = params["textDocument"]
            changes = params.get("contentChanges") or []
            text = changes[-1]["text"] if changes else ""
            self.change_document(item["uri"], text, int(item.get("version", 0)))
            return {"applied": True, "version": int(item.get("version", 0))}
        if method == "textDocument/diagnostic":
            uri = params["textDocument"]["uri"]
            result_id, items = self.diagnostics(uri)
            previous = params.get("previousResultId")
            if previous is not None and previous == result_id:
                return {"kind": "unchanged", "resultId": result_id}
            return {"kind": "full", "resultId": result_id, "items": items}
        if method == "textDocument/codeAction":
            uri = params["textDocument"]["uri"]
            return self.code_actions(uri, params)
        raise ProtocolError(-32601, f"method not found: {method}")


def _serve(server: SchemaServer, reader, writer) -> None:
    """Read, dispatch and answer until EOF, ``shutdown`` or ``exit``."""
    while True:
        try:
            message = _read_message(reader)
        except Exception:  # noqa: BLE001 - a malformed frame ends the loop cleanly
            break
        if message is None:
            break
        response = server.handle(message)
        if response is not None:
            _write_message(writer, response)
        if message.get("method") == "exit":
            break


def run_stdio(stdin=None, stdout=None) -> int:
    """Run the server on the given binary streams (default: stdin/stdout)."""
    reader = stdin if stdin is not None else sys.stdin.buffer
    writer = stdout if stdout is not None else sys.stdout.buffer
    server = SchemaServer()
    try:
        _serve(server, reader, writer)
    except Exception:  # noqa: BLE001 - never crash the editor; report and exit non-zero
        traceback.print_exc(file=sys.stderr)
        return 1
    return 0


def serve_in_thread(reader, writer) -> threading.Thread:
    """Run the server in a background thread (used by the protocol test)."""
    thread = threading.Thread(target=_serve, args=(SchemaServer(), reader, writer), daemon=True)
    thread.start()
    return thread
