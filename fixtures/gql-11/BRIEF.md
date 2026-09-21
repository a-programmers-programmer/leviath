# Task board brief (GQL-11 fixture)

This is the brief the runbook feeds to the GraphQL authoring workflow. It is a
fixture. It is not a deployed service. It matches the shipped example at
`crates/leviath-cli/agents/graphql-schema/examples/task-board`.

## Actors and boundaries

- An authenticated member belongs to a workspace. Membership is owned by an
  existing identity service. This API never lists or edits membership.
- A caller-supplied workspace id is never authority. Membership is checked from
  the authenticated principal.
- Viewers may read tasks. Editors may complete tasks.

## Client journeys

- J-browse: A member pages through the tasks they may see, in stable id order,
  with no duplicates and no gaps.
- J-read: A member reads one task. A task that is absent and a task the member
  may not see are indistinguishable to the caller.
- J-complete: An editor completes an open task using a revision they observed
  and a retry key. A retry of the same command must not complete twice.

## Lifecycle

- A task is OPEN or COMPLETED. OPEN -> COMPLETED is the only transition.

## Acceptance scenarios

- Reading another workspace's task returns the same result as reading a missing task.
- Completing with a stale revision returns a conflict carrying the current
  revision, and only to a member who may see the task.
- Replaying a complete command with the same retry key returns the original
  result and changes nothing new.
- A page of tasks never exceeds the requested page size and never exceeds 100 items.

## Out of scope

- Subscriptions, custom scalars, federation, membership CRUD, and task deletion.
