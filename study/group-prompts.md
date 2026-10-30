GROUP PROMPTS — GraphQL Blind Study

Each draft model receives: [the SAME Composition Guide] + [its assigned GROUP PROMPT below].
It writes a FULL schema + rationale (Full Monty, no caps) to a file, tagged with its blind label.

============================================================
GROUP 1: TASK
============================================================
Compose a GraphQL type group for a durable WORK UNIT (a "task"/"bead") in an agentic execution
system. This is the unit agents claim, run, and close.

Domain concepts to cover:
- The work unit itself (identity, title, description, lifecycle/status).
- Claiming / assignment (who or what agent may claim/run it; blocking vs ready).
- Execution trajectory (the run: iterations, tool calls, progress, output artifact refs).
- Dependencies and blocking (a task blocked on others).

Apply the Composition Guide. Produce a FULL schema + rationale.
Do NOT copy the workflow schema — this is the TASK abstraction (execution trajectory + beads),
distinct from the workflow (composition-of-steps) group.

============================================================
GROUP 2: MEMORY
============================================================
Compose a GraphQL type group for DURABLE KNOWLEDGE / MEMORY in an agentic system. This is the
compounding "what the system knows" store (entities + relationships + scoped facts).

Domain concepts to cover:
- Memory entities (canonical things the system knows: people, projects, decisions, findings).
- Relationships between entities (graph edges with type + optional metadata).
- Scoped facts / observations (a fact about an entity, with source, confidence, scope:
  user / project / system, and temporality).
- The graph itself is first-class (an entity's connections are traversable).

Apply the Composition Guide. Produce a FULL schema + rationale.
Distinct from workflow and task — this is the KNOWLEDGE GRAPH abstraction.

============================================================
GROUP 3: PROPOSAL
============================================================
Compose a GraphQL type group for HUMAN-IN-THE-LOOP PROPOSALS / DECISION GATES in an agentic
system. Agents propose; a human reviews and decides.

Domain concepts to cover:
- The proposal (title, description, kind, current status: draft / awaiting_review / approved /
  rejected / changes_requested).
- The decision gate (the specific thing being asked of the human, with a clear choice).
- Review verdicts + revision history (who decided what, when; revisions back to the agent).
- Bind to reviewers (who may approve).

Apply the Composition Guide. Produce a FULL schema + rationale.
Distinct from task/memory — this is the GATED-DECISION abstraction.