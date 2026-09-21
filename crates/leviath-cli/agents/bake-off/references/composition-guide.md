# aggregator: qwen/qwen3.8-max

# GraphQL Type Composition Guide

A concise, general, agent-internalizable rulebook for composing GraphQL types — derived from the anti-patterns, reversals, and architectural insights surfaced across three independent lenses reviewing a real 388-message schema-design trajectory. This guide is **generic**: reusable for any future GraphQL type group (Task, Memory, Node, Asset, Channel, or beyond), not a restatement of any specific schema.

---

## 1. The "One Concept, One Type" Rule

Every domain concept earns exactly one canonical GraphQL output type. Before creating a new type, ask: *Is this a distinct thing my users name, reason about, and query independently?*

- **Yes** → it gets a type.
- **"It's just a different view of the same thing"** → it gets a field (or a fragment) on the existing type.

**The mirroring smell:** If you count more than one type representing the same real-world entity (e.g., `Task` + `TaskDetail` + `TaskSummary`), you have a mirroring problem. Collapse into one type; use field selection and fragments for specialized views. Input shapes for mutations are a separate concern — never duplicate output types as inputs.

**Lens convergence:** All three lenses independently warn against type proliferation. The tencent lens provides concrete naming-anti-pattern examples (contextual redundancy like `StepInput` when already nested under `Step`); the deepseek lens gives the affirmative decision test; the qwen lens embeds this in the cohesion map (schema layer = "what is true").

---

## 2. Naming: App-Facing Semantics, Not Implementation Jargon

Names must answer *"what does the user call this?"* — never what the transport, DSL, database, or protocol calls it.

| Element | Convention | Example |
|---------|-----------|---------|
| Type names | Singular nouns | `Task`, `Memory`, `Channel` |
| Field names (data) | Noun phrases | `task.template`, `memory.context` |
| Field names (actions) | Verb phrases | `task.execute`, `channel.publish` |
| Enum values | `SCREAMING_SNAKE_CASE`, domain states | `TASK_READY`, `MEMORY_ARCHIVED` |
| Connection edges | Node-type name + `Edge` | `TaskEdge`, `MemoryEdge` |

**The sniff test:** Read the schema aloud to a domain expert who doesn't know GraphQL. If they follow without translation, the names are right. If they ask "what's a `MutationInputPayload`?", rename it.

**Anti-patterns the trajectory flagged repeatedly (from tencent lens):**

- **Contextual redundancy:** `StepInput` / `StepOutput` when these types only ever appear nested under `Step`. The parent type already establishes context — the prefix is noise. Prefer `Input` / `Output` when scoping is unambiguous; keep the prefix only if the type is referenced outside that shadow.
- **Metaphor mismatch:** `WorkflowDiscoveryResult` when the operation is really search/match. "Discovery" implies browsing/serendipity; "match" implies deterministic lookup. Name the *operation*, not a marketing verb.
- **Prefix redundancy:** `WorkflowDiscoveryResult` when returned by `discoverWorkflows` — the `Workflow` prefix adds no disambiguating power. Drop it: `DiscoveryResult` or `MatchResult`.
- **Conflating structural role with semantic role:** `StepInput` undersells the concept if the type includes `name`, `type`, `required`, `defaultValue`. A name like `StepParameter` or `StepSlot` captures that it's a *declaration*, not just a value.
- **Never leak storage/protocol names:** No `MongoDocument`, `KafkaMessage`, `ProtobufPayload`.

---

## 3. Interface vs Union vs Enum vs Scalar: The Decision Tree

Apply in order. This is the single most consequential type-choice decision.

| Step | Question | Answer → Choice | Example |
|------|----------|-----------------|---------|
| 1 | Closed, finite set of symbolic values with no associated data? | **Enum** | `TaskStatus`, `ChannelMode` |
| 2 | Multiple types share a structural contract (common fields meaningful across all implementors)? | **Interface** | `Node { id }`, `Temporal { createdAt, updatedAt }` |
| 3 | Types are semantically related but structurally divergent (no meaningful shared fields)? | **Union** | `SearchResult = Task \| Memory \| Asset` |
| 4 | Single value with domain-specific validation, serialization, or comparison semantics? | **Custom Scalar** | `DateTime`, `JSON`, `Markdown` |

**Key heuristics:**

