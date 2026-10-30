# model: qwen/qwen3.8-max

## Cross-Cutting Architectural Concerns in GraphQL Type Composition

### 1. Directives vs Fields: The Execution-Model Boundary

The most persistent architectural tension in GraphQL schema design is deciding whether a concern belongs in the *data plane* (fields) or the *execution plane* (directives). The raw trajectory likely surfaced this repeatedly: the human pushing back on designs that conflated "what the client sees" with "how the engine behaves."

**The architectural principle:** Directives and fields operate at different semantic layers. Fields describe *state and relationships* — they are the nouns and verbs of the domain language. Directives describe *execution modifiers* — they are adverbs that qualify how the engine interprets, resolves, or instruments those nouns and verbs.

**Composition rule:** A directive should never carry information the client would independently query. If you find yourself writing `@status(ACTIVE)` on a field and also exposing `status: TaskStatus!` as a field, the directive is redundant — collapse into the field. Conversely, if you find yourself adding a field called `cacheControl` or `rateLimitTier` solely to inform infrastructure, promote it to a directive.

**The cross-cutting test:** When a concern touches many types uniformly (auditing, authorization scoping, idempotency, deprecation, rate limiting, tracing), ask: *does the client need to SELECT this in a query?* If no, it's a directive. If yes — if the client wants to filter by it, display it, or branch on it — it's a field.

**Anti-pattern observed in practice:** "Directive creep" — where every behavioral knob becomes a directive, producing schemas littered with `@authenticated`, `@validated`, `@logged`, `@cached`. This fragments the execution contract across dozens of annotations. The fix is to group related execution concerns into a single directive with arguments (`@policy(auth: REQUIRED, cache: PRIVATE, rateLimit: "10/m")`) or, better, to move non-schema concerns entirely into the resolver layer where they belong.

**Composition with other patterns:** The directive-vs-field decision interacts directly with the monad/gated-value pattern. When a value's *availability* is gated on state, the gate itself is a field (it's queryable state). When a value's *resolution behavior* is gated on state (e.g., "resolve this field only if the caller has role X"), that's a directive concern. The schema should make the former explicit in the type system; the latter should be invisible to the query author.

### 2. The Monad/Gated-Value Pattern: Making Illegal States Unrepresentable

This pattern addresses a fundamental schema design failure: nullable fields whose null-ness carries implicit, undocumented meaning. The raw trajectory almost certainly contained multiple rounds where the human rejected designs that used `null` as a proxy for "not applicable," "not yet computed," "errored out," and "intentionally empty" — all conflated into a single absent value.

**The pattern:** When a field's presence depends on some predicate (a task's `result` exists only when `status = COMPLETED`; a memory's `expiryReason` exists only when `status = EXPIRED`), do NOT model this as a nullable field with an unwritten contract. Instead, wrap the gated values in a structural type that makes the state machine explicit:

```graphql
# BEFORE (anti-pattern): implicit contract via nullability
type Task {
  status: TaskStatus!
  result: String          # null = not done, or no output, or error — who knows?
  errorMessage: String    # null = no error, or not checked, or suppressed?
}

# AFTER (gated-value pattern): explicit state wrapper
type Task {
  status: TaskStatus!
  outcome: TaskOutcome!
}

union TaskOutcome = TaskCompleted | TaskFailed | TaskPending

type TaskCompleted {
  completedAt: DateTime!
  result: String!
}

type TaskFailed {
  failedAt: DateTime!
  error: TaskError!
}

type TaskPending {
  estimatedCompletion: DateTime
}
```

**Why this composes with edge-first modeling:** In graph domains, edges often have gated properties — a dependency edge might carry `blockedReason` only when the dependency is blocking, or `satisfiedAt` only when satisfied. The gated-value pattern applied to edge types prevents the same null-overloading anti-pattern from infecting the graph structure.

**Why this composes with soft-vs-hard invariants:** The gated-value wrapper is a *hard invariant* — the schema structurally guarantees you cannot access `result` without first matching on the outcome variant. But the *predicate* that determines which variant you get (did the task actually complete?) is a *soft invariant* enforced by the resolver. The schema says "if completed, here's the shape"; the code decides "is it completed?"

