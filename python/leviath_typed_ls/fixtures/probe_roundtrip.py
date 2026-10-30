"""Evidence probe: the GQL-04 round trip, the block gate and the contract pin.

Read-only.  Static analysis only.  Prints one line per asserted fact so the
output is the evidence, not a claim about it.
"""

from leviath_typed_ls import codegen, gate, projection, schema_reader, typed_code_adapter

V1 = 'from __future__ import annotations\n\nclass OAuthRefreshTokenInput:\n    token: Confidential | None = None\n'
S = 'type OAuthRefreshTokenInput {\n  token: Confidential! @user_confirmation(by: HUMAN_GATE)\n}\n'
DYN = 'from vendor import *\nclass R:\n    def __getattr__(self, i):\n        return eval("self." + i)\n'

before = projection.code_facts_to_schema_facts(
    typed_code_adapter.extract_typed(V1, "app/ports.py").projection.facts
)
after = schema_reader.read_sdl(S, "schema.graphql").facts

print("=== schema -> code edits ===")
edits = codegen.schema_edit_to_code_edits(V1, "app/ports.py", before, after)
for edit in edits:
    print(f"  {edit.path}:{edit.line}:{edit.col_start}-{edit.col_end} "
          f"{edit.old_text!r} -> {edit.new_text!r}   [{edit.symbol_key}]")

print("=== applied ===")
print(codegen.apply_edits(V1, edits))

print("=== unsupported constructs (explicit) ===")
extraction = typed_code_adapter.extract_typed(DYN, "app/dyn.py")
for finding in extraction.unsupported:
    print("  " + finding.message())
print("  blocked:", extraction.blocked)
try:
    gate.require_unblocked(extraction)
except gate.ObligationBlocked as error:
    print("  BLOCKED:", str(error)[:140], "...")

print("=== contract pin ===")
print("  verify_contract:", gate.verify_contract("leviath_typed_ls/fixtures/contract"))
print("  pin_host_digest:", gate.pin_host_digest("leviath_typed_ls/fixtures/contract"))
try:
    gate.verify_contract("leviath_typed_ls/fixtures/contract", expected="0" * 64)
    print("  DRIFT: NOT DETECTED (defect)")
except gate.ContractDrift as error:
    print("  drift detected:", str(error)[:90], "...")