- If you ever need to attach a description string or metadata to an enum value, it's not an enum — it's a type with an enum field.
- If you find yourself writing `... on Task { id } ... on Memory { id }` repeatedly, extract `id` into an interface.
- Don't create scalars for things that are just strings with different names. `Email` as a scalar is justified (validation); `Name` as a scalar is not (it's just a string).
- Prefer flat, composable interfaces over deep hierarchies: `type Task implements Node & Temporal` is good; a four-level `Identifiable → Versioned → Auditable → Task` chain is bad.

---

## 4. Avoiding ID-Soup, Mirror Types, and String Bags

**ID-soup:** A web of `taskId: ID!`, `memoryId: ID!`, `assetId: ID!` with no traversal structure.

**The fix:** Model relationships as fields returning the related type, not raw IDs:
```graphql
# BAD
type Task {
  parentTaskId: ID
  childTaskIds: [ID!]
}
# GOOD
type Task {
  parent: Task
  children: TaskConnection
}
```
**Rule:** If a relationship is traversable in the domain, it's a field returning the related type. Reserve bare ID fields only for intentionally opaque references (external system IDs).

**Parallel mirror input types:** `CreateTaskInput`, `UpdateTaskInput`, `PatchTaskInput` each duplicating 90% of `Task`'s fields.

**The fix:** Mutations take flat argument lists for small operations (≤3 fields). For larger operations, use a single `TaskInput` with optional fields; the mutation determines which are required. Inputs express *intent* (what the caller wants to change); outputs express *state* (what is true now). They should not mirror each other.

**String bags:** Types where every field is `String` or `String`-nullable, with no structure, enums, or gated wrappers. This is the "under-constrained schema that feels like a string bag" the human repeatedly rejected. Fix with enums, custom scalars, and the gated-value pattern (Section 5).

---

## 5. The Gated-Value Pattern (Monad/Sum-Type Pattern)

**The problem:** Nullable fields whose `null` carries implicit, undocumented meaning — conflating "not applicable," "not yet computed," "errored out," and "intentionally empty" into a single absent value. The trajectory contained multiple rounds where the human rejected exactly this.

**The pattern:** When a field's presence depends on a predicate (e.g., `result` exists only when `status = COMPLETED`), wrap the gated values in a structural type that makes the state machine explicit:

```graphql
# ANTI-PATTERN: implicit contract via nullability
type Task {
  status: TaskStatus!
  result: String          # null = not done? no output? error? who knows?
  errorMessage: String    # null = no error? not checked? suppressed?
}

# PATTERN: explicit state wrapper (sum types via union)
type Task {
  status: TaskStatus!
  outcome: TaskOutcome!
}

union TaskOutcome = TaskCompleted | TaskFailed | TaskPending

type TaskCompleted  { completedAt: DateTime!; result: String! }
type TaskFailed     { failedAt: DateTime!;   error: TaskError! }
type TaskPending    { estimatedCompletion: DateTime }
```

**Principle:** Make illegal states unrepresentable. A nullable field with an implicit "you can only read this when X is true" contract is a bug waiting to happen.

