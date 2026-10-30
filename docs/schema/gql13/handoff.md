# Desk, queue, run and decision work orders — runtime is unverified

Nothing in this contract was executed against a live service. The gate is
static. Runtime and caller-compatibility behavior are UNVERIFIED.

Backend: implement resolvers over the EXISTING Desk and queue services. Do not
re-implement queue state. Route every command through the queue store's own
lock and helper functions. Enforce the authenticated desk identity at every
root, filter rows before paging, and enforce editor rights before returning any
conflict detail. Map the store's records into the exact SDL.

- Claim must refuse a row held by another holder and name that holder.
- Completion must require a live claim and refuse queued to done.
- Release must accept a reason and must not require a prior claim.
- Filing must enqueue only in a lane that has a runner, and never for DECIDE.
- Derive status and its three age numbers; never store or write a status.
- Sign cursors with desk identity, ordering version and last identity.
- Default `first` to 20 (50 for thread); reject values outside the cap.
- One bounded query per page; no resolver may loop per row.
- Persist idempotency records atomically with the state change; reject key
  reuse with a different payload.

Frontend: generate operation types from schema.graphql and
operations/*.graphql with the application code generator. Render loading,
empty, permission-unavailable, conflict, unexpected-error and success states.
Use `__typename` for exhaustive result handling and keep a safe unknown-member
fallback. Preserve the same retry key on a retry and take a new key after
revising a command. Refresh on conflict before retrying. Mocks must satisfy
these exact operation documents.

Verification before application acceptance:

- Deny cross-desk slip and row ids, forged cursors and revoked memberships.
- Assert absent and unauthorized rows cannot be told apart by error detail.
- Prove a claim held by another holder is refused and names that holder.
- Prove queued to done is refused with no report ever filed.
- Prove a released row is revivable by its named holder.
- Cover empty, last and deleted-cursor pages, and an invalid `first`.
- Compare query counts for 1 and 100 rows; both stay constant.
- Race two claims on one row; exactly one holder wins.
- Retry a committed command after a simulated timeout; no second transition.
- Reject a retry key with a changed payload.
- Enforce depth 8, at most 20 aliases, HTTP batches at most 5 and cost 1000
  using multiplicative list estimates; calibrate with real traffic.
- Verify generated client types compile and mocks match operation shapes.

Run the contract gate's verify command before either implementation worker
starts and again before accepting the combined app. Contract verification does
not substitute for these tests.

Observed base commit is recorded in the lane report. The gate is
`crates/leviath-cli/agents/graphql-schema/scripts/gate.py`, VERSION 1, run over
`docs/schema/gql13`. No model is called on this path.