# MoA Fan-Out Research: Model-Onboarding Primitives & Typed Workflow Mechanics

## Aggregator Synthesis — Three Lenses Reconciled

**Lenses:** Tencent HY3, DeepSeek v4 Pro, Qwen 3.8 Max  
**Convergence:** Near-total. All three lenses independently converged on the same architecture. Minor divergences in stage count, scoring weights, migration phases, and repair-loop ordering were resolved by adopting the most comprehensive formulation.

---

## §0 Methodology

### Convergence Assessment
All three lenses independently arrived at: seven primitive categories with concrete typed fields; sealed discriminated unions for vendor divergence; `Money = Decimal(scale=6) + ISO 4217`; vault-reference-only credentials; nominal versioned type lattice with explicit `assignableTo` edges; signature-hash catalog indexing with `displayKind` excluded; gates-then-rank matching with θ=0.85 and ε=0.02; hash-chained audit; five core pipeline stages plus promotion; five anti-bag lint rules.

### Resolved Divergences

| Divergence | Resolution | Rationale |
|---|---|---|
| Stage count (8 vs 6) | 6 stages | CLASSIFY/MATCH are deterministic pre-conditions, not governed stages |
| Migration phases (4 vs 6) | 6 phases | P0 Freeze and P5 Remove add explicit guardrails at zero risk |
| Match scoring | Gates-then-rank + Qwen weights | Binary gates for typeConformance/effectSubset/budget/residency; scored rank with latencyFit |
| Repair loop (4 vs 5 steps) | 5-step order | "Split step" needed for complex drafts |
| Auto-approve predicate | Strictest union (DeepSeek ∪ Qwen) | More conditions = safer automation |
| Coercion model | Qwen's strongest | Coercion steps carry own cost model and effects — silent transformations impossible |
| Reasoning `spec_uri` | Mandatory on ImplicitOnlyReasoning | Closes accountability gap |
| Minimality justification | All three adopted | Derivation (why) + test (how to verify) + ownership (who maintains) |

**Net effect:** The fallback pipeline is the catalog's growth mechanism. Every unmatched task yields either a validated, approved, typed workflow or a precise `MissingPrimitive` adapter-build request. Neither outcome is a free-text apology.

---

## §1 Primitives & Variables of Model Onboarding

### 1.0 Design Principles

**Minimality:** A field is a primitive iff it derives from (1) the model's published API contract, (2) operational harness requirements, or (3) governance/compliance obligations. Acceptance test: can you delete this field and still correctly route, validate, and govern?

**Typing discipline:** Every field is a concrete typed value — closed enums, `Int`, `Bool`, `Decimal` (scale=6), ISO 8601 durations, URIs, `JsonPointer`, and sealed discriminated unions. Zero `Map<String,Any>`, zero `**kwargs`, zero `extras: [String!]`, zero stringly-typed mode flags.

**Derived vs. immutable:** `ModelOnboardingRecord` holds immutable facts. Derived telemetry lives in a separate mutable `ObservationsStore`.

### 1.1 Identity

```
ModelIdentity {
  modelKey: ModelKey!              // stable internal primary key
  provider: Provider!              // OPENAI | ANTHROPIC | GOOGLE | AWS_BEDROCK | DEEPSEEK | ALIBABA | META | MISTRAL | REPLICATE | FAL | HUGGINGFACE | CUSTOM
  vendorModelId: String!           // vendor's own identifier — may change
  family: ModelFamily!             // GPT4 | CLAUDE3 | GEMINI | LLAMA3 | QWEN | DEEPSEEK_V3 | NOVA | STABLE_DIFFUSION | FLUX | ELEVENLABS
  revision: String!
  lifecycle: ModelLifecycle!       // PREVIEW | GA | DEPRECATED | SUNSET | RETIRED
  sunsetDate: DateTime?            // required iff lifecycle ∈ {DEPRECATED, SUNSET}
  aliases: [ModelKey!]!
  modalities: [Modality!]!         // TEXT | IMAGE | AUDIO | MESH_3D | VIDEO | CODE | EMBEDDING
  residencyRegions: [RegionCode!]! // ISO 3166-1 alpha-2 + cloud region codes
  licenseRef: Uri?
  documentationUri: Uri!
  releaseDate: DateTime!
  maxConcurrency: Int!
}
```

### 1.2 Wire Communication

