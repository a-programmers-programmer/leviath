# Desk, queue, run and decision contract decisions

This is a NEW API in mode `new`. The observed repo schemas are unrelated APIs,
so evolution is not claimed. "Baseline" here means CALLER compatibility with the
existing Desk and claim-queue HTTP callers, which the field vocabulary mirrors.

Slip is the read aggregate. The queue row stays its own type because the queue
store owns queue state; folding it into Slip would put two state machines under
one identity and let a client read queue state off a slip. Thread entries stay a
third type because they are append-only and paged separately.

Separate commands were chosen over a generic `transition(input:)` mutation. A
generic transition would hide the lease rule (`report != done`) and let a client
propose an invalid jump such as queued to done. Each command names its own
invariant instead, and results are typed because conflicts are normal cases.

`claimQueueItem` requires a holder. The observed store refuses a blind claim and
refuses a named-kind row to a holder that is not that worker. A holder that is
inferred rather than supplied would make that refusal unwritable, so holder is
required and `kind` is an optional match filter that a wrong value refuses.

`completeQueueItem` takes a holder and requires a live claim. That encodes
`report != done`: a row that was never claimed cannot be completed, and a claim
held by somebody else is refused. Releasing is deliberately allowed to be more
permissive, because saying work failed is legal for unstarted work.

Reads are scoped by the authenticated desk identity from the principal, never
from a caller-supplied owner or product. The product, owner and delivery fields
are ROUTING, not authorization.

Cursor order is ascending immutable identity with binary comparison; the
identity is its own tie-breaker. The signed opaque cursor binds desk identity,
ordering version and last identity, so a cursor from another slip, desk or
ordering is rejected without disclosing data. Reads are live, not
snapshot-consistent: an insert before the current cursor needs a refresh.
Malformed or out-of-scope cursors are rejected. `first` defaults to 20 when
omitted or null (50 for thread); values outside 1..100 (1..200 for thread) are
rejected by the runtime.

Non-null is used for values already persisted or derived once the parent row is
authorized. `Query.slip` is nullable so an unavailable object read is isolated.
A fault in a non-null connection bubbles to the root; the client shows a
page-level error and retries. No remotely resolved non-null relationship is
exposed. Connections contain only rows the caller may see.

`status` is DERIVED, never stored. The store derives it from the thread and the
queue, so this schema exposes it as a computed field rather than a column, and
the three age numbers (`workingForSec`, `quietForSec`, `waitingForSec`) are
derived alongside it. Nothing in this contract writes a status.

The stream is a subscription over changes, not over raw rows. Each event carries
a monotonic `sequence` so a client that reconnects can drop what it already
painted. The schema does not promise an event per queue mutation; the observed
service re-derives and broadcasts on change.

Idempotency: every command takes a caller retry key scoped to holder and desk,
recorded with the committed outcome atomically. An identical replay returns the
original result. Reusing a key with a different payload is a masked
BAD_USER_INPUT error. Retry lookup follows authorization and precedes state
checks.

Evolution should be additive. Clients must handle unknown enum and union members
conservatively, which is why every command result is a union with an explicit
`OperationRejected` member. The `revision` integer is `Int` with a documented
32-bit ceiling; a service approaching it must migrate before overflow. No
federation is needed. Planning dependencies are not modelled here: the existing
planning tool already owns them and duplicating them would create a second
authority for the same edge.

A model was considered for generating the operation documents and rejected for
the contract path. The gate is deterministic and the documents are small, so a
model would add variance and spend without adding coverage.