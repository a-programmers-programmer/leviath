# GQL-11 fixture: inputs the runbook consumes

| File | Purpose |
| --- | --- |
| `BRIEF.md` | The application brief. The runbook passes it to the workflow as the task. |
| `review.template.json` | The review record shape. The runbook fills the fingerprint and the evidence. |

The seed contract is NOT copied here. The runbook seeds from the tracked
example at `crates/leviath-cli/agents/graphql-schema/examples/task-board`.
That example is tracked at the base commit, so a clean checkout has it.

`review.template.json` is a template. A filled template is a mechanics fixture,
not evidence of a real model review. Say so in any report that cites it.
