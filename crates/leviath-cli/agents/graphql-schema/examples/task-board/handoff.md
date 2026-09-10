# Application work orders — runtime behavior is unverified

Backend: implement scoped task reads and the completeTask transaction described in design.md.
Enforce membership at every root, filter rows before pagination/counting, and enforce editor
rights before returning conflict data. Map storage into the exact schema. Implement cursor
signing and scope validation, a 100-item cap, and default 20 on null or omitted first.
Use a request-local cache keyed by principal/workspace/task; page reads fetch all task fields
in one query. Mutation responses use committed data and invalidate request-local cached rows.
No resolver performs one remote lookup per task. Persist idempotency records atomically with
state changes; expire after 24 hours and reject key reuse with different payloads.

Frontend: generate operation types from schema.graphql and operations/*.graphql using the
application's GraphQL code generator. Render loading, empty, permission-unavailable, conflict,
unexpected-error and success states. Use __typename for exhaustive known results and a safe
unknown-result fallback. Preserve the same retry key on retries; use a new key after revising
a command. Refresh before retrying a conflict. Mocks must satisfy these exact operations.

Verification before application acceptance:
- Deny cross-workspace IDs, forged cursors, viewer mutations and revoked memberships.
- Assert absent and unauthorized task responses cannot be distinguished by error detail.
- Cover empty, last and deleted-cursor pages, invalid first, and concurrent insert semantics.
- Compare query counts for 1 and 100 rows; both page reads should remain a constant count.
- Race two completions with one revision; exactly one transition commits.
- Retry a committed command after a simulated timeout; replay without a second transition.
- Reject a retry key with changed payload and recheck authorization on replay.
- Inject a non-null title resolver fault and check root null bubbling and masked errors.
- Enforce depth 8, at most 20 aliases, HTTP batches at most 5, and cost budget 1000 using
  multiplicative list estimates. Calibrate those initial limits with representative traffic.
- Verify generated client types compile and frontend mocks match operation response shapes.

Run the contract gate's verify command before either implementation worker starts and again
before accepting the combined app. Contract verification does not substitute for these tests.
