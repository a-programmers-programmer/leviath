# NGRAPH-REVIEW — design & review ticket (graph/schema component of the LifeOps memory/world store)

**Author:** oracle worker (read-only inspection + one bounded local test run)
**Date:** 2026-09-06
**Status:** review document. No source changes, no deploys, no service restarts, no checker edits.
**Upstream authority:** Astra's verified NGRAPH assessment — `/data/work/lifeops-reliability/ngraph/ngraph-review.md` and `/data/work/lifeops-reliability/ngraph/ngraph-decide-ticket.md`. Those findings are **reused and cited here, not re-derived**. Where this document adds material, it is (a) the Python memory-graph half that Astra's slice did not cover, (b) the schema-design consolidation, and (c) a test/perf plan.

---

## 0. Scope, method, and what "verified" means here

Inspected directly (read-only):

| Area | Path |
|---|---|
| Unified GraphQL P0 gateway (JS/Apollo federation prototype) | `/data/work/life-ops/life-ops/graphql/unified/gateway/{index.js,subgraphs/*.js,test/approve-p0.js}` |
| First shipped fragment | `/data/work/life-ops/life-ops/graphql/unified/fragments/proposal-decision-card.graphql` |
| World-model projection + no-delete invariant | `/data/work/life-ops/life-ops/graphql/world-model/{schema.graphql,index.json}` |
| Signed module descriptors (Slice 0A) | `/data/work/life-ops/life-ops/graphql/modules/*.module.json`, `graphql/README.md` |
| Declarative behaviors | `/data/work/life-ops/life-ops/graphql/extensions/{behaviors.graphql,behaviors.json}` |
| **Memory graph (Python/Ariadne) — the real store-backed schema** | `/data/work/parlour-asset-review/memory/{schema.graphql,graphql_binder.py,db.py,memory_store.py,__init__.py}` |
| Mount + auth surface | `/data/work/parlour-asset-review/main.py:50,288-291,741` |
| Read-only schema viewer | `/data/work/parlour-asset-review/memory_map.py:1-22,95-102` |
| Agent-facing GraphQL protocol contract | `/data/work/life-ops/graphql-ify-protocol.md` |
| Agent context regions (world schema -> agent memory) | `/data/work/life-ops/world-schema-pipeline-report.md` |
| Manifesto + decisions | `/data/work/parlour-asset-review/proposals/ngraph-manifesto.json` |
| Non-goals / rig registry | `/data/work/life-ops/life-ops/architecture.md:142`, `projects.md:19` |

Executed: `python -m pytest test_memory_graph.py -q` in `/data/work/parlour-asset-review` -> **5 passed in 0.32s**. The fixture builds a throwaway DB under `tmp_path` (`test_memory_graph.py:10-20`), so no live store was mutated. Astra's Node VM negative probe of `control.js` is cited from their report; I did not re-run it.

Not established by this document: anything about the Windows rigs (`C:\Users\jlear\repos\ngraph`, `%LOCALAPPDATA%\LifeCity\world-schema`, the PowerShell world-schema scripts) — those paths do not exist on this box, so claims depending on them are **UNVERIFIED-here**, not absent. Deployment/exposure of any endpoint is likewise not asserted.

---

## 1. Architecture & schema design

### 1.1 The load-bearing observation: there are two "ngraph" halves, and only one is durable

| | **(A) Unified gateway** | **(B) Memory graph** |
|---|---|---|
| Stack | Apollo Federation v2, `@apollo/server`, in-process `LocalGraphQLDataSource` (`gateway/index.js:39-51`) | Ariadne + graphql-core over Flask (`memory/__init__.py:11-34`) |
| Schema source | Handwritten SDL per subgraph (`subgraphs/control.js:32-89`, `world.js:6-48`, `mcp.js:9-30`, `finance.js:6-30`) | One SDL file, `memory/schema.graphql` (192 lines), loaded by `build_schema()` (`graphql_binder.py:275-292`) |
| State | **In-process JS arrays**, explicitly a stand-in: `control.js:9-16` `LEDGER = {proposals, decisions, queue, next}` | **SQLite**, durable, room-scoped: `memory/db.py:89-138` (`entities`, `gates`, `edges` + `workspace_id` + `idx_entities_workspace_updated`) |
| Governance | None in the resolver (`control.js:98` takes no context; `:106` hardcodes `decidedBy: 'human:approve'`) | Rule interceptor + human gate enforced in code (`graphql_binder.py:52-79`, `:201-255`) |
| Tests | Happy-path only (`test/approve-p0.js:17-45`) | 5 tests incl. negative gate + no-permanent-delete (`test_memory_graph.py:188`, `:316`) — **passing locally** |
| Mounted? | Not mounted anywhere found; `start()` exists (`index.js:54-58`) but no caller traced | Mounted in the live app: `main.py:50` `import memory as memory_graph`, `main.py:741` `memory_graph.register(app)` |