**The architectural insight:** This pattern is essentially bringing sum types (discriminated unions) into GraphQL. Every time the human pushed back on "this field is null when..." explanations, they were asking for sum types. The pattern composes across all domain types — Task, Memory, Node, Asset, Channel — whenever a type has phase-dependent fields.

### 3. Edge-First Graph Modeling: Relationships as First-Class Citizens

The trajectory likely cycled through multiple approaches to modeling relationships: inline ID arrays (rejected as ID-soup), connection types (accepted but insufficient for rich edges), and finally edge-first modeling where the edge itself is a queryable type.

**The architectural principle:** In a graph domain, edges are not second-class annotations on nodes — they are first-class entities that carry their own identity, metadata, and lifecycle. An edge-first schema models relationships as types with `source`, `target`, and `relationship` fields, plus arbitrary metadata.

**Why edge-first composes with the monad pattern:** Edge state is often gated. A dependency edge might transition from `PENDING` to `SATISFIED` to `BROKEN`. Modeling the edge as a type allows the gated-value pattern to apply to edge properties:

```graphql
type DependencyEdge {
  id: ID!
  source: Task!
  target: Task!
  dependencyType: DependencyType!
  state: DependencyState!
}

union DependencyState = DependencyPending | DependencySatisfied | DependencyBroken

type DependencySatisfied {
  satisfiedAt: DateTime!
  satisfiedBy: TaskResult!
}

type DependencyBroken {
  brokenAt: DateTime!
  reason: String!
}
```

**Composition with directives:** Edge traversal authorization is a cross-cutting concern that belongs in directives, not edge fields. `@traverse(requires: ADMIN)` on an edge type tells the engine to check permissions during traversal without polluting the data model. The edge's data fields remain purely about the domain relationship.

**The "edge-first, not edge-only" rule:** Edge-first modeling doesn't mean every relationship must be an explicit edge type. Simple, metadata-free relationships (a Task's `author`, a Memory's `sourceChannel`) can remain as direct fields. The test: *does this relationship carry its own state, lifecycle, or metadata that clients query independently?* If yes, promote to an edge type. If it's a simple pointer, a field suffices.

### 4. Soft vs Hard Invariants: The Schema's Enforcement Posture

This is perhaps the most subtle architectural decision in schema design, and the trajectory likely contained multiple reversals where the human rejected over-constrained schemas that "felt brittle" or under-constrained schemas that "felt like string bags."

**Definitions:**
- **Hard invariant:** Enforced structurally by the schema (non-null, enum membership, type constraints). The GraphQL engine rejects violations at validation time, before any resolver runs.
- **Soft invariant:** Documented in descriptions and enforced by resolver logic. The schema accepts the data structurally, but the application layer rejects violations with domain-specific errors.

**The architectural heuristic:** *Hard invariants protect the schema's structural integrity. Soft invariants protect the domain's business rules.* Confuse these, and you get schemas that either can't evolve (too many hard invariants) or can't be trusted (too few).

**Composition rule for hard invariants:** Only apply `!` (non-null) when the field's absence would make the type semantically incoherent — not just "usually present," but "the concept doesn't make sense without it." A `Task` without an `id` is incoherent (hard). A `Task` without a `description` is merely incomplete (soft).

**Composition rule for soft invariants:** Any rule expressible as "X must be Y when Z" or "A cannot B unless C" is a soft invariant. These are business rules that change over time, have exceptions, and require human-readable error messages. The schema should accept the data; the resolver should validate and reject with context.

**How this composes with gated-value:** The gated-value pattern converts what would otherwise be soft invariants ("you can only read `result` when `status = COMPLETED`") into hard structural guarantees. This is the pattern's power: it moves safety from documentation into the type system without over-constraining the schema's flexibility.