```
WireCommunication {
  endpoint: Endpoint!              // sealed: RestHttps | Grpc | WebSocket | SdkOnly
  protocolShape: ProtocolShape!    // OPENAI_COMPATIBLE | ANTHROPIC_NATIVE | GEMINI_NATIVE | BEDROCK_CONVERSE | BEDROCK_INVOKE_MODEL | DEEPSEEK_NATIVE | DASHSCOPE_NATIVE | REPLICATE_PREDICTIONS | FAL_QUEUE | CUSTOM_OPENAPI
  authScheme: AuthScheme!          // sealed: BearerToken | ApiKeyHeader | SigV4 | OAuth2ClientCredentials | MtlsCert | AwsIamRole | GcpServiceAccount | NoAuth
  streamingMode: StreamingMode!    // NONE | SSE | CHUNKED_TRANSFER | BIDI_WEBSOCKET | WEBHOOK_CALLBACK | POLLING
  asyncPattern: AsyncPattern!      // SYNC_ONLY | POLLING | WEBHOOK | BOTH
  requestSchemaRef: SchemaRef! responseSchemaRef: SchemaRef! errorSchemaRef: SchemaRef!
  timeout: Duration8601! connectTimeout: Duration8601!
  maxRetries: Int! (0..10) retryBackoff: BackoffStrategy!
  idempotency: IdempotencyConfig!
  usageFieldMap: UsageFieldMap!    // REQUIRED — JsonPointers for prompt_tokens, completion_tokens, cached_tokens, reasoning_tokens, total_tokens
}
```

**Critical field — `usageFieldMap`:** REQUIRED and blocking at onboarding time (all three lenses unanimous). Without typed `JsonPointer`s into each vendor's response shape, the harness cannot compute actual spend — budget enforcement is impossible.

### 1.3 Execution Capabilities

```
ExecutionCapabilities {
  toolCalling: ToolCallingCapability!    // NONE | NATIVE_FUNCTION_CALL | TOOL_USE_BLOCKS | LEGACY_FUNCTIONS_DEPRECATED
  parallelToolCalls: Boolean! maxToolsPerRequest: Int?
  structuredOutput: StructuredOutputCapability!  // NONE | JSON_MODE | JSON_SCHEMA_STRICT | JSON_SCHEMA_LOOSE
  structuredOutputRequiresAdditionalPropertiesFalse: Boolean!
  vision: VisionCapability?              // present iff IMAGE ∈ modalities (BLOCKING iff-invariant)
  audioInput: AudioInputCapability?      // present iff AUDIO ∈ modalities
  audioOutput: AudioOutputCapability?
  imageGeneration: ImageGenCapability?   // present iff IMAGE ∈ modalities AND model generates images
  meshGeneration3d: MeshGenCapability?   // present iff MESH_3D ∈ modalities
  embeddings: EmbeddingsCapability?      // present iff EMBEDDING ∈ modalities
  executionModes: [ExecutionMode!]!      // REALTIME | BATCH | ASYNC_POLL
  batchDiscountMultiplier: Decimal?
  statefulness: StatefulnessConfig!      // STATELESS | CONVERSATION_HISTORY | CACHED_CONTENT | RESPONSE_CONTINUATION
  artifactKinds: [ArtifactKind!]!        // INLINE_TEXT | INLINE_JSON | INLINE_BASE64 | SIGNED_URL | BLOB_HANDLE | STREAM_CHUNK
}
```

**Blocking iff-invariant:** `vision` iff `IMAGE ∈ modalities`, `audioInput` iff `AUDIO ∈ modalities`, `meshGeneration3d` iff `MESH_3D ∈ modalities`. Enforced at onboarding time by schema validator.

**Modality sub-structs:** VisionCapability (formats, resolution, detailLevels); ImageGenCapability (resolutions, nPerRequest, conditioningModes, filterLevel, asyncPattern); MeshGenCapability (outputFormats GLB|GLTF|OBJ|USDZ|STL|PLY, textureSupport, maxTriangles, inputModes, asyncPattern); AudioInputCapability (codecs, sampleRates, channels, maxDuration, languages); AudioOutputCapability (codecs, sampleRates, voices, latencyClass, streaming).

### 1.4 Thinking/Reasoning Controls

**7-variant discriminated union** (all three lenses unanimous):