**Design consequence (agrees with Astra's decide ticket, "Avoid a second work store"):** the ngraph *schema/graph* component of the memory/world store is **(B)**. (A) is a federation prototype and a UI/agent adapter shape, not a store. Any ngraph work that grows (A)'s ledger grows a competing authority; the correct move is to make (A) resolve against (B).

### 1.2 Schema design as built (B) — what is genuinely good

`memory/schema.graphql` already encodes several manifesto properties structurally rather than by convention:

- **Entity/lifecycle core.** `interface MemoryEntity { id, lifecycle, createdAt, updatedAt, createdById, metadata }` (`:25-32`); `enum Lifecycle { ACTIVE TRASHED TOMBSTONE }` (`:4-8`). Concrete types: `Fact`, `Decision`, `Rule`, `Seat`, `Agent`, `Node`, `Channel`, `Slip`, `ThreadEntry` (`:53-160`).
- **No permanent delete, by construction.** The only removal mutations are `trashEntity` / `tombstoneEntity` (`:188-189`); there is no `deletePermanent`/`purge` field. The same invariant is written into the world-model SDL as a single-member enum `DeletePolicy { TRASH_ONLY }` plus `type Trashable` (`world-model/schema.graphql`, "NO-PERMANENT-DELETE INVARIANT" block) and into `extensions/behaviors.graphql:36-56,119-120` (`DeleteDisposition`, `@trashOnly`, `extend schema @trashOnly`). Manifesto 4.4 "no permanent delete, ever" is therefore **IMPLEMENTED at schema level** in (B) and asserted by `test_memory_graph.py:316`.
- **Graph edges are first-class.** `type Edge { id, fromId, toId, type, metadata }` with `enum EdgeType { OWNS REFERENCES PART_OF LOCATED_AT ASSIGNED_TO TRIGGERS }` (`:16-23,45-51`), queryable by `graphEdges(fromId,toId)` (`:179`) and indexed both ways (`memory_store.py:111-112`). `TRIGGERS`/`ASSIGNED_TO` are exactly the edges a decision->work lineage needs.
- **Human gate as data + as code.** `type Gate { status, reason, requestedBy, approvedBy, targetMutation, payload, createdAt }` (`:34-43`); `union MutationResult = GenericSuccess | GatePending` (`:162-172`) so a gated write *returns a gate* instead of throwing. Enforcement: `rule_interceptor_middleware` refuses agent `resolveGate` outright (`graphql_binder.py:58-59`), evaluates `Rule`s by `triggerMutation` + condition match (`:63-77`), and creates a pending gate carrying the original payload; `resolveGate` re-checks `is_human` (`:205-206`) and `status == PENDING` (`:211-212`) before replaying the target mutation with the *original requester* as `created_by_id` (`:223-253`). This is manifesto 3.3/4.1 "refuse-in-code, humans top the ladder" actually running.
- **Tenancy seam.** `workspace_id` is nullable, never backfilled, and every read carries the scope predicate / every write stamps it (`memory/db.py:36-49,128-136`; `memory_store.py:19-27,116-122`). `ALL_WORKSPACES` is a sentinel with exactly one caller, so "forgot the scope argument" defaults to the NULL room, not to everything (`db.py:19-33`). This is the isolation primitive the viewer-extension story needs.

### 1.3 Schema design gaps (what to fix, in priority order)

1. **AuthN is string-sniffing, not identity.** `parse_token` (`graphql_binder.py:17-36`) base64-decodes a JWT-ish payload *without verifying a signature*, and falls back to `if "human" in token.lower(): is_human = True` (`:34-35`). Every gate property in 1.2 therefore rests on an unverified claim: any caller who sends `Authorization: Bearer human_anything` is a human. The existing tests use exactly that (`test_memory_graph.py:41,189`). **This is the single highest-severity design defect in the durable half** and it is not in Astra's ledger (their probe covered `control.js`). Fix: verify a signed grant (OIDC/JWT signature or an HMAC'd agent grant) before `is_human` can be true; make unsigned tokens resolve to a non-human viewer with no gate authority. Manifesto 3.7: "an unsigned state is never treated as human authority."
2. **No idempotency key on any write.** `createFact/createDecision/createRule/createEdge/resolveGate` (`schema.graphql:183-191`) take no `idempotencyKey`, and no idempotency/dedup logic exists in `memory/*.py` (grep for `idempot` hits only unrelated DDL comments). This is the same defect Astra demonstrated in (A) — repeated approval of `p1` produced `t1` then `t2` and a queue of length 2 — so it is a *system-wide* gap, not a prototype-only one. The protocol doc already mandates the fix: "every mutating field should accept a client-supplied `idempotencyKey` and return a 'this was already done' signal" (`graphql-ify-protocol.md` 2.7, 6 `CONFLICT`).
3. **No lineage stamp on writes.** `MemoryEntity.createdById` records *who*, but nothing records *under which gate / decision / grant* a write happened, and there is no hash chain. Manifesto 4.3 requires the human-requiredness to ride on the data. Minimum viable: add `lineage: JSON` (gateId, decisionId, grantId, prevHash, hash) to gated writes and stamp it in `resolveGate`'s replay path (`graphql_binder.py:223-253`), where the gate is already in hand.
4. **No read-side authorization / field visibility.** `Query.getEntity/queryFacts/activeRules/pendingGates/graphEdges` (`:174-180`) resolve straight to the DB with no viewer check (`graphql_binder.py:136-154`); only `workspace_id` scoping limits them, and the mounted instance uses the default room (`main.py:741` passes no `workspace_id`). Manifesto 3.6 ("no secret leaks through a well-formed query") and 3.7 (field visibility by context) are **NOT IMPLEMENTED** here. Protocol 4 requires per-field/per-claim allowlists server-side.
5. **No agent-context taint.** Nothing in `memory/*.py` reads a caller context/taint set; authority is a boolean `is_human`. Manifesto 3.7's "same agent, same credentials, different context -> different authority" is **NOT IMPLEMENTED**. (Leviath taint semantics are a different subsystem and are not evidence of ngraph enforcement — per Astra's ledger.)
6. **No query-safety middleware.** No depth limit, complexity/cost budget, alias cap, persisted-query allowlist, per-principal rate limit, or per-request timeout anywhere in the mounted path (`memory/__init__.py:11-34` calls `graphql_sync` with only the rule middleware). Protocol 4 lists all of these as mandatory and 10 sketches the middleware. Introspection is also implicitly on.
7. **Descriptor-driven generation is not demonstrated.** The pieces exist — signed module descriptors with grants and tool mappings (`modules/state.module.json` 9 fields, `ui.module.json` 10, `work_context.module.json` 7, `builder.module.json` `explicit_only` + `no_hot_patch`/`no_self_authorize`/`non_amplifying_delegation`; `graphql/README.md`: "Not a running GraphQL server yet — signed module descriptors for Guide/compiler"), the world-schema generator output (`world-model/index.json`: revision `20260820-094508`, `type_count: 11`, `runs_scanned: 0`), and the fragment (`proposal-decision-card.graphql:9-16`). But no generator execution producing resolvers + agent tools + UI from one fragment was traced, and the fragment's "this is also the Approve mutation" claim lives in **comments** (`:1-7,18-26`). Verdict stands as Astra recorded: **UNVERIFIED / not demonstrated by this slice.**
8. **Connector subgraphs return fixture data.** `mcp.js:32-45` hardcodes two servers and four tools; `finance.js:32-41` hardcodes two accounts and two transactions; `world.js:50-61` hardcodes a two-type world. `test/approve-p0.js:40-45` accepts that static data as "connected". These are shape proofs, not adapters — matching Astra's "PROTOTYPE ONLY".

