# Task board requirements

Illustrative application brief, not an existing service or deployed schema.

- R-browse: A workspace member pages through visible tasks without duplicates in stable ID order.
- R-read: A member reads a task; missing and inaccessible objects are indistinguishable.
- R-complete: An editor completes an open task using an observed revision and a retry key.

Membership is checked from the authenticated principal. Caller-supplied workspace IDs
never authorize access. Viewers may read; editors may complete. Other operations are
outside this example. Query.task returns null for unavailable tasks; completion uses
TaskUnavailable. No subscriptions, custom scalars or existing API are assumed.
