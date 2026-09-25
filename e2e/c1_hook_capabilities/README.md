# C1 hook capabilities
Run `lev run e2e/c1_hook_capabilities/granted` : the log shows `c1 hook shell output: c1-ok` and the run completes.
Run `lev run e2e/c1_hook_capabilities/denied` : the on_stage_enter hook fails with a `[denied]` shell error and the run errors out.
The unit tests live in crates/leviath-scripting/src/stage_hook.rs (a_hook_with_a_host_can_call_shell and friends).
