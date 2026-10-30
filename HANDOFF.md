# HANDOFF — wt/split: make the split compile (stage cut off at iteration cap)

Branch: `wt/split` · base commit `5e89984b` · workdir
`/data/work/leviath-worktrees/split`
No push, no PR, nothing touched upstream — per directive 0070 Part B3.

## DONE

### 1. The 7 E0603 errors are fixed; `cargo build --release` is CLEAN

Edited **one file only**:
`crates/leviath-runtime/src/fanout/mod.rs`

The directory-module header re-exported its children with private globs, so
every `crate::fanout::<item>` call from `pipeline/tools.rs` and `world.rs` was
an E0603. Change (verified in `git diff`):

```diff
-use framing::*;
-use authoritative::*;
-use requests::*;
-use collect::*;
+pub(crate) use requests::*;
+pub(crate) use collect::*;
+
+// `framing` and `authoritative` are listed item by item ...
 pub(crate) use framing::frame_split_round;
 pub(crate) use authoritative::{
     AuthoritativeFanOutPending, prepare_authoritative_fanouts, start_authoritative_fanouts,
 };
```

Plus explanatory comments. `requests::*` and `collect::*` became
`pub(crate)`; the two globs whose items are all already re-exported
explicitly (`framing`, `authoritative`) were dropped as redundant.

**Build result (full, verbatim tail):**

```
warning: profiles for the non root package will be ignored, specify profiles at the workspace root:
package:   /data/work/leviath-worktrees/split/crates/leviath-cli/Cargo.toml
workspace: /data/work/leviath-worktrees/split/Cargo.toml
   Compiling leviath-runtime v0.5.9 (/data/work/leviath-worktrees/split/crates/leviath-runtime)
   Compiling leviath-cli v0.5.9 (/data/work/leviath-worktrees/split/crates/leviath-cli)
   Compiling leviath v0.5.9 (/data/work/leviath-worktrees/split/crates/leviath)
    Finished `release` profile [optimized] target(s) in 5m 42s
```

