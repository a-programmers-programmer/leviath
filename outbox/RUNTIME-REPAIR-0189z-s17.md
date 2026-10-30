# RUNTIME REPAIR 0189z / 0191 — enable the wedge watchdog by default

Repo: a-programmers-programmer/leviath — workdir /data/work/leviath, HEAD 36025f69.
Not committed. Not pushed. Changes left in the working tree.

## Defect (one line)

The wedge watchdog shipped disabled (`DEFAULT_WEDGE_TIMEOUT_SECS = 0`), so a wedged run stays
`running` in `meta.json` and holds its inference slot for the life of the daemon, starving the
factory of slots until each executor pass grows past its bound.

## Before / after

| | value |
|---|---|
| before | `0` (watchdog off) |
| after | `300` (watchdog on, five minutes) |

`[limits] wedge_timeout_secs` still overrides the default. An operator who wants the old
behaviour sets `0`. No config file was touched: `/data/.leviath/config.toml` is refused to
every run by `[security] lock_permission_files`, so the code default is the in-band fix.

## Files changed

    crates/leviath-runtime/src/pipeline/wedge.rs   constant 0 -> 300, doc comment rewritten
    crates/leviath-cli/src/config/mod.rs           one assertion 0 -> 300

### git diff --stat

     crates/leviath-cli/src/config/mod.rs         |  7 ++++---
     crates/leviath-runtime/src/pipeline/wedge.rs | 14 +++++++-------
     2 files changed, 11 insertions(+), 10 deletions(-)

### git status (tracked only)

     M crates/leviath-cli/src/config/mod.rs
     M crates/leviath-runtime/src/pipeline/wedge.rs

### sha256sum crates/leviath-runtime/src/pipeline/wedge.rs

    before (git show HEAD:...)  8f028c331616c57a2c2e96ccc2511dd3d592071ef905a7ffbfe21121f0ab502b  -
    after                       3daa65b57c71eea327e2ac40134daa6f84fb667e24e95962b5737f9a7048ed20  crates/leviath-runtime/src/pipeline/wedge.rs

## TDD

The directive's filter matched no test, so I widened it as the directive allows:

    cargo test -p leviath-cli --lib wedge_timeout -- --list   ->  0 tests, 0 benchmarks

### tdd_cmd (exact, used for both RED and GREEN)

    cd /data/work/leviath && cargo test -p leviath-cli --lib limits -- --nocapture

Test pinning the default: `config::tests::limits_absent_section_uses_defaults`
(`crates/leviath-cli/src/config/mod.rs`, assertion at line 2432).

### RED — assertion changed to 300, constant still 0

Verified the constant was still `0` at RED time:

    103:pub const DEFAULT_WEDGE_TIMEOUT_SECS: u64 = 0;

    thread 'config::tests::limits_absent_section_uses_defaults' (639958) panicked at crates/leviath-cli/src/config/mod.rs:2432:9:
    assertion `left == right` failed
      left: 0
     right: 300
    note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace

    test config::tests::limits_absent_section_uses_defaults ... FAILED
    test result: FAILED. 44 passed; 1 failed; 0 ignored; 0 measured; 4803 filtered out; finished in 0.18s
    EXIT=101

### GREEN — constant set to 300

    103:pub const DEFAULT_WEDGE_TIMEOUT_SECS: u64 = 300;

    test config::tests::limits_absent_section_uses_defaults ... ok
    test result: ok. 45 passed; 0 failed; 0 ignored; 0 measured; 4803 filtered out; finished in 0.14s
    EXIT=0

## Phase 3 — the three protected assertions still pass

I did not edit them. All three are in passing tests:

    crates/leviath-cli/src/commands/setup/state/mod.rs:~2628 (expects 44)   PASS
    crates/leviath-cli/src/commands/setup/state/mod.rs:~2690 (expects 300)  PASS
    crates/leviath-cli/src/config/mod.rs:~2444 (expects 300)                PASS

Named evidence:

    test commands::setup::state::tests::the_watchdog_limits_store_zero_and_keep_the_default_when_blank ... ok
    test commands::setup::state::tests::limits_are_written_back_including_the_zero_guard ... ok
    test commands::setup::state::tests::every_limits_field_is_written_back ... ok
    test config::tests::limits_partial_section_fills_the_other_default ... ok
    test config::tests::the_wedge_watchdog_is_off_until_it_is_configured ... ok

The table-driven `Unreachable` wedge tests still pass, including the explicit zero-disables case:

    test pipeline::wedge::tests::a_zero_timeout_disables_the_watchdog ... ok
    test pipeline::wedge::tests::a_run_no_system_can_reach_is_failed ... ok
    test pipeline::wedge::tests::a_run_blocked_on_a_person_is_left_to_issue_204 ... ok
    test pipeline::wedge::tests::a_finished_run_with_no_record_is_a_no_op ... ok
    test pipeline::wedge::tests::a_paused_run_is_never_failed_and_loses_its_record ... ok
    test pipeline::wedge::tests::an_agent_without_a_stage_log_still_fails ... ok
    test pipeline::wedge::tests::a_recovered_agent_starts_a_fresh_clock ... ok
    test pipeline::wedge::tests::inside_the_grace_period_it_is_only_recorded ... ok
    test pipeline::wedge::tests::a_finished_run_is_left_to_the_host ... ok
    test pipeline::wedge::tests::every_phase_marker_exempts_its_agent ... ok
    test pipeline::wedge::tests::a_world_without_the_resource_is_off_by_default ... FAILED

## BLOCKER — a second pin of the old default, in a file I may not edit

