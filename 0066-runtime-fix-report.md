# 0066-runtime-fix-report — final-output-at-cap fix (0069 Part C2)

- **run**: ceo-bus-executor Qwen worker (0069 Part C2 continuation)
- **issued**: 2026-09-12T17:40:00Z
- **worktree**: `/data/work/leviath-worktrees/final-output-cap`
- **branch**: `fix/final-output-at-cap`
- **commit**: `d872a54d2f85269e9f8a1f7c956f685d003cd56f`
- **push**: `app` (a-programmers-programmer/leviath), branch `sync/upstream-0.6.0`, fast-forward only, no force
- **PR**: gh not available; compare URL:
  `https://github.com/a-programmers-programmer/leviath/compare/sync/upstream-0.6.0...d872a54d`

## Summary

Fixed the runtime so a terminal/final-output stage that exhausts its
`max_iterations` budget keeps the output it already produced (a
success/complete terminal state carrying that output), instead of setting
`AgentStatus::Error`.

The fix is a one-location change: `require_final_output` in `requirements.rs`
now adopts the model's last conversation turn as `FinalOutput` when the stage
ended on `StageOutcome::MaxIterations`.  The `transition.rs` `Terminal` arm
(which already checks `submitted.is_none()`) then sees the present
`FinalOutput` and reports `Complete` rather than `Error`.

The `f42efcd0` "owed an answer and gave none is not a success" behaviour is
preserved: when the required-output re-entry budget is exhausted without
`MaxIterations` (the model kept refusing to call `submit_output`), the
`outcome.is_some()` branch is never entered, and the re-entry-exhausted
branch records `output_forced` and proceeds without an output — still
producing `Error` downstream.

## Files changed

1. `crates/leviath-runtime/src/pipeline/requirements.rs` — the fix (+48/-6)
2. `crates/leviath-runtime/src/pipeline/tests.rs` — the test (+52)

## RED → GREEN

### RED (commit `dd517e69`, test only)
```
test result: FAILED. 0 passed; 1 failed; 0 ignored; 0 measured; 1816 filtered out; finished in 0.00s
```
The test `pipeline::tests::model_parts::a_final_output_stage_that_hits_its_iteration_cap_keeps_the_output_it_has` panicked:
```
a capped final-output stage must keep the output it produced
```

### GREEN (commit `d872a54d`, test + fix)
```
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 1816 filtered out; finished in 0.00s
```

## Builder verification

### Runtime suite (`cargo test -p leviath-runtime`)
```
test result: FAILED. 1814 passed; 3 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.56s
```
- 1813 → 1814 passes: the +1 is the new test
- 3 pre-existing failures at `15dc182e` (unchanged):
  - `fanout::tests::authoritative_fanout_starts_without_provider_inference`
  - `pipeline::resolve::tests::head_source_tells_an_override_from_the_blueprints_own_choice`
  - `pipeline::resolve::tests::resolve_stages_notes_a_substitution_and_is_silent_otherwise`

### CLI lib suite (`cargo test -p leviath-cli --lib`)
```
test result: FAILED. 4821 passed; 25 failed; 0 ignored; 0 measured; 0 filtered out; finished in 36.03s
```
- Baseline (15dc182e, measured just before this report): **24 failures**, 4822 passed
- Our commit: **25 failures**, 4821 passed
- The single delta (`commands::serve::providers::tests::the_default_admin_points_at_this_machine`) is the known host-dependent flake documented in the 0046 test baseline (row 8 note). Re-running it alone passes:
  ```
  test commands::serve::providers::tests::the_default_admin_points_at_this_machine ... ok
  ```
- The 24-failure set includes `blueprint_edit::tests::every_bundled_manifest_round_trips_byte_for_byte`, which appeared on the builder between the 0046 baseline measurement and now — also pre-existing and unrelated to our change.
- **No new failures introduced.** The failure set is a subset of `{baseline_24} ∪ {the_default_admin_points_at_this_machine}`, and the latter is a known flake.

## Git status
```
?? new_test_insert.rs   (stray sed artifact, removed before final commit)
```
After cleanup: `git status --porcelain` is **empty**.

```
d872a54d fix(runtime): keep the final output when a stage hits its iteration cap
dd517e69 RED: add failing test for final-output stage hitting its iteration cap
15dc182e fix: satisfy fmt and clippy on the synced tree
```

## PR
`gh` is not available on hermes-gateway.
Compare URL: `https://github.com/a-programmers-programmer/leviath/compare/sync/upstream-0.6.0...d872a54d`