`RC=0`, wall 343 s, no errors and no warnings emitted (the two lines at the top
are cargo's workspace-profile notice, not a lint). Full log kept at
`target/logs/build.log`; rc/wall in `target/logs/build.done`.

The compiler reported **no further errors** after the visibility fix — steps
1's "fix any further errors" needed nothing more.

### 2. `cargo test` — PARTIAL, and partial in a useful way

Ran `cargo test -p leviath-cli --lib` (the crate that contains the split's
callers):
`RC=101`, wall **80 s**, **4846 tests run**.

`4846 == 4823 passed + 23 failed` from the stated baseline, which is good
evidence the lib suite is the whole of the matrix.

Failures I actually observed in the log (all `FAILED`, list is truncated by
the log read — see LEFT):

```
blueprint_edit::tests::every_bundled_manifest_round_trips_byte_for_byte
bundled::tests::every_bundled_agent_ends_by_handing_something_back
bundled::tests::every_bundled_layout_scales_with_the_model_window
bundled::tests::every_bundled_stage_offers_every_provider_setup_can_configure
commands::dashboard::agents::tests::delete_and_reset_ask_first_and_launch_goes_to_the_new_run_screen
commands::dashboard::agents::tests::a_opens_the_catalog_with_every_source_and_esc_closes_it
commands::dashboard::agents::tests::the_chooser_starts_simple_or_clones_and_checks_the_name
commands::dashboard::graph::tests::a_bundled_blueprint_loads_by_name_and_an_unknown_name_does_not
commands::dashboard::new_run_preview::tests::the_preview_follows_the_selected_agent_and_says_why_when_it_cannot_draw
```

Every one of these is a *bundled-agent-catalog / dashboard-preview* test —
i.e. environment-shaped (they need an installed/bundled agents tree), not
fan-out-shaped. Nothing in the failure list touches `fanout` or
`manifest::stage`. Full log: `target/logs/libtest.log` (485 KB).

## LEFT

1. **Not committed.** `git status --porcelain` is:
   ```
    M crates/leviath-runtime/src/fanout/mod.rs
   ?? HANDOFF.md
   ```
   Step 4 (commit naming the 7 E0603 errors) was not reached.
2. **`cargo test` (workspace) not run** — only `-p leviath-cli --lib`. Need to
   confirm the other crates, and to enumerate all 23 failures and diff them
   against the baseline set.
3. **`cargo fmt` not fixed and `cargo clippy` not run** (step 3). See the trap
   below — `cargo fmt --check` is *already dirty at the base commit*.
4. The final `test result:` summary line for the lib run was not captured, so
   the failure count is corroborated by arithmetic (4846 − 4823) rather than
   read off the summary.

## STATE

- Branch `wt/split`, at `5e89984b`, **one commit ahead of nothing** — my fix is
  **uncommitted in the working tree**.
- Exactly one tracked file modified: `crates/leviath-runtime/src/fanout/mod.rs`.
  No other edits, no stashes, no half-applied patches.
- `HANDOFF.md` is untracked (this file). Its previous contents (the original
  author's split verification) are preserved verbatim in the appendix below.
- Scratch files live only under `target/` (gitignored):
  `target/logs/{build.log,build.done,libtest.log,libtest.done,sleeper.txt}`.
  A helper `tools/runbg.sh` was created then deleted — no trace.
- **No secrets read or written.** Did not open `/data/config.yaml`,
  `/data/profiles/*/config.yaml`, any `.env`. No Gemini/Google model called.

## NEXT — single next action

Fix formatting on the touched file, then run the rest of the verification:

```
cd /data/work/leviath-worktrees/split
cargo fmt -p leviath-runtime        # see trap below
cargo clippy -p leviath-runtime --all-targets -- -D warnings
cargo test                          # whole workspace, capture the summary line
git commit -am "split: make fanout re-exports pub(crate); fixes 7 E0603 private-import errors"
```

Note `cargo build --release` takes **~6 minutes** and the shell tool caps at
60 s, so background it (the previous agent's `setsid` pattern with a
`.done` sentinel under `target/logs/` worked).

## UNVERIFIED / TRAPS

- **TRAP — `cargo fmt --check` is dirty at the BASE commit, not just after my
  edit.** Proven by `git stash` + re-run: `5e89984b` alone reports 5 diffs:
  - `crates/leviath-core/src/manifest/stage/mod.rs:2` (mod/use ordering)
  - `crates/leviath-runtime/src/fanout/collect.rs:210` (fn signature wrap)
  - `crates/leviath-runtime/src/fanout/mod.rs:37, :52, :546`
  So "fmt must stay clean" cannot be satisfied by reverting my edit. My change
  *adds* diffs inside the same `mod.rs` header (rustfmt wants `mod authoritative`
  before `mod collect`, `pub(crate) use collect::*` before `use requests::*`,
  and `use framing::frame_split_round` sorted before the `authoritative::{...}`
  block). Running `cargo fmt` on `fanout/` fixes mine and the three pre-existing
  ones in that directory; the `manifest/stage/mod.rs` one is in another crate.
  Decide whether fixing pre-existing fmt dirt is in scope, but do not let it
  block.
- **UNVERIFIED — that all 9 observed failures are in the 23-failure baseline
  set.** They look baseline-shaped (bundled-catalog/dashboard), and no
  fanout/stage test is among them, but the log read truncated before the
  summary. Confirm by name against the baseline before claiming parity.
- **UNVERIFIED — that dropping the `use framing::*;` / `use authoritative::*;`
  globs is behaviour-preserving** beyond what the build proves. It compiles
  with no warnings, so `mod.rs` corpus does not use any other item from those
  two modules; but a reviewer should confirm the removed globs carried nothing
  the module body needed. If in doubt, restore them as `pub(crate) use` rather
  than removing.
- **UNVERIFIED — `cargo clippy`** was never run. The build was warning-clean,
  which is weak evidence only.
- **UNVERIFIED — workspace-wide `cargo test`**, including whether
  `leviath-runtime` and `leviath-core` unit tests for the split itself pass.

## Appendix — previous handoff (split author), preserved verbatim

Directive 0046 Part D1 (as restated by 0051 Part C3) — CONTINUATION run.

### Split performed

`fanout.rs` (1,143 lines total, 735 moved) → `fanout/` directory module:
- `mod.rs` — remainder (use/type/struct/FanOutWaiting/restore/test … 599 production lines)
- `framing.rs` — FRAMED_PREVIOUS_ITEMS, FrameSplitRoundQuery, frame_split_round, split_round_framing
- `authoritative.rs` — AuthoritativeFanOutPending, prepare_authoritative_fanouts, start_authoritative_fanouts
- `requests.rs` — FanOutRequest, is_fan_out_tool, parse_fan_out_call, config_for, PendingFanOut, start_pending_fan_outs, begin_fan_out
- `collect.rs` — MAX_WORKER_STARTS_PER_PASS, fan_out_collect, MergedWorker, slim_merged_workers, finish_fan_out, finish_stage_fan_out, finish_tool_fan_out, leave_fan_out

`manifest/stage.rs` (1,218 lines total, 432 moved) → `manifest/stage/` directory module:
- `mod.rs` — remainder (786 production lines: parse_stage, parse_stage_hooks, parse_context_transform, parse_nudge_config, key consts, etc.)
- `transitions.rs` — parse_transitions, parse_transition_gate, parse_stuck_config
- `mode.rs` — apply_stage_mode, fan_out_number

### Text preservation

`tools/check_split_preserves_text.py` verifies: every top-level item in each
original maps byte-for-byte into exactly one new file. The only allowable
differences are:

- `mod` / `use` / `pub(crate) use` lines added to mod.rs
- `use super::*;` added to child files
- `//!` doc headers on child files
- `pub(super)` prepended to declaration lines of formerly-private items that
  child modules reference (FRAMED_PREVIOUS_ITEMS, FrameSplitRoundQuery,
  split_round_framing, finish_fan_out, finish_stage_fan_out,
  finish_tool_fan_out, leave_fan_out, fan_out_number; also five items left
  in fanout/mod.rs: ActiveWorker, authoritative_items,
  blueprint_fan_out_max_items, start_worker, worker_terminal_result)

FINAL VERDICT (reproducible — run `python3 tools/check_split_preserves_text.py`):
```
  Original: 42 items in crates/leviath-runtime/src/fanout.rs
  PASS: all 42 items preserved byte-for-byte
  Original: 21 items in crates/leviath-core/src/manifest/stage.rs
  PASS: all 21 items preserved byte-for-byte
All checks passed: split is text-preserving.
```

`tools/_final_check.py` also passes (same as 15:29Z, no changed verdict).

### Commit

Branch `wt/split`, commit `5e89984b`, NOT pushed.