**Composition:**
- **With edge-first (Section 6):** Edge properties are often gated (a dependency edge's `blockedReason` exists only when blocking). Apply the same union-wrapper pattern to edge types.
- **With soft/hard invariants (Section 7):** The wrapper is a *hard invariant* — the schema structurally guarantees you can't access `result` without matching the variant. The *predicate* that determines which variant you get is a *soft invariant* enforced by the resolver. Schema says "if completed, here's the shape"; code decides "is it completed?"
- **With directives:** When a value's *availability* is gated on state, that's a field concern (queryable). When a value's *resolution behavior* is gated (e.g., "resolve only if caller has role X"), that's a directive concern (invisible to query author).

---

## 6. Edge-First Graph Modeling

**Principle:** In a graph domain, edges are first-class entities carrying their own identity, metadata, and lifecycle — not second-class annotations on nodes.

```graphql
type DependencyEdge {
  id: ID!
  source: Task!
  target: Task!
  dependencyType: DependencyType!
  state: DependencyState!   # ← gated-value pattern applies here too
  metadata: EdgeMetadata    # created_at, created_by, weight, etc.
}
```

**Why edge-first:**
- Relationships carry their own data (when created? by whom? soft or hard?).
- Traversal becomes explicit and queryable.
- Graph structure survives node-type refactoring.
- Invariants enforced at edge level without polluting node types.

**The "edge-first, not edge-only" rule:** Simple, metadata-free relationships (`Task.author`, `Memory.sourceChannel`) remain as direct fields. The promotion test: *does this relationship carry its own state, lifecycle, or metadata that clients query independently?* Yes → edge type. No → field.

**Composition with gated-value:** Edge state is often gated. A dependency edge might transition `PENDING → SATISFIED → BROKEN`. Model edge state with the same union-wrapper pattern used for nodes.

**Composition with directives:** Edge traversal authorization (`@traverse(requires: ADMIN)`) is a directive concern — it modulates execution without polluting the edge's data fields.

---

## 7. Soft vs Hard Invariants

| | Hard Invariant | Soft Invariant |
|---|---|---|
| **Enforced by** | Schema structure (`!`, enum, type constraint) | Resolver logic + descriptions |
| **Rejected at** | GraphQL validation time (before resolvers run) | Application layer (domain-specific errors) |
| **Protects** | Structural integrity of the schema | Business rules of the domain |
| **When to use** | Field absence makes the type semantically incoherent | Rule is expressible as "X must be Y when Z" or "A cannot B unless C" |

**Hard invariant examples:** `Task.id: ID!` (a task without identity is incoherent), `Edge.target: Task!` (an edge without a target is incoherent).

**Soft invariant examples:** "A task cannot depend on itself" (schema allows the edge; resolver rejects cycles), "Memory TTL must be positive" (schema accepts any Int; resolver validates), "Task name ≤ 256 chars."

**The heuristic:** *Hard invariants protect structural integrity. Soft invariants protect business rules.* Confuse them and you get schemas that can't evolve (too many hard) or can't be trusted (too few). The gated-value pattern converts what would otherwise be soft invariants ("you can only read `result` when `status = COMPLETED`") into hard structural guarantees — moving safety from documentation into the type system without over-constraining flexibility.

---

## 8. Directives vs Fields: The Execution-Model Boundary

| Concern belongs in… | When… | Examples |
|---|---|---|
| **Field** (data plane) | The client queries, filters on, or mutates it | `task.priority`, `memory.ttl`, `channel.retentionPolicy` |
| **Directive** (execution plane) | It modifies execution behavior without appearing in the data model | `@deprecated`, `@skip`, `@audited`, `@idempotent`, `@rateLimit` |

**The cross-cutting test:** When a concern touches many types uniformly (auditing, authz, idempotency, rate limiting), ask: *does the client need to SELECT this in a query?* No → directive. Yes → field.

**Anti-pattern — "directive creep":** Schemas littered with `@authenticated`, `@validated`, `@logged`, `@cached`. Fix: group related execution concerns into a single directive with arguments (`@policy(auth: REQUIRED, cache: PRIVATE, rateLimit: "10/m")`) or move non-schema concerns entirely into resolver middleware.

**Redundancy test:** If you have both `@status(ACTIVE)` on a field AND `status: TaskStatus!` as a field, the directive is redundant — collapse into the field. Conversely, a field called `cacheControl` that only informs infrastructure should be promoted to a directive.

---

## 9. "Do Guided by Prompt, Do-Not Enforced by Invariant"

**The principle:** The schema is a *communication medium* first and a *validation engine* second. It should *prompt* correct usage through shape, names, and descriptions. It should *enforce* only what would cause data corruption or semantic incoherence if violated. Everything else is guidance.

**Guidance mechanisms (schema as teacher):**
- Descriptive type/field names that make relationships self-documenting (`parent: Task`, not `parentTaskId: ID`)
- `@deprecated(reason: "Use 'outcome' instead")` — guides evolution without breaking queries
- Enum values that read as domain states (`TASK_READY`, not `STATUS_1`)
- Field descriptions that explain gated-value contracts in human terms

**Enforcement mechanisms (schema as gatekeeper):**
- Non-null (`!`) only for structural coherence
- Enum value sets
- Custom scalar validation
- Required mutation arguments

**The balance:** If you're adding `!` "just to be safe," make it nullable with a description instead. If you're writing "the client must ensure X before calling Y" in docs, consider whether the schema can enforce X directly — but don't over-constrain. Strong typing serves communication by making the domain model legible; it's not a substitute for documentation or proper validation logic.

---

## 10. Two-Way Schema ↔ Code Authoring

**Principle:** Schema and code are two projections of a single domain model. Neither is the "master." Changes propagate in both directions:

- **Schema → Code:** Adding a type/field prompts the code to grow a resolver. The schema *prompts* implementation.
- **Code → Schema:** When implementation reveals a field is expensive or a relationship is actually 1:N not N:M, the schema adjusts. The code *informs* the schema.

**The drift-prevention rule:** After any significant schema change, ask: "Can I implement this?" After any significant code change, ask: "Should this be reflected in the schema?" Both must be "yes" before the change is complete.

**The drift test:** Can you delete the schema and regenerate it from the code? Can you delete the code and re-implement from the schema? If both answers are no, you have hidden coupling that will break.

**Composition with other patterns:**
- **Gated-value:** Adding a new union variant (`TaskCancelled`) requires both a schema change (new type in the union) and a code change (new resolver branch).
- **Edge-first:** Edge types prompt resolvers to treat relationships as queryable entities. If implementation reveals computed (not stored) edges, the schema may need `isImplied: Boolean!` on the edge type.
- **Soft invariants:** Live in code and evolve faster than the schema. The schema provides the stable structural contract; the code provides evolving business rules. This separation makes "do guided by prompt" practical — tighten validation in code without a schema migration.

---

## 11. The "Would I Query This?" Test (Ultimate Filter)

Before adding any field, type, or relationship, ask: *Would a real client write a query that selects this field?*

- "Only in a contrived example" → omit.
- "The backend needs it but clients don't" → omit.
- Internal concerns (database IDs, shard keys, queue names, internal timestamps) have no place unless clients query them.

The schema is a query language for clients, not a complete data model of your system.

---

## 12. Cohesion Map: How the Patterns Compose

```
                    ┌─────────────────────────────────┐
                    │     DOMAIN MODEL (the truth)     │
                    └───────────────┬─────────────────┘
                                    │
            ┌───────────────────────┼───────────────────────┐
            │                       │                       │
            ▼                       ▼                       ▼
   ┌────────────────┐    ┌──────────────────┐    ┌──────────────────┐
   │  SCHEMA LAYER  │    │  EXECUTION LAYER │    │   CODE LAYER     │
   │                │    │                  │    │                  │
   │ • Edge types   │    │ • Directives     │    │ • Soft invariants│
   │ • Gated-value  │    │ • Authz policies │    │ • Business rules │
   │   wrappers     │    │ • Rate limits    │    │ • Validation     │
   │ • Hard invars  │    │ • Deprecation    │    │ • Computation    │
   │ • Enums        │    │ • Tracing        │    │ • State machines │
   │ • Interfaces   │    │                  │    │                  │
   │                │    │                  │    │                  │
   │ "What is true" │    │ "How to execute" │    │ "What to enforce"│
   └────────────────┘    └──────────────────┘    └──────────────────┘
```

**The six cohesion rules:**

1. **What the client queries** → Schema layer (fields, types, enums).
2. **How the engine behaves** → Execution layer (directives).
3. **What the domain requires** → Code layer (soft invariants, business logic).
4. **Gated-value wrappers** bridge schema and code: schema declares the shape of each state; code determines which state applies.
5. **Edge types** bridge schema and graph: schema declares relationships as queryable entities; code resolves them from the underlying store.
6. **"Do guided by prompt"** governs the boundary: schema guides, code enforces, directives modulate.

When a new cross-cutting concern arises (e.g., "track who last modified each entity"):
- Queryable by clients? → Field (or `Auditable` interface).
- Execution metadata? → Directive or resolver middleware.
- Business rule about who *can* modify? → Soft invariant in code.

---

## Quick-Reference Checklist

Before finalizing any GraphQL type group, run through these:

- [ ] **One Concept, One Type** — no mirror/output-duplicate types for the same entity.
- [ ] **Naming sniff test** — domain expert understands without translation; no storage/protocol names leaked.
- [ ] **No contextual redundancy** — type names don't repeat context already established by the parent.
- [ ] **No metaphor mismatch** — names reflect the actual operation, not a marketing verb.
- [ ] **Decision tree applied** — enum/interface/union/scalar chosen via the 4-step test, not by habit.
- [ ] **No ID-soup** — relationships are traversable fields, not raw ID arrays.
- [ ] **No mirror input types** — inputs express intent; they don't duplicate output type fields.
- [ ] **No string bags** — enums, scalars, and gated wrappers used where values have structure.
- [ ] **Gated-value pattern applied** — no nullable fields with implicit "only when X" contracts.
- [ ] **Edge-first where warranted** — relationships with their own state/lifecycle/metadata are edge types.
- [ ] **Hard invariants minimal** — `!` only for structural incoherence, not "just to be safe."
- [ ] **Directive creep avoided** — execution concerns grouped or moved to resolver middleware.
- [ ] **Would I query this?** — every field passes the real-client test.
- [ ] **Drift test** — schema ↔ code co-evolution is symmetric; neither is the isolated "master."