"""Entry point: ``python -m leviath_typed_ls serve`` — the typed-code LSP server.

A minimal JSON-RPC 2.0 server over stdio.  It never imports or executes the
artifact under edit: every answer is derived from static analysis (``ast``) and
the two projections (code <-> schema), through GQL-02's accepted engine.

Standard methods
----------------
``initialize`` / ``shutdown`` / ``exit``
    Lifecycle.  ``initialize`` reports ``serverInfo.name``.
``textDocument/didOpen``, ``textDocument/didChange``
    Full-document sync; the buffer is kept per URI.

Custom requests
---------------
``leviath/schemaProjection {uri}``
    -> ``{"sdl": <the schema this Python file implies>, "unsupported": [...]}``
``leviath/codeEdits {"uri": ..., "schema": <SDL text>}``
    -> ``{"edits": [ {path, line, col_start, col_end, old_text, new_text, ...} ]}``

A response is written for every message that carries an ``id`` (including
notifications re-declared as requests by a client), so a caller that reads one
reply per send is never left waiting.
"""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

from . import codegen, projection, schema_reader, typed_code_adapter

#: The name the server reports at ``initialize``.
SERVER_NAME = "leviath-typed-code-ls"
SERVER_VERSION = "0.1.0"


def _uri_to_path(uri: Optional[str]) -> str:
    """``file:///app/ports.py`` -> ``/app/ports.py`` (non-file URIs unchanged)."""
    if not uri:
        return ""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    return unquote(parsed.path) or unquote(parsed.netloc + parsed.path)


def _read_message(stream: BinaryIO) -> Optional[Dict[str, Any]]:
    """Read one Content-Length-framed JSON-RPC message; ``None`` at EOF."""
    headers: Dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        name, _, value = line.decode("utf-8").partition(":")
        headers[name.strip().lower()] = value.strip()
    if "content-length" not in headers:
        return None
    length = int(headers["content-length"])
    if length <= 0:
        return None
    body = stream.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _send(stream: BinaryIO, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    stream.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    stream.flush()


class Server:
    """Stateful request handler: open buffers in, projections and edits out."""

    def __init__(self) -> None:
        self.documents: Dict[str, Dict[str, Any]] = {}

    # -- helpers ---------------------------------------------------------

    def _text(self, uri: Optional[str]) -> Tuple[str, str]:
        document = self.documents.get(uri or "", {})
        return document.get("text", ""), _uri_to_path(uri)

    def _store(self, params: Dict[str, Any], text: str) -> None:
        uri = params.get("textDocument", {}).get("uri") or params.get("uri")
        if uri:
            self.documents[uri] = {"text": text, "version": params.get("textDocument", {}).get("version")}

    # -- dispatch --------------------------------------------------------

    def handle(self, method: Optional[str], params: Dict[str, Any]) -> Tuple[Any, bool]:
        """Return ``(result, should_exit)`` for one request."""
        if method == "initialize":
            return {
                "capabilities": {"textDocumentSync": 1},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }, False

        if method == "initialized":
            return None, False

        if method == "textDocument/didOpen":
            self._store(params, params.get("textDocument", {}).get("text", ""))
            return None, False

        if method == "textDocument/didChange":
            changes = params.get("contentChanges") or []
            if changes:
                self._store(params, changes[-1].get("text", ""))
            return None, False

        if method == "leviath/schemaProjection":
            text, path = self._text(params.get("uri"))
            extraction = typed_code_adapter.extract_typed(text, path or "module.py")
            return {
                "sdl": projection.render_sdl_projection(extraction.projection.facts),
                "unsupported": [finding.as_dict() for finding in extraction.unsupported],
                "blocked": extraction.blocked,
            }, False

        if method == "leviath/codeEdits":
            text, path = self._text(params.get("uri"))
            schema_text = params.get("schema", "")
            before = projection.code_facts_to_schema_facts(
                typed_code_adapter.extract_typed(text, path or "module.py").projection.facts
            )
            after = schema_reader.read_sdl(schema_text, "schema.graphql").facts
            edits = codegen.schema_edit_to_code_edits(text, path or "module.py", before, after)
            return {"edits": [edit.as_dict() for edit in edits]}, False

        if method == "shutdown":
            return None, False

        if method == "exit":
            return None, True

        return None, False


def serve(stdin: BinaryIO, stdout: BinaryIO) -> int:
    """Run the server loop until ``exit`` or EOF; return the process exit code."""
    server = Server()
    while True:
        message = _read_message(stdin)
        if message is None:
            break
        method = message.get("method")
        rid = message.get("id")
        try:
            result, should_exit = server.handle(method, message.get("params") or {})
        except Exception as exc:  # noqa: BLE001 - reported to the client, not swallowed
            if rid is not None:
                _send(
                    stdout,
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
                    },
                )
            continue
        if rid is not None:
            _send(stdout, {"jsonrpc": "2.0", "id": rid, "result": result})
        if should_exit:
            break
    return 0


def main(argv: Optional[list] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "serve":
        return serve(sys.stdin.buffer, sys.stdout.buffer)
    print("usage: python -m leviath_typed_ls serve", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())