The directive listed the config assertion as the only pin. That is not correct. There is a
second pin, and it is the one remaining failure:

    crates/leviath-runtime/src/pipeline/wedge/tests.rs:373  fn a_world_without_the_resource_is_off_by_default()
      line 379: assert_eq!(status_of(&world, e), AgentStatus::Active);
      line 380: assert_eq!(DEFAULT_WEDGE_TIMEOUT_SECS, 0);
      line 381: assert_eq!(WedgeTimeout::default().0, DEFAULT_WEDGE_TIMEOUT_SECS);

It fails first at line 379, on behaviour, not only on the constant:

    thread 'pipeline::wedge::tests::a_world_without_the_resource_is_off_by_default' (635917) panicked at crates/leviath-runtime/src/pipeline/wedge/tests.rs:379:5:
    assertion `left == right` failed
      left: Error { message: "run stopped being driven in stage 'implement': 10000s with nothing in flight and no marker any system acts on, so it can never move again. Failing it releases the capacity it was holding. This is a bug in Leviath - please report it with the daemon log" }
     right: Active

This test's name and body encode the old decision: a world that never installed the resource
must leave the run `Active`. Enabling the default inverts that by design. The failure is the
change working, not a bug in the change.

Per the hard constraint "if you believe another file needs editing, STOP and write down why
instead", I stopped and did not edit it. I did not delete, skip, or weaken it.

The follow-up edit needs authorising. It is the same one-line edit the directive already
authorised for config/mod.rs, applied to the runtime's own pin:

  - rename the test to say the default is on, and
  - expect `AgentStatus::Error` plus `DEFAULT_WEDGE_TIMEOUT_SECS == 300`.

`a_zero_timeout_disables_the_watchdog` already covers the off case, so coverage of "off"
survives.

## Suite results

Both suites had pre-existing failures at HEAD. I captured HEAD baselines to separate them from
my change.

### cargo test -p leviath-cli --lib  (tail)

        commands::serve::blueprints::tests::delete_blueprint_removal_failure_returns_500
        commands::serve::blueprints::tests::list_blueprints_empty_path_returns_ok
        commands::serve::blueprints::tests::list_blueprints_with_agent_returns_it
        commands::serve::blueprints::tests::update_blueprint_write_failure_returns_500
        commands::serve::fs::tests::list_dirs_unreadable_directory_is_reported
        commands::serve::providers::tests::the_default_admin_points_at_this_machine
        commands::serve::runs::tests::a_run_that_cannot_be_removed_reports_the_failure
        runstate::tests::tail_file_open_permission_denied_returns_empty
        tools::policy_tests::the_bundled_agents_resolve_unchanged
        tui::flowgraph::model::tests::every_bundled_agent_builds_a_valid_stage_graph

        test result: FAILED. 4820 passed; 28 failed; 0 ignored; 0 measured; 0 filtered out; finished in 37.45s

        error: test failed, to rerun pass `-p leviath-cli --lib`

CLI counts: HEAD baseline 4821 passed / 27 failed. With my change 4820 passed / 28 failed.
The one extra name is `commands::serve::providers::tests::the_default_admin_points_at_this_machine`.
It is flaky, not mine: it passes 3/3 in isolation and references no wedge or limits value.

    test result: ok. 1 passed; 0 failed; ... (x3)

No wedge, limits, or `config::tests` failure remains in the CLI set: grep count 0.

### cargo test -p leviath-runtime --lib  (tail)

    assertion `left == right` failed
      left: Error { message: "run stopped being driven in stage 'implement': 10000s with nothing in flight and no marker any system acts on, so it can never move again. Failing it releases the capacity it was holding. This is a bug in Leviath - please report it with the daemon log" }
     right: Active


    failures:
        fanout::tests::authoritative_fanout_starts_without_provider_inference
        pipeline::resolve::tests::head_source_tells_an_override_from_the_blueprints_own_choice
        pipeline::resolve::tests::resolve_stages_notes_a_substitution_and_is_silent_otherwise
        pipeline::wedge::tests::a_world_without_the_resource_is_off_by_default

    test result: FAILED. 1817 passed; 4 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.60s

    error: test failed, to rerun pass `-p leviath-runtime --lib`

Runtime counts: HEAD baseline 1818 passed / 3 failed. With my change 1817 passed / 4 failed.
Baseline failures (pre-existing, unrelated to this pass):

    fanout::tests::authoritative_fanout_starts_without_provider_inference
    pipeline::resolve::tests::head_source_tells_an_override_from_the_blueprints_own_choice
    pipeline::resolve::tests::resolve_stages_notes_a_substitution_and_is_silent_otherwise

Diff of failure sets, baseline vs mine:

    3a4
    >     pipeline::wedge::tests::a_world_without_the_resource_is_off_by_default

So the change adds exactly one failure: the second pin described in the BLOCKER section.

## Scope

Constant plus doc comment in wedge.rs, and the one assertion in config/mod.rs. Nothing else.
No new scheduler, queue, framework, watchdog, or verifier — the watchdog already existed. No
config or permission file edit. No commit, push, or ledger edit. No probe, no infrastructure
change, no daemon restart. I did not implement blob beb3f81becd6bd4730ff0fdc2a9a5b402c2621f5.

## Conclusion

The constant change is done and the CLI default is GREEN. It is **not quite sufficient on its
own**: one runtime test pins the old default in a file outside my allowed scope. Per the
directive I stopped rather than expand scope. One authorised one-line test update in
`crates/leviath-runtime/src/pipeline/wedge/tests.rs` closes the pass.
