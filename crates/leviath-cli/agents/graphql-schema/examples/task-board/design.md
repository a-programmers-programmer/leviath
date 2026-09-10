# Task contract decisions

Task is the aggregate. Stable opaque IDs identify tasks; Workspace is an authorization
scope with membership owned by the existing identity service. We do not expose membership
or user CRUD because no client journey needs it. OPEN to COMPLETED is the only transition.
A generic updateTask patch was considered and rejected: it hides this invariant and allows
clients to propose invalid states. Results are typed because conflicts are normal UI cases.

All task properties are locally persisted and guaranteed after authorization, hence non-null.
The nullable Query.task isolates unavailable object reads. A storage outage in a non-null
connection can bubble to the root; clients show a page-level error and retry. No remotely
resolved non-null relationships are exposed. Connection edges contain only authorized rows.

Cursor order is ascending immutable task ID using a binary comparison; the ID itself is the
tie-breaker. A signed, opaque cursor includes workspace and order version. Reads are live,
not snapshot-consistent: newly inserted IDs before the current cursor will require refresh.
Deleted cursor rows do not break continuation because paging compares IDs, not row offsets.
Malformed or out-of-scope cursors are rejected without disclosing data. Page size defaults
to 20 when omitted or null; values outside 1..100 are rejected by the runtime.

Completion authenticates and authorizes before fetching conflict details. It atomically
checks OPEN and expectedRevision, changes the state, increments revision, and records the
outcome under (principal, workspace, idempotencyKey). An identical retry within 24 hours
returns the original committed result; reusing a key with a different payload is a masked
BAD_USER_INPUT error. Retry lookup precedes the revision check but follows authorization.
Revision or state conflicts return TaskConflict only to an authorized editor. No hidden
object's existence is disclosed. Unexpected faults remain masked top-level errors.

This is a new API. Changes should be additive, and clients handle unknown enum/union
members conservatively. The integer revision has a documented 32-bit storage ceiling;
a service approaching that ceiling must migrate before overflow. No federation is needed.