```
union ReasoningConfig =
    OpenAiReasoning { effort: MINIMAL|LOW|MEDIUM|HIGH, summary?, reasoningTokensField: JsonPointer }
  | AnthropicReasoning { budgetTokens: 1024..64000, redactedThinking, interleavedThinking, thinkingBlockField, redactedThinkingBlockField }
  | GeminiReasoning { thinkingBudget: -1|0|>0, includeThoughts, thinkingBudgetField }
  | BedrockReasoning { thinkingBudget, budgetStrategy: FIXED|PERCENTAGE, reasoningTextField }
  | BooleanThinking { enableThinking, reasoningContentField }
  | ImplicitOnlyReasoning { specUri: Uri! }  // MANDATORY
  | NoReasoning {}

enum ChainOfThoughtExposurePolicy { NONE | REDACTED_ONLY | FULL | CONFIGURABLE }
```

Each variant carries `JsonPointer` field maps. `ImplicitOnlyReasoning.specUri` is **mandatory** — closing the accountability gap.

### 1.5 Token/Context Bounds

```
TokenContextBounds {
  contextWindowTokens: Int! (1..10,000,000)
  maxInputTokens: Int! maxOutputTokens: Int! maxOutputTokensDefault: Int!
  effectiveMaxOutputAtFullContext: Int!  // [inference] distinct field — vendors reduce output at full context
  tokenizer: Tokenizer!              // sealed: NamedTokenizer | OpaqueTokenizer
  tokenCountEndpoint: Uri?
  promptCache: PromptCacheConfig!    // supported, granularity, minCacheableTokens, ttl, writeSurchargeMultiplier, readDiscountMultiplier
  overflowPolicy: OverflowPolicy!    // REJECT_400 | TRUNCATE_HEAD | TRUNCATE_TAIL | AUTO_SUMMARIZE_AND_RETRY | HARD_FAIL
}
```

**Critical invariant:** Do NOT assume `contextWindow ≥ maxInput + maxOutput`. Store `effectiveMaxOutputAtFullContext` as a distinct field.

### 1.6 Credential/Auth

**Hard constraint (unanimous): raw secrets NEVER appear in any schema.** Only vault/STS references.

```
CredentialConfig {
  credentialRef: CredentialRef!      // sealed: VaultKvRef | AwsSecretsManagerRef | GcpSecretManagerRef | AzureKeyVaultRef | K8sSecretRef | EphemeralStsRef(900..43200s)
  credentialKind: CredentialKind!    // API_KEY | OAUTH2_CLIENT_CREDENTIALS | SERVICE_ACCOUNT_JWT | SIGV4_IAM_ROLE | MTLS_CERT | PAT | HMAC_SIGNING_SECRET
  oauthScopes: [Uri!]?
  principal: Principal!
  environmentBinding: [Environment!]!
  rotation: RotationConfig!          // maxAgeDays, rotateVia, dualKeyOverlapWindow
  leastPrivilege: LeastPrivilegeConfig!  // permittedOperations, networkEgressAllowlist, piiScope
  modelAccessGrant: ModelAccessGrantConfig!  // required: Boolean — Bedrock per-region access is a real blocker
}
```

### 1.7 Billing/Rate Limits

```
BillingConfig {
  rateLimits: RateLimits!            // rpm, tpm, rpd, concurrency, imagesPerMinute?, audioSecondsPerMinute?, tier
  rateLimitHeaders: RateLimitHeaders!  // limitHeader, remainingHeader, resetHeader, resetSemantics
  quotaResetWindow: Duration8601!
  backpressure: BackpressureConfig!  // on429 strategy + failoverChain: [ModelKey]
  pricing: PricingModel!             // sealed: PerTokenPricing | PerImagePricing | PerAudioSecondPricing | PerMeshUnitPricing | PerGpuSecondPricing | ProvisionedPricing
  billingUnit: BillingUnit!
  budgetCeiling: BudgetCeiling!      // daily?, monthly?, perRun?, enforcement: ALERT_ONLY | THROTTLE | HARD_STOP
  spendAttribution: SpendAttribution!
  freeTierQuota: FreeTierQuota?
}

Money { amount: Decimal(scale=6), currency: Iso4217Code }  // NEVER float — non-negotiable
```

### 1.8 Anti-String-Bag Lint Rules (blocking at registration)

