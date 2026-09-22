# T01M34YA4GQG0NACYAKA9WBZRVG: fix E0061/E0063 on the GQL-01 merged tree

Merge of fleet/T01M34W830V3XQZ3PEWMFMJ9T5Y reported "Already up to date": no conflicts.

## Changes (file, line, change)

- crates/leviath-cli/src/commands/mcp/serve_tools/control.rs:75 - ControlRequest::Message initializer: added `parts: Vec::new()` (E0063).
- crates/leviath-cli/src/commands/mcp/serve_tools/run.rs:288 - OutputSpec initializer: added `overwrite_artifacts: None` and `artifacts: Vec::new()`, the values master uses at its other OutputSpec sites (E0063).
- crates/leviath-cli/src/commands/mcp/serve_tools/run.rs:361 - LaunchRequest initializer: added `yolo_profile: None` and `parts: Vec::new()` (E0063).
- crates/leviath-cli/src/commands/mcp/serve_tests.rs:2113 - RunListEntry initializer: added `yolo_profile: None` (E0063).
- crates/leviath-cli/src/daemon/wait_tests.rs:228 - WorldEvent::ToolCallFinished initializer: added `execution_id: "x1".to_string()` (E0063).

No E0061 error exists on this tree (grep count 0). Nothing was changed for E0061.

## Verification

`CARGO_TARGET_DIR=/data/work/leviath/target cargo build --workspace --tests` then
`grep -B3 -A16 -E 'error\[E0061\]|error\[E0063\]'` prints nothing.

## Unfinished

The tree still has 10 errors of other classes (E0425, E0432, E0599, E0603, E0615):
`installed_manifest`, `provenance_line`, `refuse_wait_with_count`, `spawn_and_wait`,
`spawn_and_wait_with`, `InstalledTool::summary`, private `held_checkpoint_warning_for_spawn`,
`params_summary`, `read_path_warning_for_spawn`, `spawn_once`, and `agent_dir` on
`CheckedManifest`. These sit outside this task's named error codes.