### 1.4 Target architecture (recommended, minimal-delta)

```
                 +-----------------------------------------------+
   human UI ---> |  ONE gateway (Apollo supergraph, (A) index.js) |
   agent tool -> |  - signed-viewer authN  - depth/cost/alias caps|
   MCP caller -> |  - persisted-query allowlist - per-principal RL|
                 +-----------------------+-----------------------+
                                         | LocalGraphQLDataSource -> remote subgraph URL
              +--------------------------+-----------------------+
              v                          v                       v
      control subgraph             world subgraph         connector subgraphs
      (thin adapter)               (projection only)      (mcp / finance / email)
              |                          |
              v                          v
   +----------------------+   world-schema pipeline (generated SDL,
   | memory graph (B)     |   revisions, TRASH_ONLY behaviors)
   | Ariadne + SQLite     |
   | entities/gates/edges |  <-- system of record for graph + gates
   | rules + lineage      |
   +----------+-----------+
              v
   durable work (Desk proposal/queue) --> Leviath run --> result --> receipt
```

Rules that make this real rather than aspirational:

- **(B) owns graph truth; (A) owns the wire.** `control.js`'s `LEDGER` (`:9-16`) is deleted, not extended; `decideProposal` becomes an adapter that calls the same shared operation the Desk's proposal path calls, and returns the gate/ticket/decision that (B) persisted. This is precisely Astra's recommendation ("Keep GraphQL as an adapter over shared business logic", seams named in `ngraph-decide-ticket.md`).
- **One correlation chain, stamped at write time:** proposal/decision id -> work id -> Leviath run id -> result artifact -> delivery receipt, carried on `Edge(TRIGGERS)` rows plus the new `lineage` field, so "Approve saved but nothing ran" and "inference finished so it must have been delivered" are both answerable from data.
- **Projection never becomes truth.** `world-model/schema.graphql:1` ("Agent world model (projection). Not beads/Dolt truth.") and `world.js:2` stay load-bearing; the world subgraph is read-only and regenerated.
- **Viewer extension = regeneration, scoped by `workspace_id`.** `memory_map.py:1-22` is the working precedent: a viewer that owns no schema and no tables, derives its shape by walking `memory.graphql_binder.schema`, and cannot drift. Generalize that (per-viewer `Vx = C (+) extx`, read-only, forwarding the same gated core mutation) instead of inventing a new extension mechanism.
- **Respect the declared non-goal.** `architecture.md:142` lists "NGRAPH production runtime before manifesto-level ideas need execution" as out of scope; `projects.md:19` registers `ngraph` as `research / arch`. Nothing in this document authorizes a production runtime.