1. **NO_ANY:** No `Any`, `object`, `Map<String,Any>`, `**kwargs`, or `[String!]` as contract surface
2. **VENDOR_SCHEMA:** Vendor-specific params in typed per-provider schema via `SchemaRef`
3. **UNION_NOT_BAG:** New vendor divergence ⇒ add discriminated union variant via migration + review
4. **OTHER_ESCAPE:** `OTHER` enum members only with mandatory typed escape record `{other_kind, spec_uri, adapter_class}`
5. **DERIVED_SEPARATE:** Derived telemetry in mutable `ObservationsStore`, never in immutable `ModelOnboardingRecord`

---

## §2 Strongly-Typed Workflow Contracts

### 2.1 The Problem

`WorkflowStepKind` + `requiredInputs: [String!]` types *names*, not *values*: no compile-time wiring check; catalog search is lexical; adding a modality requires code deploy; no way to express typed capability requirements.

### 2.2 Step Contract Data Model

```graphql
scalar TypeRefUri scalar SchemaRef scalar Sha256 scalar SemVer scalar Duration8601 scalar Money scalar JsonPointer

enum BaseKind { TEXT INT DECIMAL BOOL DURATION MONEY URI TIMESTAMP IMAGE AUDIO MESH VECTOR EMBEDDING JSON_DOC ARTIFACT_REF MODEL_HANDLE CREDENTIAL_HANDLE TOOL_SPEC SCHEMA_REF }
enum Cardinality { EXACTLY_ONE ZERO_OR_ONE ONE_OR_MORE ZERO_OR_MORE }
enum DeterminismClass { PURE IDEMPOTENT RETRYABLE_SIDE_EFFECT NON_DETERMINISTIC }
enum Effect { MODEL_INVOKE NETWORK_EGRESS FILE_WRITE DB_WRITE SPEND PII_ACCESS SECRET_READ HUMAN_NOTIFY WORKFLOW_REGISTER }

type NominalType {
  uri: TypeRefUri! version: SemVer! baseKind: BaseKind!
  assignableTo: [TypeRefUri!]! schemaRef: SchemaRef constraints: [Constraint!]!
}

union Constraint = RangeConstraint | RegexConstraint | EnumOfConstraint | MaxItemsConstraint | UnitOfMeasureConstraint | JsonSchemaConformanceConstraint | ChecksumRequiredConstraint

type Port {
  name: PortName! typeRef: TypeRefUri! cardinality: Cardinality! nullable: Boolean!
  constraints: [Constraint!]! schemaRef: SchemaRef default: TypedLiteral
  provenance: ProvenancePolicy!   # REQUIRED | DERIVABLE | OPTIONAL
}

type StepContract {
  contractId: Sha256!              # sha256(canonical_json(signature_only))
  version: SemVer! stepTypeUri: URI!
  displayKind: String              # @deprecated — excluded from contractId and all indexes
  inputs: [Port!]! outputs: [Port!]! effects: [Effect!]! determinism: DeterminismClass!
  preconditions: [Predicate!]! postconditions: [Predicate!]!
  costModel: StepCostModel! timeout: Duration8601! retryPolicy: RetryPolicy!
  requiredModelCapability: CapabilityQuery!  # typed predicate, not a model name
  supersedes: Sha256
}

type Edge {
  fromStep: StepIndex! fromPort: PortName! toStep: StepIndex! toPort: PortName!
  coercionUri: TypeRefUri           # must point at a registered conversion contract
}

type WorkflowDefinition {
  workflowId: Sha256! version: SemVer! status: WorkflowStatus!
  inputSignature: [Port!]! outputSignature: [Port!]! steps: [StepInstance!]! edges: [Edge!]!
  declaredEffects: [Effect!]! budgetCeiling: Money! dataResidency: [RegionCode!]!
  approvalRef: ProposalId registeredBy: PrincipalId!
}
```

**`requiredModelCapability: CapabilityQuery`** — a step declares *what it needs*; the router binds a concrete `model_key` at execution time. Workflows stop being hard-wired to one vendor.

### 2.3 Compile-Time Wiring Rules

Eight blocking rules, each emitting typed error `{rule, edge, expected, actual, suggestion}`: SUBTYPE, CARDINALITY, NULLABILITY, UNIT, SCHEMA_SUBSET, EFFECT_DECLARED, BUDGET_FEASIBLE, DAG_AND_BINDING.

**Coercions** are never implicit. `Edge.coercionUri` must point at a registered conversion contract (`audio/resample@1`, `mesh/convert-glb-to-obj@1`) with its own cost model and effects.