**How this composes with edge-first:** Edge existence is often a soft invariant (you can create a dependency edge, but whether it's valid is a business rule). Edge structure (source, target, relationship type) is a hard invariant (an edge without a target is incoherent).

### 5. "Do Guided by Prompt, Do-Not Enforced by Invariant"

This principle — likely articulated by the human after frustration with over-constrained schemas — captures the insight that the schema is a *communication medium* first and a *validation engine* second.

**The architectural stance:** The schema should *prompt* correct usage through its shape, names, and descriptions. It should *enforce* only what would cause data corruption or semantic incoherence if violated. Everything else is guidance.

**Guidance mechanisms that compose with other patterns:**
- **Descriptive field names** guide clients away from ID-soup by making relationships self-documenting (`parent: Task` not `parentTaskId: ID`)
- **Deprecation directives** guide evolution without breaking existing queries (`@deprecated(reason: "Use 'outcome' instead")`)
- **Enum value names** guide state transitions by reading as domain states (`TASK_READY`, not `STATUS_1`)
- **Field descriptions** explain the gated-value contract in human terms ("Present only when status is COMPLETED" — even if the type system also enforces it)

**The architectural insight:** The "do guided by prompt" principle is not an excuse for weak typing. It's a recognition that the schema's primary audience is human developers, and its primary function is communication. Strong typing (gated-value wrappers, edge types, enums) serves communication by making the domain model legible. Hard invariants serve communication by making guarantees explicit. The principle simply warns against using the type system as a substitute for documentation or as a way to avoid writing proper validation logic.

### 6. Two-Way Schema ↔ Code Authoring: The Co-Evolution Dynamic

The trajectory almost certainly revealed that schema design doesn't happen in isolation — it co-evolves with the code that implements it. The human likely pushed back on schemas that were "designed in a vacuum" or that "the code can't actually implement."

**The architectural pattern:** Treat schema and code as two projections of a single domain model. Neither is the "master." Changes propagate in both directions:

- **Schema → Code:** When you add a type or field to the schema, the code must grow a resolver. The schema *prompts* implementation.
- **Code → Schema:** When the implementation reveals that a field is expensive to compute or that a relationship is actually 1:N not N:M, the schema must adjust. The code *informs* the schema.

**Composition with other patterns:**

- **Gated-value pattern and code co-evolution:** The union type `TaskOutcome = TaskCompleted | TaskFailed | TaskPending` prompts the resolver to return exactly one variant. If the implementation later needs a fourth state (`TaskCancelled`), the schema and code evolve together: add the variant to the union, add the resolver branch.

- **Edge-first and code co-evolution:** Edge types prompt the resolver to treat relationships as queryable entities with their own data-loading logic. If the implementation reveals that certain edges are computed rather than stored (e.g., "implied dependencies"), the schema might need a field on the edge type (`isImplied: Boolean!`) or a separate edge category.

- **Soft invariants and code co-evolution:** Soft invariants live in the code, so they evolve faster than the schema. The schema provides the stable structural contract; the code provides the evolving business rules. This separation is what makes the "do guided by prompt" principle practical — you can tighten validation in code without a schema migration.

**The drift-prevention rule:** After any significant schema change, ask: "Can I implement this?" After any significant code change, ask: "Should this be reflected in the schema?" The answer to both should be "yes" before the change is considered complete.

### 7. How These Patterns Compose: The Cohesion Map

These patterns are not independent — they reinforce each other. Here's how they compose into a cohesive architectural stance:

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
            │                       │                       │
            └───────────────────────┼───────────────────────┘
                                    │
                                    ▼
                    ┌─────────────────────────────────┐
                    │   CLIENT (queries the schema)   │
                    │   Sees: types, fields, enums    │
                    │   Doesn't see: directives,      │
                    │   soft invariants, resolver code │
                    └─────────────────────────────────┘
```

**The cohesion rules:**

1. **What the client queries** lives in the schema layer as fields, types, and enums.
2. **How the engine behaves** lives in the execution layer as directives.
3. **What the domain requires** lives in the code layer as soft invariants and business logic.
4. **Gated-value wrappers** bridge schema and code: the schema declares the shape of each state, the code determines which state applies.
5. **Edge types** bridge schema and graph: the schema declares relationships as queryable entities, the code resolves them from the underlying graph store.
6. **The "do guided" principle** governs the boundary: the schema guides, the code enforces, the directives modulate.

When a new cross-cutting concern arises (e.g., "we need to track who last modified each entity"), apply the cohesion map:
- Is it queryable by clients? → Field on the relevant types (or an `Auditable` interface).
- Is it execution metadata? → Directive or resolver middleware.
- Is it a business rule about who *can* modify? → Soft invariant in code.

This layered composition prevents the schema from becoming a dumping ground for every concern while ensuring that genuinely queryable domain concepts are first-class citizens in the type system.