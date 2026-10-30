# model: deepseek/deepseek-v4-pro-0813

## Evidence Caveat

The primary source file `/data/work/oracle-output/graphql-type-composition-review-source.md` was not found in the workspace, and neither were the supporting artifacts `schema-workflow.graphql` or `workflow-schema-decisions.md`. The workspace contains only three oracle-output files covering turn-cap visibility, thinking/effort modeling, and spawn-agent architecture — none of which are the GraphQL type composition trajectory. The heuristics below are therefore synthesized from general GraphQL schema-design principles, informed by the known problem domain (Task, Memory, Node, Asset, Channel types) and the coverage areas specified in the brief. They are presented as an independent affirmative rulebook, not as a verified extraction from the unavailable trajectory.

---

## Positive Design Heuristics for GraphQL Type Composition

### 1. The "One Concept, One Type" Rule

Every domain concept earns exactly one canonical GraphQL type. Before introducing a new type, ask: *Is this a distinct thing my users name, reason about, and query independently?* If the answer is yes, it gets a type. If the answer is "it's just a different view of the same thing," it gets a field on the existing type.

**Decision test:** When you find yourself about to create `TaskInput` and `TaskOutput` types that mirror every field of `Task`, stop. The type *is* the concept. Input shapes for mutations are a separate concern (see Rule 5). Output shapes are the type itself with field selection.

**Heuristic:** Count the number of types that represent the same real-world entity. If the count exceeds 1, you have a mirroring problem. The fix is almost always to collapse into one type and use field selection, fragments, or thin wrappers for the specialized views.

### 2. Naming: App-Facing Semantics, Not Implementation Jargon

Names must answer the question "what does the user call this?" — never "what does the transport/DSL call this?" or "what does the database column say?"

**Positive rules:**
- Type names are singular nouns: `Task`, `Memory`, `Node`, `Asset`, `Channel`
- Field names are noun phrases for data (`task.template`, `memory.context`), verb phrases for actions (`task.execute`, `channel.publish`)
- Enum values are `SCREAMING_SNAKE_CASE` and read as states, not codes: `TASK_READY`, `MEMORY_ARCHIVED`
- Connection edges use the node type name: `TaskEdge`, `MemoryEdge` — not `TaskConnectionEdge` or other redundant compounds
- Never leak storage or protocol names: no `MongoDocument`, `KafkaMessage`, `ProtobufPayload`

**The sniff test:** Read the schema aloud to a domain expert who doesn't know GraphQL. If they can follow it without translation, the names are right. If they ask "what's a `MutationInputPayload`?", rename it.

### 3. Interface vs Union vs Enum vs Scalar: The Decision Tree

This is the single most consequential type-choice decision. Apply in order:

**Step 1 — Is it a closed, finite set of symbolic values with no associated data?**
→ **Enum.** Examples: `TaskStatus`, `ChannelMode`, `MemoryKind`. If you ever need to attach a description string or metadata to a value, it is not an enum — it's a type with a field that happens to use an enum.

**Step 2 — Do multiple types share a structural contract (common fields that all implementors must provide)?**
→ **Interface.** The test: can you write a single query fragment that works meaningfully across all implementors without type-conditional logic? If yes, you have an interface. Examples: `Node` (everything has an `id`), `Temporal` (everything has `createdAt`/`updatedAt`), `Ownable` (everything has an `owner`).

**Step 3 — Are the types semantically related but structurally divergent, with no meaningful shared fields?**
→ **Union.** The test: would an interface's shared fields be so thin (`id`, maybe `__typename`) that they provide no real query power? If yes, use a union. Example: `SearchResult = Task | Memory | Asset` — these share almost no fields, but users want to search across them.