### 2.4 Catalog Indexing

- **Primary:** `(outputSignatureHash)` → candidate steps
- **Secondary:** `(inputSignatureHash)`
- **Tertiary:** `(effectSet, determinismClass)`
- **Quaternary:** `requiredModelCapability` predicate

`displayKind` excluded from every hash and index. Renaming `TRANSCRIBE_AUDIO` → `ASR` is a no-op; changing a port's type requires re-approval.

### 2.5 Polymorphic Payloads

Registry union types with compiler-inserted exhaustive match: `artifact/media@2 = oneOf{ImageArtifact, AudioArtifact, MeshArtifact, TextArtifact}`. Every branch covered or terminating in typed `UnsupportedVariant{variant, handler: FAIL|SKIP|ROUTE_TO_FALLBACK}`.

### 2.6 Migration from String-List Contracts (6 phases, zero-downtime)

| Phase | Action | Exit criterion | Rollback |
|---|---|---|---|
| P0 Freeze | `@deprecated` legacy; lint blocks new string-list | CI active | Disable rule |
| P1 Infer | `InferredPort{name, type_ref, confidence, evidence}` | ≥90% ports inferred | None (read-only) |
| P2 Sign-off | Human review where confidence < 0.95 | 100% typed & signed | Revert to P1 |
| P3 Shadow | Dual validation; typed violations warn-only | 14 days clean | Flag off |
| P4 Cut over | Rewrite edges; recompute contractId; write supersedes | All on V2 | V1 IDs resolvable forever |
| P5 Remove | Delete `requiredInputs` next major version | No readers | Restore flag |

---

## §3 "No Matching Workflow Exists" Protocol

### 3.1 Deterministic State Machine

```
RECEIVED → CLASSIFY (pre-condition) → MATCH (pre-condition)
MATCH outcome: MATCHED | NO_MATCH | AMBIGUOUS

NO_MATCH → S1 DRAFT → S2 VALIDATE → S3 GOVERN → S4 REGISTER → S5 EXECUTE → S6 PROMOTE
            ↑              │
            └── repair ≤3 ─┘
```

### 3.2 Match Determination — Gates Then Rank

**Gates (binary, all must pass):**
1. `typeConformance = 1.0`
2. `effectSubset` — `workflow.effects ⊆ task.effects_allowed`
3. `budgetFeasible` — `cost_upper_bound ≤ task.budget_ceiling`
4. `residencyCompatible` — `workflow.data_residency ⊆ task.data_residency`
5. `status ∈ {STABLE, CANDIDATE(if override)}`

**Rank (after gates pass):**
```
score = 0.40·specificity + 0.30·historicalSuccessRate + 0.20·costEfficiency + 0.10·latencyFit
```

**Verdict:** MATCH iff unique top with `top − second > ε(0.02)`; AMBIGUOUS iff within ε → escalate; NO_MATCH iff zero gated candidates or `top < θ(0.85)`. Thresholds in versioned policy config.

### 3.3 Stage S1: DRAFT

**Entry:** `MatchDecision{verdict: NO_MATCH, gates_failed, θ, candidates_considered}`

**Method:** Backward chaining from `desired_output_type_ref` over StepContract catalog + `TypeRegistry.assignableTo`. Model binding by CapabilityQuery constraint solve, never by name.

**Bounds:** `max_depth=6`, `max_branching=8`, `max_candidates=64`, `wall_timeout=10s`. [inference]

**Exit:** `WorkflowDraft{steps, edges, ports, effects, cost_interval, model_bindings, residency}`

**Artifacts:** `DraftRecord{draft_hash, search_trace, primitives_used:[ContractId], gaps:[MissingPrimitive{from_type, to_type}]}`

**Failures:** `InfeasibleTask` (terminal); `MissingPrimitive` → adapter-build request; `SearchTimeout` → retry once at depth 4 then terminal.

### 3.4 Stage S2: VALIDATE

**Static:** 8 wiring rules; literal↔schema conformance; credential-ref resolvability (existence, not secret read); model-access grant present; endpoints ∈ egress allowlist; Σ cost.upper ≤ budget; residency intersection non-empty.

**Dry-run:** `DryRunContext{stub_artifacts: true, spend_cap: Money(0), network: SHADOW, pii: REDACTED, fixtures}`.

**Exit:** `ValidationReport{verdict, failures:[TypedFailure], cost_interval, dry_run_trace, coverage}`

