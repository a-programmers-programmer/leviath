"""GQL-04 round-trip tests: the typed-code language server and its Python adapter.

Written RED first: the package ``leviath_typed_ls`` does not exist yet, so every
test below fails at import.  The suite asserts exactly the GQL-04 "Done means"
line:

* a Python edit updates the schema projection;
* a schema edit produces concrete code edits;
* callable signatures, nullability and confirmation wrappers round-trip with
  source locations;
* unsupported dynamic constructs are reported explicitly, and an omitted
  obligation BLOCKS;
* the accepted contract fingerprint is re-verified and drift fails loudly.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[2]      # .../repo/python
REPO = PKG_ROOT.parent
sys.path.insert(0, str(PKG_ROOT))

CONTRACT_DIR = REPO / "python" / "leviath_typed_ls" / "fixtures" / "contract"
PINNED = "ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559"
PIN_HOST_DIGEST = "8c3056bbb91a2fd52af261c25768bc9645ccde24620a59b6a2e3fa32e5f5c9e5"

CODE_V1 = '''\
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class WorkflowStepInput(Protocol):
    simpleEnglishDescription: str


@dataclass
class OAuthRefreshTokenInput(WorkflowStepInput):
    simpleEnglishDescription: str
    token: Confidential | None = None
    expiresAt: datetime
    scope: list[str]


class Dispatcher:
    def dispatch(self, workflow: WorkflowDefinition, dry_run: bool = False) -> WorkflowRun:
        return workflow
'''

# The Python edit: token becomes required and gains a confirmation wrapper.
CODE_V2 = CODE_V1.replace(
    "    token: Confidential | None = None",
    '    token: Confirmation[Confidential, "STEP_RUNNER"]',
)

# The schema edit: token becomes non-null and the confirmation authority moves.
SCHEMA_V2 = '''\
type OAuthRefreshTokenInput implements WorkflowStepInput {
  simpleEnglishDescription: String!
  token: Confidential! @user_confirmation(by: HUMAN_GATE)
  expiresAt: DateTime!
  scope: [String!]!
}
'''

# The schema edit for the callable-signature round trip: the parameter becomes
# nullable, which must produce a concrete code edit on the `workflow` argument.
SCHEMA_SIG = '''\
type Dispatcher {
  dispatch(workflow: WorkflowDefinition, dry_run: Boolean!): WorkflowRun!
}
'''

DYNAMIC = '''\
from __future__ import annotations

from vendor import *


def __getattr__(name):
    return globals()[name]


class Registry:
    def __getattr__(self, item):
        return eval("self." + item)


setattr(Registry, "made", 1)
'''


def test_python_edit_updates_schema_projection():
    from leviath_typed_ls import projection, typed_code_adapter

    before = typed_code_adapter.extract_typed(CODE_V1, "app/ports.py")
    after = typed_code_adapter.extract_typed(CODE_V2, "app/ports.py")
    sb = {(f.symbol_key, f.attribute): f for f in projection.code_facts_to_schema_facts(before.projection.facts)}
    sa = {(f.symbol_key, f.attribute): f for f in projection.code_facts_to_schema_facts(after.projection.facts)}

    key = "OAuthRefreshTokenInput.token"
    assert sb[(key, "type.kind")].value == "confidential?"
    assert (key, "callable.confirmation") not in sb

    assert sa[(key, "type.kind")].value == "confidential"
    assert sa[(key, "callable.confirmation")].value == "by=STEP_RUNNER"

    # the fact carries the source location of the edited line
    source = sa[(key, "callable.confirmation")].source
    assert source.path == "app/ports.py"
    assert CODE_V2.splitlines()[source.line - 1].strip().startswith("token:")

    sdl = projection.render_sdl_projection(after.projection.facts)
    assert "token: Confidential! @user_confirmation(by: STEP_RUNNER)" in sdl


def test_schema_edit_produces_concrete_code_edits():
    from leviath_typed_ls import codegen, projection, schema_reader, typed_code_adapter

    code_facts = typed_code_adapter.extract_typed(CODE_V1, "app/ports.py").projection.facts
    before = projection.code_facts_to_schema_facts(code_facts)
    after = schema_reader.read_sdl(SCHEMA_V2, "schema.graphql").facts

    edits = codegen.schema_edit_to_code_edits(CODE_V1, "app/ports.py", before, after)
    assert edits, "a schema edit must yield concrete code edits"
    for edit in edits:
        assert edit.path == "app/ports.py"
        assert edit.line >= 1 and edit.col_end >= edit.col_start
        assert edit.old_text != edit.new_text

    applied = codegen.apply_edits(CODE_V1, edits)
    assert 'token: Confirmation[Confidential, "HUMAN_GATE"]' in applied

    re_facts = projection.code_facts_to_schema_facts(
        typed_code_adapter.extract_typed(applied, "app/ports.py").projection.facts
    )
    idx = {(f.symbol_key, f.attribute): f for f in re_facts}
    key = "OAuthRefreshTokenInput.token"
    assert idx[(key, "type.kind")].value == "confidential"
    assert idx[(key, "callable.confirmation")].value == "by=HUMAN_GATE"


def test_callable_signature_roundtrips_with_source_location():
    from leviath_typed_ls import codegen, projection, schema_reader, typed_code_adapter

    ex = typed_code_adapter.extract_typed(CODE_V1, "app/ports.py")
    idx = {(f.symbol_key, f.attribute): f for f in ex.projection.facts}

    params = idx[("Dispatcher.dispatch", "callable.params")]
    assert params.value == "workflow:WorkflowDefinition,dry_run:boolean"
    assert idx[("Dispatcher.dispatch", "callable.returns")].value == "WorkflowRun"
    assert idx[("Dispatcher.dispatch", "callable.signature")].value == (
        "(workflow:WorkflowDefinition,dry_run:boolean)->WorkflowRun"
    )
    assert params.source.path == "app/ports.py"
    assert CODE_V1.splitlines()[params.source.line - 1].lstrip().startswith("def dispatch")

    # declared callable port, read statically from the base list
    assert idx[("OAuthRefreshTokenInput", "callable.port")].value == "WorkflowStepInput"

    # schema edit -> concrete code edit on the parameter, then round-trip back
    before = projection.code_facts_to_schema_facts(ex.projection.facts)
    after = schema_reader.read_sdl(SCHEMA_SIG, "schema.graphql").facts
    edits = codegen.schema_edit_to_code_edits(CODE_V1, "app/ports.py", before, after)
    assert edits, "a nullable-parameter schema edit must yield a code edit"
    applied = codegen.apply_edits(CODE_V1, edits)
    assert "workflow: WorkflowDefinition | None" in applied

    re_idx = {
        (f.symbol_key, f.attribute): f
        for f in projection.code_facts_to_schema_facts(
            typed_code_adapter.extract_typed(applied, "app/ports.py").projection.facts
        )
    }
    assert re_idx[("Dispatcher.dispatch", "callable.params")].value == (
        "workflow:WorkflowDefinition?,dry_run:boolean"
    )


def test_dynamic_construct_reported_explicitly():
    from leviath_typed_ls import gate, typed_code_adapter

    ex = typed_code_adapter.extract_typed(DYNAMIC, "app/dynamic.py")
    constructs = {finding.construct for finding in ex.unsupported}
    assert any("__getattr__" in c for c in constructs)
    assert any("globals()" in c for c in constructs)
    assert any("eval" in c for c in constructs)
    assert any("setattr" in c for c in constructs)
    assert any("import *" in c for c in constructs)

    for finding in ex.unsupported:
        assert finding.path == "app/dynamic.py"
        assert finding.line >= 1
        assert finding.reason
        assert finding.obligation

    assert ex.blocked is True
    with pytest.raises(gate.ObligationBlocked):
        gate.require_unblocked(ex)


def test_contract_gate_blocks_on_drift():
    from leviath_typed_ls import gate

    assert gate.verify_contract(CONTRACT_DIR) == PINNED
    assert gate.pin_host_digest(CONTRACT_DIR) == PIN_HOST_DIGEST
    with pytest.raises(gate.ContractDrift):
        gate.verify_contract(CONTRACT_DIR, expected="0" * 64)


# ---------------------------------------------------------------------------
# The language-server process itself.
# ---------------------------------------------------------------------------


def _rpc(proc, method, params, rid):
    message = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    body = message.encode("utf-8")
    proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    proc.stdin.flush()
    headers = {}
    while True:
        line = proc.stdout.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        name, _, value = line.decode("utf-8").partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers["content-length"])
    return json.loads(proc.stdout.read(length).decode("utf-8"))


def test_language_server_process_round_trips_over_stdio():
    env = dict(os.environ, PYTHONPATH=str(PKG_ROOT))
    proc = subprocess.Popen(
        [sys.executable, "-m", "leviath_typed_ls", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        init = _rpc(proc, "initialize", {"rootUri": None}, 1)
        assert init["result"]["serverInfo"]["name"] == "leviath-typed-code-ls"

        _rpc(
            proc,
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": "file:///app/ports.py",
                    "languageId": "python",
                    "version": 1,
                    "text": CODE_V1,
                }
            },
            2,
        )
        first = _rpc(proc, "leviath/schemaProjection", {"uri": "file:///app/ports.py"}, 3)["result"]
        assert "token: Confidential" in first["sdl"]
        assert "token: Confidential!" not in first["sdl"]

        _rpc(
            proc,
            "textDocument/didChange",
            {
                "textDocument": {"uri": "file:///app/ports.py", "version": 2},
                "contentChanges": [{"text": CODE_V2}],
            },
            4,
        )
        second = _rpc(proc, "leviath/schemaProjection", {"uri": "file:///app/ports.py"}, 5)["result"]
        assert "token: Confidential! @user_confirmation(by: STEP_RUNNER)" in second["sdl"]

        edits = _rpc(
            proc,
            "leviath/codeEdits",
            {"uri": "file:///app/ports.py", "schema": SCHEMA_V2},
            6,
        )["result"]
        assert edits["edits"], "a schema edit must produce code edits over the server"

        _rpc(proc, "shutdown", {}, 7)
        _rpc(proc, "exit", {}, 8)
    finally:
        proc.stdin.close()
        returncode = proc.wait(timeout=30)
        stderr = proc.stderr.read().decode("utf-8")
    assert returncode == 0, stderr