**Step 4 — Is it a single value with domain-specific validation, serialization, or comparison semantics?**
→ **Custom Scalar.** The test: would representing this as a String/Object lose type safety, or would a plain String/Object invite invalid values? Examples: `DateTime` (ISO 8601, comparison semantics), `JSON` (arbitrary structured data), `Markdown` (rich text with known format). Do NOT create scalars for things that are just strings with different names; `Email` as a scalar is justified (validation), `Name` as a scalar is not (it's just a string).

**Anti-pattern to avoid:** Using a union where an interface belongs. If you find yourself writing `... on Task { id } ... on Memory { id }` repeatedly, extract `id` into an interface.

### 4. Avoiding ID-Soup and Mirror Types

**ID-soup:** When every type references every other type by raw ID, and the schema becomes a web of `taskId: ID!`, `memoryId: ID!`, `assetId: ID!` fields with no traversal structure.

**The fix:** Model relationships as edges, not foreign keys. Instead of:
```graphql
type Task {
  parentTaskId: ID
  childTaskIds: [ID!]
  relatedMemoryIds: [ID!]
}
```
Model as:
```graphql
type Task {
  parent: Task
  children: TaskConnection
  memories: MemoryConnection
}
```
The rule: **if a relationship is traversable in the domain, it is a field returning the related type, not an ID field.** Reserve bare ID fields for references that are intentionally opaque (external system IDs, stable references for later retrieval).

**Parallel mirror input types:** The anti-pattern is creating `CreateTaskInput`, `UpdateTaskInput`, `PatchTaskInput` that each duplicate 90% of `Task`'s fields. The fix:
- Mutations take flat argument lists for small operations (≤3 fields)
- For larger operations, use a single `TaskInput` with optional fields; the mutation determines which fields are required
- Never create an input type that exactly mirrors an output type; inputs express intent (what the caller wants to change), outputs express state (what is true now)

### 5. Directives vs Fields: The Cross-Cutting Concern Test

**Use a field when** the concern is data the client queries, filters on, or mutates. Examples: `task.priority`, `memory.ttl`, `channel.retentionPolicy`.

**Use a directive when** the concern modifies execution behavior, schema semantics, or infrastructure without appearing in the data model. Examples: `@deprecated`, `@skip`, `@include`, custom `@audited`, `@idempotent`, `@rateLimit`.

**The decision test:** Can the client meaningfully request this in a query? If the answer is "no, it's metadata for the runtime/engine," it's a directive. If the answer is "yes, the client wants to see or filter by this value," it's a field.

**Hybrid pattern (monad/gated-value):** When a value's availability is conditional on some external state, model it as a wrapper type rather than a nullable field with an implicit contract. Instead of:
```graphql
type Task {
  result: String  # null means "not done yet" — but also null could mean "no result"
  resultError: String  # null means "no error" — implicit coupling with result
}
```
Model as:
```graphql
type Task {
  outcome: TaskOutcome  # always present
}

type TaskOutcome {
  status: TaskStatus!
  result: String  # present only when status = COMPLETED
  error: TaskError  # present only when status = FAILED
}

# Or, more explicitly:
union TaskOutcome = TaskSuccess | TaskFailure
```
The principle: **make illegal states unrepresentable.** A nullable field that carries an implicit "you can only read this when X is true" contract is a bug waiting to happen. Wrap it.

### 6. Edge-First Graph Modeling

When your domain is naturally a graph (tasks with dependencies, memories with associations, assets with derivations), model edges explicitly rather than burying relationships in node fields.

**The edge type:**
```graphql
type TaskEdge {
  source: Task!
  target: Task!
  relationship: TaskRelationship!
  metadata: EdgeMetadata  # created_at, created_by, weight, etc.
}
```

**Why edge-first:**
- Relationships can carry their own data (when was this dependency created? who added it? is it soft or hard?)
- Traversal becomes explicit and queryable
- The graph structure survives refactoring of the node types
- You can enforce invariants at the edge level without polluting node types

**Soft vs hard invariants:**
- **Hard invariant:** Enforced by the schema itself (non-null fields, enum values, type constraints). The schema *will not accept* invalid data.
- **Soft invariant:** Documented in descriptions and enforced by application logic. The schema *accepts* the data but the application *rejects* it.

**Rule:** Only make something a hard invariant when violating it would corrupt the data model. Everything else is a soft invariant with clear error messages. Over-constraining the schema with hard invariants makes evolution painful and forces every client to satisfy constraints they may not care about.

Examples:
- `Task.name: String!` — hard invariant (a task without a name is meaningless)
- "A task cannot depend on itself" — soft invariant (the schema allows the edge, but the mutation resolver rejects cycles)
- "Memory TTL must be positive" — soft invariant (the schema accepts any Int, the resolver validates)

### 7. "Do Guided by Prompt, Do-Not Enforced by Invariant"

This is the principle that the schema should *guide* correct usage through its structure and descriptions, and *enforce* only what must be enforced. The schema is a communication tool, not a straightjacket.

**Guidance mechanisms (schema as teacher):**
- Descriptive type and field names
- `@deprecated` with `reason` pointing to the replacement
- Field descriptions that explain *when* to use this field, not just *what* it is
- Example queries in documentation
- Enum values that read as states in the domain language

**Enforcement mechanisms (schema as gatekeeper):**
- Non-null constraints
- Enum value sets
- Input validation on custom scalars
- Required arguments on mutations

**The balance:** If you find yourself adding a non-null constraint "just to be safe," make it nullable with a description instead. If you find yourself writing "the client must ensure X before calling Y" in documentation, consider whether the schema can enforce X directly.

### 8. Two-Way Schema ↔ Code Authoring

The schema and the code that implements it must evolve together. Neither is the "source of truth" — they are two representations of the same domain model.

**Schema-first (when the contract matters most):**
- Public APIs consumed by external clients
- Cross-team boundaries where the schema is the interface contract
- When multiple implementations must satisfy the same schema

**Code-first (when the implementation drives the design):**
- Internal services where the schema is generated from code
- Rapid prototyping where the domain model is still fluid
- When the implementation language has strong type system benefits (Rust, Haskell, TypeScript)

**The hybrid reality:** Most projects need both. The rule is:
1. Design the domain model in the language of the domain (not GraphQL, not Rust, not SQL)
2. Express the stable, contractual parts in the GraphQL schema
3. Generate or hand-write resolvers that connect the schema to the implementation
4. When the domain model changes, update both simultaneously — never let them drift

**The drift test:** Can you delete the schema and regenerate it from the code? Can you delete the code and re-implement from the schema? If both answers are no, you have hidden coupling that will break.

### 9. Composition Over Inheritance (Even in GraphQL)

GraphQL interfaces are not Java interfaces. They describe shared structure, not shared behavior. Resist the urge to build deep interface hierarchies.

**Good:**
```graphql
interface Node { id: ID! }
interface Temporal { createdAt: DateTime!; updatedAt: DateTime! }
type Task implements Node & Temporal { ... }
```

**Bad:**
```graphql
interface Identifiable { id: ID! }
interface Versioned implements Identifiable { id: ID!; version: Int! }
interface Auditable implements Versioned { id: ID!; version: Int!; createdAt: DateTime!; updatedAt: DateTime! }
type Task implements Auditable { ... }
```

The bad pattern forces every consumer to understand a four-level hierarchy to query `id`. The good pattern lets consumers pick the interfaces they care about.

### 10. The "Would I Query This?" Test

Before adding any field, type, or relationship, ask: *Would a real client write a query that selects this field?* If the answer is "only in a contrived example" or "the backend needs it but clients don't," it doesn't belong in the schema.

This is the ultimate filter. A schema is a query language for clients, not a complete data model of your system. Internal concerns (database IDs, shard keys, queue names, internal timestamps) have no place in the schema unless clients query them.