# REPORT — GQL-03: Build the schema language server

Merged fleet/T01M2KX1Z7XQY3DDS1GFJJWWWP8 into this branch with no conflicts.
The branch adds a GraphQL SDL language server under python/leviath_schema_ls/.
It speaks LSP JSON-RPC over stdin/stdout with no LSP framework.
It reuses the GQL-02 reconciliation engine read-only for diagnostics.
It adds tests/test_protocol.py, conftest.py, evidence scripts, REPORT.md and EVIDENCE.json.