**Bounded repair loop:** ≤3 iterations, fixed mutation order: 1 retype port, 2 insert coercion step, 3 swap model binding, 4 widen cardinality, 5 split step. Exhausted ⇒ InfeasibleTask.

### 3.5 Stage S3: GOVERN

**Entry:** `ValidationReport{verdict: PASS}`

**Risk classification rule table (deterministic, never model-inferred):**

| Condition | Risk Class |
|---|---|
| PII_ACCESS ∧ pii_scope == FULL | CRITICAL |
| SECRET_READ ∧ new egress domain | CRITICAL |
| spend.upper > policy.high_spend_threshold | HIGH |
| NON_DETERMINISTIC step with side effects | HIGH |
| EXPERIMENTAL model in binding | HIGH |
| New model or new provider | MEDIUM |
| New egress domain | MEDIUM |
| Otherwise | LOW |

**Auto-approve predicate (strictest union of DeepSeek ∪ Qwen):**
```
risk_class == LOW ∧ cost_estimate.upper ≤ per_run_auto_ceiling
  ∧ effects ⊆ {MODEL_INVOKE, HUMAN_NOTIFY} ∧ PII_ACCESS ∉ effects
  ∧ no NEW_MODEL ∧ no new egress domain
  ∧ all endpoints ∈ preapproved_egress ∧ no EXPERIMENTAL model
```

**Exit:** `ApprovalDecision{decision, decided_by, decided_at, conditions, rationale, audit_hash}`. TTL 24h → EXPIRED. CONDITIONS re-validated in S2.

### 3.6 Stage S4: REGISTER

```graphql
type CatalogRegistration {
  contractId: Sha256! version: SemVer! status: CANDIDATE
  contentHash: Sha256! supersedes: Sha256 registeredBy: PrincipalId!
  approvalRef: ProposalId! conditions: [Condition]
  immutableFrom: DateTime! catalogZone: CANDIDATE
}
```

Idempotency: identical `content_hash` ⇒ `already_registered: true`. Concurrent write ⇒ optimistic lock. Append-only; signature change ⇒ new version + supersedes + fresh S3.

### 3.7 Stage S5: EXECUTE

Per-step spans: `{run_id, step_index, contract_id, model_key, tokens{in,out,cached,reasoning}, cost_actual: Money, latency_ms, retry_count, artifact_checksums, determinism_class, approval_ref}`.

Enforcement: budget HARD_STOP; checksum verify on every artifact; NON_DETERMINISTIC steps never auto-retried without idempotency key; SECRET_READ args redacted; runtime residency violation → immediate HARD_STOP + CRITICAL incident.

**Exit:** `ExecutionRecord{verdict, artifacts, cost_actual_total, incident_refs}`

### 3.8 Stage S6: PROMOTE

**Criteria:** `min_successful_runs ≥ 5 ∧ success_rate ≥ 0.95 ∧ p95_latency ≤ slo ∧ |cost_actual - cost_estimate|/cost_estimate ≤ 0.25 ∧ zero_high_severity_incidents ∧ reviewer_sign_offs ≥ 1` → STABLE.

**Symmetric demotion:** STABLE → QUARANTINED on `success_rate < 0.80` over 20 runs or any CRITICAL incident.

### 3.9 Kernel Invariants

1. No execution without registration; no registration without approval; no approval without validation PASS; no validation without a compiling draft.
2. Every transition writes an append-only hash-chained audit event.
3. LLM may propose but never authorizes — all gates, type checks, and risk classification are deterministic code over typed data.
4. Every spend event carries `(workflow_run_id, step_index, model_key, contract_id)`.
5. Every artifact carries a typed kind and a checksum.
6. All searches and repair loops are bounded; unbounded recursion is a kernel error, not a hang.

---

## §4 Synthesis

### Unanimous Findings (all three lenses)
Seven primitive categories; zero untyped kwargs/string bags; sealed discriminated unions; `Money = Decimal(scale=6) + ISO 4217`; vault/STS credentials only; nominal versioned types with explicit `assignableTo`; signature-hash indexing with `displayKind` excluded; gates-then-rank matching (θ=0.85, ε=0.02); hash-chained audit; five pipeline stages + PROMOTE; 7-variant reasoning union; five anti-bag lint rules; `effective_max_output_at_full_context`; blocking `iff`-invariant; `requiredModelCapability: CapabilityQuery`; `MissingPrimitive` gap reports; "LLM may propose but never authorize."