---

## 2. Integration points

### 2.1 GraphQL server (live Flask app)

- **Mount:** `memory.register(app)` at `main.py:741` exposes `POST /graphql` (`memory/__init__.py:11`), building context `{viewer, db, request}` (`:20-25`) and running `graphql_sync(schema, data, context_value, middleware=[rule_interceptor_middleware], debug=True)` (`:27-33`).
- **Auth boundary:** `graphql_server` is on the sync-token allowlist (`main.py:288-291`) with the stated intent "Reads + safe creates are token-OK; human-gated mutations are refused in code". That intent holds *only* because of the interceptor — and the interceptor's `is_human` comes from unverified `parse_token` (1.3.1). The app's OAuth path (`main.py:155-162,387-431`) is the human identity source; the graph endpoint does not currently consult it. **Integration fix #1: derive `viewer` from the OAuth/session identity for humans and from a signed grant for agents; keep the token-only path non-human by construction.**
- **`debug=True` in production** (`memory/__init__.py:32`) leaks resolver/stack detail to callers; protocol 6 wants structured `errorCode` + `message` + `path` instead. Turn debug off and map `PermissionError`/`ValueError` from the binder (`graphql_binder.py:59,206,210,212`) to `FORBIDDEN`/`NOT_FOUND`/`CONFLICT`.
- **Gateway <-> app:** (A) is not mounted and has no HTTP subgraph fetch (`index.js:43-47`). The integration step is to point `buildService` at the real `/graphql` (or at a shared Python operation) rather than at in-process fixtures, and to pass an authenticated context — `index.js:49` currently constructs `ApolloServer` with **no** context/auth plumbing, which is why Astra's probe saw an empty caller context still return an approval ticket.
- **Desk / work substrate:** durable proposal + queue records already exist (`proposal_registry.py`, `proposals_pub.py`, `queue_store.py`, `kanban_pub.py`); Astra's ticket names the decision handler in `proposals_pub.py` and the auth in `main.py` as the seams to map first. The graph must not re-implement them.

### 2.2 Agent context

