# stage/

Mechanically split from `stage.rs` with text preserved byte-for-byte.

- `transitions.rs` — parse_transitions, parse_transition_gate, parse_stuck_config
- `mode.rs` — apply_stage_mode, fan_out_number

All items are `pub(super)`; see `mod.rs` for re-exports.