### Resolved Divergences
Stage count (6 adopted), migration phases (6 adopted), match scoring (gates-then-rank + Qwen weights), repair loop (5-step), auto-approve (strictest union), coercion model (Qwen's strongest), reasoning spec_uri (mandatory), minimality (all three justifications).

### Net Architectural Effect
The fallback pipeline is the catalog's **growth mechanism**. Every unmatched task yields either a validated, approved, typed workflow or a precise `MissingPrimitive` adapter-build request. Neither outcome is a free-text apology. The system grows deterministically with every unmatched task, and `MissingPrimitive` reports form a prioritized backlog for the platform team.

---

## §5 Citations

### Vendor API Documentation
- **OpenAI** — platform.openai.com/docs (Models, Chat Completions, Responses, Structured Outputs, Batch, prompt caching, rate-limit tiers)
- **Anthropic** — docs.anthropic.com (Messages API, thinking, redacted_thinking, interleaved thinking, count_tokens, prompt caching)
- **Google** — ai.google.dev, cloud.google.com/vertex-ai (Gemini thinkingConfig, responseMimeType/responseSchema, context caching)
- **AWS Bedrock** — docs.aws.amazon.com/bedrock (Converse/InvokeModel, reasoningConfig, SigV4, per-region model access)
- **DeepSeek** — api-docs.deepseek.com (thinking bool, reasoning_content field)
- **Alibaba DashScope / Qwen** — help.aliyun.com (enable_thinking, Qwen-VL/Qwen-Audio)
- **Replicate** — replicate.com/docs (Predictions API, webhook+poll, output_schema)
- **fal.ai** — docs.fal.ai (Queue API, webhook signing)
- **Hugging Face** — huggingface.co/docs (Inference Providers)

### Standards
- **MLflow** — mlflow.org/docs (ModelSignature/Schema — prior art for typed model I/O)
- **JSON Schema 2020-12**, **OpenAPI 3.1**, **GraphQL Specification**
- **RFC 9457, RFC 8941, RFC 6750, RFC 3339 / ISO 8601, ISO 4217, ISO 3166-1 alpha-2, BCP 47, glTF 2.0**

### Workflow Engines
- **Temporal** — docs.temporal.io (determinism constraints, RetryPolicy, activity idempotency)
- **Argo Workflows** — (negative example: string-typed parameters)
- **Apache Airflow** — (negative example: untyped XCom)
- **LangGraph** — (positive example: typed StateGraph)

### Security & Governance
- **HashiCorp Vault** — developer.hashicorp.com/vault (dynamic secrets, rotation, dual-key overlap)
- **OWASP** — Secrets Management Cheat Sheet
- **NIST AI RMF 1.0**, **EU AI Act** (risk tiering)

### Marked [inference]
`effective_max_output_at_full_context`; θ=0.85 / ε=0.02; repair-loop mutation order; promotion thresholds (5 runs / 0.95 / 0.25); draft-search bounds (depth 6 / branching 8 / candidates 64 / 10s).

---

## §6 Summary

Three independent model lenses (Tencent HY3, DeepSeek v4 Pro, Qwen 3.8 Max) analyzed the core primitives of model onboarding and typed workflow mechanics. Near-total convergence on architecture, typing discipline, and invariants. Minor divergences resolved by adopting the most comprehensive formulation.

**Key deliverables:**
1. **§1 Primitives:** 7 categories, ~90 typed fields, all concrete. Zero string bags. Five blocking anti-bag lint rules. Derived telemetry in separate ObservationsStore.
2. **§2 Typed Contracts:** `StepContract` with typed `Port` inputs/outputs replaces `WorkflowStepKind` + `requiredInputs:[String!]`. Nominal versioned TypeRegistry. Eight blocking compile-time wiring rules. Signature-hash indexing. Six-phase zero-downtime migration.
3. **§3 No-Match Protocol:** Gates-then-rank matching (θ=0.85, ε=0.02). Six-stage deterministic pipeline. Bounded repair loop (≤3 iterations, 5-step mutation order). Hash-chained audit. Six kernel invariants. LLM may propose but never authorize.

**Net effect:** The fallback pipeline is the catalog's growth mechanism. Every unmatched task yields either a validated, approved, typed workflow or a precise `MissingPrimitive` adapter-build request. Neither outcome is a free-text apology.