- **Context regions (designed, Windows-side, UNVERIFIED here).** `world-schema-pipeline-report.md` documents two Leviath agent memory zones fed by the world-schema pipeline: `world_model` (`kind = hashmap`, `budget = 6%`, `max_tokens = 6000`, `max_entries = 40`) and `graphql_memory` (`kind = sliding_window`, `budget = 5%`, `max_tokens = 5000`, `max_items = 10`), seeded by `world-schema-export-region.ps1 -Mode hashmap` and `world-schema-project-query.ps1 -Query types`, added to both `worker` and `reviewer` `agent.leviath`, and explicitly additive ("absent data seeds leave the regions empty, never fail a run"). Observed seed output there: `types=10 rev=20260809-161501`, hashmap lines like `Bead|conf=0.99|fields=title,status,id|Durable work unit in beads store`. On this box `world-model/index.json` shows a later revision (`20260820-094508`, `type_count: 11`, `runs_scanned: 0`) — i.e. the projection is regenerated but the run-scan evidence is empty.
- **This is the "slim context projection" mechanism** (manifesto 3.8): the agent gets a bounded, trimmed type map, not the store or the machinery. It is *partial* — the budget/shape discipline is real and measurable, but end-to-end context minimization for agent *operations* is not demonstrated, and the seed commands are PowerShell paths that do not exist here.
- **Agent tool surface.** The intended bridge is protocol 5: one raw `/graphql` endpoint as the canonical contract plus a generated function-call/MCP wrapper per mutation, with the tool surface kept narrow ("don't expose every introspection/graph-metadata query as a tool"). `modules/*.module.json` is the registry that would generate those descriptors (26 tools = 9 state + 10 ui + 7 work_context, `graphql/README.md`), and `mcp.js:9-30` is the graph-side shape (`McpServer`/`McpTool` with `inputSchema`). Neither is wired to the other yet.
- **Taint into context.** For 3.7 to be more than a slogan, the agent's context region set must be *read* by the resolver: the same `viewer` object should carry `heldScopes`/`taints` derived from what the caller already fetched in this session, and gated mutations must consult it. Today the binder reads only `viewer.is_human` and `viewer.id`.

---

## 3. Verification / test plan and performance expectations

### 3.1 What is verified today

| Item | Evidence | Result |
|---|---|---|
| Memory graph schema builds & serves | `test_memory_graph.py:33`, run locally | **PASS** (5 passed / 0.32s) |
| Fact CRUD + `getEntity` + `queryFacts` round trip | `test_memory_graph.py:40-114` | **PASS** |
| Edge creation/traversal | `test_memory_graph.py:117` | **PASS** |
| Rule-driven human gate returns `GatePending`; agent `resolveGate` refused | `test_memory_graph.py:188-260`; `graphql_binder.py:58-59,63-77,205-206` | **PASS** |
| No permanent delete (trash/tombstone only) | `test_memory_graph.py:316`; `schema.graphql:188-189` | **PASS** |
| Gateway composes 4 subgraphs; Approve -> `decideProposal` -> PENDING ticket | `test/approve-p0.js:17-38`; `unified/README.md` "10/10" | **PASS but happy-path only** — asserts `decidedBy === 'human:approve'` (`:34`), i.e. it *encodes* the hardcoded actor as correct |
| Unauthenticated approval refused; repeated approval idempotent | Astra's Node VM probe of `control.js` | **RED** (both assertions failed; empty context approved, `p1` -> `t1` then `t2`, queue length 2) |

The asymmetry is the headline: the durable half has passing negative tests for gating and deletion; the prototype half has none, and its one test blesses the defect.

### 3.2 Required tests before any activation (adopting Astra's seven, plus four for the memory half)

Astra's list (`ngraph-decide-ticket.md`, "Tests required before activation") is adopted verbatim as the acceptance gate: (1) unauthenticated mutation refused with no state change; (2) agent without a delegated grant cannot claim human approval; (3) repeated identical request returns the original work id, conflicting replay rejected; (4) durable decision/outbox transaction survives an interrupted dispatcher without duplicated work; (5) failed worker retains partial output and returns explicit failure; (6) UI and agent queries observe the same durable state and result; (7) delivery failure stays pending delivery.

Added for (B), where the auth/gate code actually lives:

