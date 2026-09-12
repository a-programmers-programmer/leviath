# fanout/

Mechanically split from `fanout.rs` with text preserved byte-for-byte.

- `framing.rs` — split-round framing for re-entered fan-out stages
- `authoritative.rs` — region-backed fan-out (items from a context region)
- `requests.rs` — tool-parsed fan-out requests and pending-fan-out start
- `collect.rs` — worker collection, merging, and result delivery

All items are `pub(super)` or `pub(crate)`; see `mod.rs` for re-exports.