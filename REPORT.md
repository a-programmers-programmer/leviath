# Report

- Changed `crates/leviath-runtime/src/pipeline/hooks.rs` to run terminal hooks for complete, error, and cancelled status. It runs `on_terminal` after the status-specific hook and marks the entity once.
- Changed `crates/leviath-runtime/src/pipeline/tests.rs` to assert cancellation runs the terminal hook once.
- Changed `crates/leviath-scripting/src/stage_hook.rs` with scripting recognition tests.
- Changed `crates/leviath-core/src/manifest/tests.rs` with a manifest round-trip test.
- Timeout: `std::thread` plus `recv_timeout`, because `run(...)` is synchronous and this system cannot await. Timed-out hooks allow the run to continue.
- Tests: core passed, 1135 tests. CLI check passed. Scripting had 215 pass and one known failure: oracle Rhai script uses reserved `call` keyword. Runtime rerun passed 1967 tests except the known fanout failure. The cancellation test now passes.
- Commit: `fdef43643307b1e59d59a03fd05f265e0364ffde`. Push result: `push_rc=0`.
- Nothing else is left undone.