8. **`parse_token` negative suite** — unsigned token, tampered payload, `"human"` substring in an agent token, missing/expired grant => `is_human == False` and gate authority denied. This test must be written *before* the fix, and must fail today (`graphql_binder.py:17-36`).
9. **Gate replay idempotency** — `resolveGate(g, approve=true)` twice => second call rejected on `status != PENDING` (`:211-212`) *and* the target mutation is not applied twice (assert one `entities` row, not two).
10. **Read authorization** — a viewer in room A cannot read room B's entities/edges/gates through `getEntity`/`graphEdges`/`pendingGates`; `ALL_WORKSPACES` is unreachable from a request context (`db.py:19-33,36-49`).
11. **Query-safety middleware** — depth > 15, cost > budget, alias count > cap, non-allowlisted operation in production mode => rejected with a machine-readable code and an audited denial (protocol 4, 8).

Contract/CI additions: snapshot `memory/schema.graphql` and the composed supergraph SDL and fail on a breaking diff (protocol 9); assert `DeletePolicy`/`Lifecycle` still have no permanent-delete member; assert every `Mutation` field either carries a gate rule or is explicitly listed as ungated (this is the machine-checkable form of "you cannot emit a mutation that doesn't carry its gate").

Test hygiene: keep using `tmp_path` DBs (`test_memory_graph.py:10-20`) — never run tests against `memory.db` or the live app, since imports build the schema at module load (`graphql_binder.py:292`) and `register()` opens a real DB (`memory/__init__.py:9`).

### 3.3 Performance expectations

Measured baseline (this box, local, no network): 5 end-to-end operations through the Flask test client, including a rule-intercepted gate and a trash mutation, completed in **0.32s** => roughly **<=65 ms/op** at trivial data volume. That is a correctness-run artifact, not a load test; treat the numbers below as **targets to be measured**, and record the actuals next to them before activation.

