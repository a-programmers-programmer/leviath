"""Evidence probe: drive the real server process over stdio.

Starts ``python -m leviath_typed_ls serve``, speaks JSON-RPC 2.0 to it, and
prints each reply.  Proves the process is runnable and that a Python edit
changes the schema projection it reports.
"""

import json
import os
import subprocess
import sys

V1 = (
    "from __future__ import annotations\n\n"
    "class Dispatcher:\n"
    "    def dispatch(self, workflow: WorkflowDefinition) -> WorkflowRun:\n"
    "        return workflow\n"
)
V2 = V1.replace(
    "workflow: WorkflowDefinition", "workflow: WorkflowDefinition | None"
)

env = dict(os.environ, PYTHONPATH=os.getcwd())
proc = subprocess.Popen(
    [sys.executable, "-m", "leviath_typed_ls", "serve"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
)


def rpc(method, params, rid):
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode()
    proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    proc.stdin.flush()
    headers = {}
    while True:
        line = proc.stdout.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        name, _, value = line.decode("utf-8").partition(":")
        headers[name.strip().lower()] = value.strip()
    return json.loads(proc.stdout.read(int(headers["content-length"])))


print("initialize:", rpc("initialize", {"rootUri": None}, 1)["result"]["serverInfo"])
rpc(
    "textDocument/didOpen",
    {"textDocument": {"uri": "file:///app/ports.py", "languageId": "python", "version": 1, "text": V1}},
    2,
)
one = rpc("leviath/schemaProjection", {"uri": "file:///app/ports.py"}, 3)["result"]
print("=== projection after V1 ===")
print(one["sdl"])

rpc(
    "textDocument/didChange",
    {"textDocument": {"uri": "file:///app/ports.py", "version": 2}, "contentChanges": [{"text": V2}]},
    4,
)
two = rpc("leviath/schemaProjection", {"uri": "file:///app/ports.py"}, 5)["result"]
print("=== projection after V2 (edit added `| None`) ===")
print(two["sdl"])

edits = rpc(
    "leviath/codeEdits",
    {"uri": "file:///app/ports.py", "schema": "type Dispatcher {\n  dispatch(workflow: WorkflowDefinition!): WorkflowRun!\n}\n"},
    6,
)["result"]
print("=== codeEdits for a schema edit ===")
print(json.dumps(edits, indent=2))

rpc("shutdown", {}, 7)
rpc("exit", {}, 8)
proc.stdin.close()
print("server exit code:", proc.wait(timeout=20))