| Dimension | Target | Rationale / lever |
|---|---|---|
| Gated mutation p50 / p95 (single room, <=100k entities) | <= 25 ms / <= 80 ms | One SQLite write + one gate write; keep them in one transaction. `MemoryStore` is already lock-guarded (`memory_store.py:36-40`) |
| Read query p50 / p95 (`getEntity`, `queryFacts`, `graphEdges` 1-hop) | <= 10 ms / <= 40 ms | Uses existing indexes (`db.py:133-135`, `memory_store.py:108-113`); 1-hop edge fan-out must stay index-bounded |
| Room-scoped read cost as rooms grow | flat, not fan-out | `db.py:52-58` states the intent ("one more indexed predicate — nothing fans out"); verify with a 20-room fixture |
| Supergraph composition at gateway boot | <= 2 s for 4 subgraphs; <= 10 s at 20 | `composeServices` runs at build (`index.js:26-37`); cache the composed SDL and fail fast on composition errors (already throws, `:33-35`) |
| World-schema regeneration (compact hook) | <= 30 s for <=200 types / <=30 revisions | Matches the cleanup caps in `extensions/behaviors.graphql:103-109` (`maxAgeDays 14`, `minConfidence 0.4`, `maxTypes 200`, `maxRevisions 30`); the reported run was 9.7s for 10 types (Windows, UNVERIFIED here) |
| Agent context cost | <= 6% budget / 6000 tokens / 40 entries (`world_model`); <= 5% / 5000 / 10 (`graphql_memory`) | Already the declared region caps; a regression here is a context-window regression, so assert it in CI against a generated seed |
| Schema-map viewer payload | ~14 KB grouped, cached; never a >=100 KB `__schema` dump | `memory_map.py:14-19,95-102` — cached walk of the executable schema |
| Query-safety ceilings | depth <= 15, cost <= 1000, aliases <= 20, per-request timeout enforced | Protocol 4/10 defaults; must be measured as *rejection* latency (< 5 ms, pre-execution) |
| Concurrency | no lost gate under N parallel identical approvals | Requires the idempotency key (1.3.2) + a unique constraint; today the JS ledger demonstrably duplicates (Astra's probe) |

Load-test method when the time comes: `k6`/`locust` against a *staging* instance with a seeded room (10k entities, 50k edges, 1k gates), reporting p50/p95/p99 per operation plus denial counts; never against the live desk.

---

## 4. Claim ledger (Astra's verdicts, reused; memory-half verdicts added)

Verdict vocabulary per the task: IMPLEMENTED / PARTIAL / NOT-FOUND-IN-INSPECTED-SCOPE / UNVERIFIED.

| Manifesto claim | (A) Unified gateway | (B) Memory graph | Evidence |
|---|---|---|---|
| Shared gateway/schema across callers | PARTIAL (composes; no proof live Desk/agents use it) | PARTIAL (one executable schema, one mounted endpoint; UI viewer derives from it) | `index.js:17-36,39-51`; `main.py:741`; `memory_map.py:1-22` |
| Common human-agent write path | PARTIAL, one mutation, no auth context | PARTIAL — same mutation set for both, but human/agent distinguished by unverified token | `control.js:86-88,98-119`; `graphql_binder.py:17-36,52-79` |
| Descriptor-generated schema/resolvers/tools/UI | UNVERIFIED (no generator execution traced) | NOT-FOUND-IN-INSPECTED-SCOPE | `modules/*.module.json`; `graphql/README.md`; fragment comments `:1-7,18-26` |
| Viewer extension isolation (`Vx = C (+) extx`) | UNVERIFIED | PARTIAL — read-only viewer isolation demonstrated; per-viewer *fields* not | `memory_map.py:1-22`; `db.py:19-49` |
| Read/write authorization | NOT IMPLEMENTED in inspected resolver | Writes PARTIAL (gate enforced); **reads NOT IMPLEMENTED**; authN UNSOUND | `control.js:98,106`; `index.js:49`; `graphql_binder.py:136-154,205-206,34-35` |
| Context taint | UNVERIFIED globally; absent in resolver | NOT IMPLEMENTED (boolean `is_human` only) | `control.js:98-119`; `graphql_binder.py:58,66,205` |
| Slim context projections | PARTIAL (schema/fragment projects fields) | PARTIAL (bounded agent regions designed; not verified on this box) | `control.js:40-88`; fragment `:9-16`; `world-schema-pipeline-report.md` |
| Lineage / signed authority | NOT IMPLEMENTED (fixed actor + time) | NOT IMPLEMENTED (`createdById` only; no gate/decision stamp, no hash chain) | `control.js:104-108`; `schema.graphql:25-32`; `graphql_binder.py:223-253` |
| Real adapters / runtime integration | PROTOTYPE ONLY (in-process ledger; fixture connectors) | PARTIAL — durable SQLite + mounted endpoint; no work/Leviath dispatch from the graph | `control.js:9-16,111-116`; `mcp.js:32-50`; `finance.js:32-49`; `db.py:89-138` |
| No permanent delete | NOT-FOUND-IN-INSPECTED-SCOPE (no delete mutation at all) | **IMPLEMENTED** (schema-enforced + tested) | `schema.graphql:4-8,188-189`; `world-model/schema.graphql` DeletePolicy; `behaviors.graphql:36-56,119-120`; `test_memory_graph.py:316` |

---

## 5. Recommendation

1. **Do not certify the manifesto from the P0 test.** `unified/README.md`'s "BUILT + VERIFIED (P0), 10/10" and `test/approve-p0.js:34` assert the hardcoded `human:approve` actor as correct behaviour; Astra's probe shows the same resolver approves with an empty caller context and duplicates tickets.
2. **Name (B) the ngraph schema/graph component of record** and make (A) an adapter over it plus the shared Desk operation — Astra's recommended slice, unchanged: one authenticated human-or-delegated-agent operation creates/reuses durable work, dispatches Leviath once, and exposes the same status/result to both callers.
3. **Fix `parse_token` before adding any capability.** Every governance property in the durable half currently rests on an unsigned, substring-matched identity claim. This is cheap, local, testable, and it is a prerequisite for tests 1-3 of the activation gate.
4. **Add `idempotencyKey` + lineage stamp to gated writes** (one schema addition, one interceptor change), which closes the duplicate-work class of bug in both halves at once.
5. **Then, and only then, wire the gateway context** (`index.js:49`) to a real authenticated viewer and swap `LocalGraphQLDataSource` for the shared operation.
6. **Hold the line on scope:** no live data migration, no deployment, no new standing permission, no outbound message, no production ngraph runtime (`architecture.md:142`), no edits to Astra's checker files or services.

---

## 6. Limitations of this document

- Read-only inspection plus one bounded `pytest` run against a `tmp_path` DB. No live app was started, no network call made, no daemon touched, no secrets/env files opened.
- Astra's `control.js` negative probe is cited, not re-executed; their correction of the investigator's line numbers is carried forward.
- Windows-side rigs (the `ngraph` repo, `LifeCity\world-schema`, the PowerShell pipeline, the Leviath agent region files) are absent here, so all claims depending on them are UNVERIFIED-here rather than absent globally.
- No assertion is made about public endpoint exposure or exploitability in any deployment.
