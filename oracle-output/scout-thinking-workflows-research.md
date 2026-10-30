# Thinking/Effort Modeling and Workflow/Connector Discovery in Large Agentic Codebases

**Research Report — 2026-09-08**

---

## Executive Summary

1. **"Thinking," "effort," and "token budget" are three distinct domain concepts across AI providers, not interchangeable terms.** Thinking is the model's internal chain-of-thought capability. Effort is a calibrated caller-side steering signal that controls intensity. Token budget is a legacy mechanical resource constraint. This taxonomy is the report's analytical framework, not provider-endorsed terminology. **(High confidence — grounded in primary API documentation from four independent providers [[1]](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking) [[2]](https://docs.anthropic.com/en/docs/build-with-claude/thinking-steering-and-cost) [[3]](https://docs.anthropic.com/en/docs/build-with-claude/effort) [[4]](https://platform.openai.com/docs/guides/reasoning) [[5]](https://ai.google.dev/gemini-api/docs/thinking) [[6]](https://api-docs.deepseek.com/guides/thinking_mode) [[7]](https://api-docs.deepseek.com/))**

2. **Only Anthropic cleanly separates all three layers into distinct API surfaces.** OpenAI, Google, and DeepSeek each combine or omit layers to varying degrees. Anthropic's `budget_tokens` is deprecated on Claude 4.6+ and rejected on 4.7+. It was always a target, not a hard cap. **(High confidence — Anthropic's own docs [[1]](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking) [[3]](https://docs.anthropic.com/en/docs/build-with-claude/effort))**

3. **No major agent framework models thinking or effort as a first-class abstraction.** LangChain, DSPy, LlamaIndex, AutoGen, CrewAI, Haystack, and Semantic Kernel all rely on provider-level parameter pass-through at model initialization. CrewAI's `reasoning` boolean is agent-level planning, not a pass-through to provider thinking APIs. It is a distinct concept that shares a name. **(High confidence for DSPy [[20]](https://dspy.ai/api/models/LM) [[21]](https://dspy.ai/api/models/BaseLM) [[22]](https://dspy.ai/community/normalized-lm-api-migration), AutoGen [[24]](https://microsoft.github.io/autogen/stable/reference/python/autogen_core.models.html), and Semantic Kernel [[23]](https://learn.microsoft.com/en-us/semantic-kernel/concepts/ai-services/chat-completion) based on API references; medium confidence for LangChain [[9]](https://docs.langchain.com/oss/python/langchain/agents), LlamaIndex [[11]](https://docs.llamaindex.ai/en/stable/module_guides/models/llms/), CrewAI [[17]](https://docs.crewai.com/concepts/agents), and Haystack [[18]](https://docs.haystack.deepset.ai/docs/agents) based on guide/tutorial pages rather than exhaustive API references.)**

4. **No major framework uses passive, prompt-based skill discovery as its primary tool onboarding mechanism.** All frameworks rely on explicit registration, declarative configuration, or queryable registries. Haystack's `SearchableToolset` is the only framework with a built-in, queryable tool discovery mechanism for large catalogs. This finding is qualified by the scope of documentation examined. MCP is widely supported across frameworks as a cross-framework discovery protocol. **(Medium-high confidence — positive evidence from framework docs [[8]](https://docs.langchain.com/oss/python/langchain/models) [[13]](https://docs.llamaindex.ai/en/stable/understanding/agent/tools/) [[17]](https://docs.crewai.com/concepts/agents) [[18]](https://docs.haystack.deepset.ai/docs/agents); the uniqueness claim rests on absence from other frameworks' examined docs and may not capture their full feature surfaces.)**

5. **For large agentic codebases with 100+ integrations, the architectural pattern emerging from framework designs combines MCP for discovery, searchable/queryable registries for relevance filtering, explicit registration as a safety net, and high-quality tool metadata.** This recommendation is the report's synthesis of observed patterns across frameworks, not an industry consensus. **(Medium confidence — synthesized from individual framework documentation [[8]](https://docs.langchain.com/oss/python/langchain/models) [[13]](https://docs.llamaindex.ai/en/stable/understanding/agent/tools/) [[17]](https://docs.crewai.com/concepts/agents) [[18]](https://docs.haystack.deepset.ai/docs/agents), not from any source that explicitly endorses this combination.)**

---

## Background and Context

As agentic codebases grow to encompass dozens or hundreds of tools, connectors, and model integrations, two architectural questions become critical. First: how to control the reasoning depth of the underlying LLM. Second: how to reliably onboard new capabilities without the agent hallucinating tool names or behaviors. This report examines both questions across four major AI providers (Anthropic, OpenAI, Google, DeepSeek) and seven major agent frameworks (LangChain, DSPy, LlamaIndex, AutoGen, CrewAI, Haystack, Semantic Kernel).

Web search was unavailable throughout this research run. All findings derive from direct inspection of official documentation and API references fetched during the investigation.

---

## Part 1: Thinking vs. Effort vs. Token Budget

### 1.1 Three Distinct Concepts — A Proposed Taxonomy

Across all four providers, three distinct configuration surfaces emerge. This taxonomy is the report's own analytical framework. No provider uses these exact categories.

| Layer | Definition | Control surface |
|-------|-----------|-----------------|
| **Thinking** | The model's internal chain-of-thought capability — whether it reasons step-by-step before answering | On/off toggle, or intrinsic to model family |
| **Effort** | A calibrated caller-side steering signal controlling how hard the model tries — depth, thoroughness, iteration count | Continuous or discrete scale (low/medium/high) |
| **Token budget** | A mechanical resource constraint on the reasoning process | Raw token count target or cap |

### 1.2 Provider-by-Provider Analysis

#### Anthropic — Full Three-Layer Separation

Anthropic is the only provider that cleanly separates all three layers into distinct API surfaces [[1]](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking) [[2]](https://docs.anthropic.com/en/docs/build-with-claude/thinking-steering-and-cost) [[3]](https://docs.anthropic.com/en/docs/build-with-claude/effort):

- **Thinking toggle** (`thinking.type`): `"enabled"`, `"adaptive"`, or `"disabled"`. In adaptive mode, Claude automatically decides when to think, including between tool calls (interleaved thinking). No beta header is required for this behavior [[1]](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking).

- **Effort control** (`output_config.effort`): `"low"`, `"medium"`, `"high"`, `"xhigh"`, `"max"`. According to Anthropic's documentation, this applies to all output types — text responses, tool calls, and thinking — not just thinking. Lower effort is claimed to produce fewer and terser tool calls even when thinking is disabled. These claims rest on Anthropic's own vendor documentation [[3]](https://docs.anthropic.com/en/docs/build-with-claude/effort). Independent benchmarking to confirm these behavioral effects was not found during this research.

- **Steering** (system prompt + per-message steering): System-level instructions guide Claude's thinking approach broadly. A beta per-message steering mechanism allows appending guidance (for example, "Please think hard before responding") to individual user messages without changing configuration or invalidating prompt cache. This matters because changing `effort` between requests does invalidate Anthropic prompt cache breakpoints [[2]](https://docs.anthropic.com/en/docs/build-with-claude/thinking-steering-and-cost).

- **Token budget** (`thinking.budget_tokens`): This was a target, not a strict cap. `max_tokens` remained the hard ceiling. It is deprecated on Claude 4.6+ and rejected with a 400 error on Claude 4.7+ [[1]](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking).

- **Display control** (`thinking.display`): Controls how thinking content is returned. Options: `"summarized"` (readable summaries), `"omitted"` (empty thinking field with signature only, for faster time-to-first-text-token when streaming), `"updates"` (beta, progress updates between tool calls). Thinking tokens are billed as output tokens even when display is `"omitted"`, and they count toward `max_tokens` [[1]](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking).

#### OpenAI — Toggle and Intensity Combined

OpenAI's reasoning models (astra/terra/luna families) use a single `reasoning.effort` parameter that serves as both toggle and intensity control. The documentation shows `reasoning.effort` as the sole configuration surface for reasoning behavior [[4]](https://platform.openai.com/docs/guides/reasoning). Whether this represents intentional design simplification or conflation of concerns is a matter of interpretation. The report characterizes it as "conflation" for analytical contrast with Anthropic's approach. OpenAI's own documentation does not frame it this way.

Reasoning models always reason. There is no separate on/off toggle. Internal reasoning tokens are consumed before producing a response, helping the model plan, use tools effectively, inspect alternatives, recover from ambiguity, and solve harder multi-step tasks [[4]](https://platform.openai.com/docs/guides/reasoning).

#### Google — Thinking as Intrinsic Model Behavior

Google's Gemini 3 and 2.5 series treat thinking as an intrinsic model behavior [[5]](https://ai.google.dev/gemini-api/docs/thinking). The `thinkingLevel` parameter controls reasoning depth. The documentation examined does not describe a separate on/off toggle. **This is an inference from absence.** The Gemini Thinking guide describes `thinkingLevel` and `thinking_summaries` but does not explicitly state that thinking cannot be disabled. A full Gemini API reference or model card might reveal a disable option not mentioned in the Thinking guide.

Every thought step carries a cryptographic signature. The `thinking_summaries` parameter controls whether thought summaries are returned: `"auto"`, `"none"`, or `"enabled"`. Summaries can be streamed via SSE with `thought_summary` and `thought_signature` delta types [[5]](https://ai.google.dev/gemini-api/docs/thinking).

#### DeepSeek — Toggle Plus Collapsed Effort Scale

DeepSeek offers both a binary toggle (`thinking.type`: `"enabled"` / `"disabled"`) and an effort control (`reasoning_effort`: `"low"`, `"high"`, `"max"`). The documentation indicates that `"medium"` and `"xhigh"` effort levels are internally collapsed to `"high"`, yielding effectively three real effort levels [[6]](https://api-docs.deepseek.com/guides/thinking_mode) [[7]](https://api-docs.deepseek.com/). **The confidence on this collapsing behavior depends on whether the documentation explicitly describes it or whether the report infers it from the effort scale documentation.** If described explicitly, confidence is high. If inferred, confidence is medium.

DeepSeek's `reasoning_content` must be passed back in subsequent requests when using tools. It is ignored (not concatenated into context) when not using tools [[6]](https://api-docs.deepseek.com/guides/thinking_mode).

DeepSeek supports dual API format compatibility: OpenAI-compatible format (`thinking.type` + `reasoning_effort`) and Anthropic-compatible format (`reasoning.effort` + `output_config.effort`). The documentation was found to demonstrate both formats [[6]](https://api-docs.deepseek.com/guides/thinking_mode) [[7]](https://api-docs.deepseek.com/).

### 1.3 Cross-Provider Comparison

| Dimension | Anthropic | OpenAI (reasoning) | Google (Gemini) | DeepSeek |
|-----------|-----------|-------------------|-----------------|----------|
| On/off toggle | `thinking.type` | None (always on) | None found in docs examined | `thinking.type` |
| Intensity control | `output_config.effort` (5 levels) | `reasoning.effort` (combined) | `thinkingLevel` | `reasoning_effort` (3 effective levels) |
| Token budget | Deprecated (4.6+) | Not exposed | Not documented separately | Not documented separately |
| Steering | System prompt + per-message (beta) | Not documented separately | Not documented separately | Not documented separately |
| Output visibility | `display`: summarized/omitted/updates | Not documented | `thinking_summaries` | `reasoning_content` (must echo back for tools) |
| Billing for hidden thoughts | Yes (output tokens) | Not documented | Not documented | Not documented |
| Cryptographic signatures | No | No | Yes (every thought step) | No |

### 1.4 The Framework Abstraction Gap

No major agent framework models thinking or effort as a first-class abstraction. All seven frameworks examined — LangChain, DSPy, LlamaIndex, AutoGen, CrewAI, Haystack, and Semantic Kernel — rely on provider-level parameter pass-through at the model initialization level. Evidence quality varies:

- **High confidence (API references examined):** DSPy's `dspy.LM` and `dspy.BaseLM` constructors have no thinking, effort, or reasoning parameters [[20]](https://dspy.ai/api/models/LM) [[21]](https://dspy.ai/api/models/BaseLM). The typed `LMRequest`/`LMResponse` boundary in the normalized LM API migration also excludes thinking/effort fields [[22]](https://dspy.ai/community/normalized-lm-api-migration). AutoGen's `ChatCompletionClient.create()` method signature has no thinking, effort, or reasoning parameter. `AssistantMessage.thought` captures reasoning output where available. `extra_create_args` could theoretically pass through provider-specific parameters, but this is not documented as a supported pattern [[24]](https://microsoft.github.io/autogen/stable/reference/python/autogen_core.models.html). Semantic Kernel's chat completion connector configuration for all major providers shows no thinking, effort, reasoning, or `thinkingBudget` parameters in any service configuration example [[23]](https://learn.microsoft.com/en-us/semantic-kernel/concepts/ai-services/chat-completion).

- **Medium confidence (guide/tutorial pages, not exhaustive API docs):** LangChain's `create_agent` uses `model=`, `tools=`, and `system_prompt=` as core parameters with no thinking/effort parameter visible [[9]](https://docs.langchain.com/oss/python/langchain/agents). LlamaIndex's agent construction uses tools but no thinking parameter at the framework abstraction level [[11]](https://docs.llamaindex.ai/en/stable/module_guides/models/llms/) [[12]](https://docs.llamaindex.ai/en/stable/understanding/agent/). Haystack's agent documentation shows no thinking/effort parameters [[18]](https://docs.haystack.deepset.ai/docs/agents). **These are guide pages, not comprehensive API references. A thinking/effort parameter could exist in the full API surface but not be mentioned in the getting-started guide.**

#### CrewAI's `reasoning` — A Different Concept

CrewAI's `reasoning` boolean attribute on agents is agent-level planning behavior. The agent reflects and creates a plan before executing tasks. It is NOT a pass-through to provider-level thinking APIs [[17]](https://docs.crewai.com/concepts/agents). This is a critical terminological distinction: the same word names completely different mechanisms at different layers of the stack.

---

## Part 2: Workflow and Connector Discovery

### 2.1 The Discovery Problem

When an agent faces a catalog of 100+ tools, it cannot have all tool descriptions in its context window simultaneously. The challenge: how does the agent discover which tools are available and relevant without hallucinating tool names, inventing API calls, or wasting context on irrelevant tools?

### 2.2 Framework-by-Framework Analysis

#### LangChain

LangChain's agent framework uses explicit tool binding at agent creation time: `create_agent(model=..., tools=..., system_prompt=...)` [[9]](https://docs.langchain.com/oss/python/langchain/agents). Tools are passed as a concrete list to the agent constructor. MCP (Model Context Protocol) is supported for cross-framework tool discovery [[8]](https://docs.langchain.com/oss/python/langchain/models). The documentation examined does not describe a built-in queryable or searchable tool registry. This finding is qualified by the scope of documentation examined (guide/tutorial pages rather than comprehensive feature lists).

#### LlamaIndex

LlamaIndex provides `LlamaHub`, a centralized registry of community-contributed tools and data connectors described as functioning like a queryable tool marketplace [[13]](https://docs.llamaindex.ai/en/stable/understanding/agent/tools/). The platform documents MCP support for tool integration. Tool registration is explicit. Multi-agent patterns are supported: agents can be composed into larger workflows with tool distribution across agents [[14]](https://docs.llamaindex.ai/en/stable/understanding/agent/multi_agent/).

#### AutoGen

AutoGen distributes tools across agents in teams using multiple team patterns: `RoundRobinGroupChat`, `SelectorGroupChat` (LLM-based speaker selection), `MagenticOneGroupChat` (generalist multi-agent), and `Swarm` (handoff-based) [[16]](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html). Tool assignment is explicit. Each agent in a team declares its tools at construction time. The documentation examined does not describe a queryable tool discovery mechanism within AutoGen's own abstraction layer.

#### CrewAI

CrewAI uses JSONC/YAML declarative configuration where tools are referenced by name and resolved at runtime [[17]](https://docs.crewai.com/concepts/agents). MCP servers are supported as tools across multiple transports: stdio, SSE, and streamable HTTP. Tool assignment is explicit and declarative. The documentation examined does not describe passive or auto-discovery mechanisms as a primary approach.

#### Haystack

Haystack provides the most structured tool ecosystem among the examined frameworks, with multiple tool creation patterns [[18]](https://docs.haystack.deepset.ai/docs/agents):

- `@tool` decorator — for wrapping Python functions
- `Tool` class — explicit tool construction
- `ComponentTool` — wraps any Haystack component as a tool
- `PipelineTool` — wraps a full pipeline as a tool
- `AgentTool` — wraps an Agent for delegation

Haystack's `SearchableToolset` is the only framework mechanism found in this investigation that provides built-in, queryable tool discovery for large catalogs. It uses keyword-based filtering so the LLM only sees contextually relevant tools at each step, reducing both hallucination risk and context waste. **This uniqueness claim is qualified.** It rests on comprehensive Haystack documentation [[18]](https://docs.haystack.deepset.ai/docs/agents) and absence from other frameworks' examined docs [[8]](https://docs.langchain.com/oss/python/langchain/models) [[9]](https://docs.langchain.com/oss/python/langchain/agents) [[13]](https://docs.llamaindex.ai/en/stable/understanding/agent/tools/) [[15]](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/models.html) [[17]](https://docs.crewai.com/concepts/agents). LangChain or other frameworks could have equivalent mechanisms not covered in the pages examined.

#### Semantic Kernel

Semantic Kernel uses a plugin-based architecture with OpenAPI specifications for sharing extensions. It describes itself as "middleware translating the model's request to a function call" [[19]](https://learn.microsoft.com/en-us/semantic-kernel/overview/). Only the overview page was available for examination. Detailed plugin discovery mechanism documentation was not found during this research.

### 2.3 MCP — Cross-Framework Discovery Protocol

MCP (Model Context Protocol) is supported by LangChain [[8]](https://docs.langchain.com/oss/python/langchain/models), LlamaIndex [[13]](https://docs.llamaindex.ai/en/stable/understanding/agent/tools/), CrewAI [[17]](https://docs.crewai.com/concepts/agents), and Haystack [[18]](https://docs.haystack.deepset.ai/docs/agents). The report characterizes MCP as an "emerging industry standard for cross-framework tool discovery." **This characterization is a judgment call.** The documentation shows MCP support exists across four frameworks, but does not quantify adoption rates, does not demonstrate that MCP is the primary discovery mechanism within any framework, and does not compare it to alternatives. The label "emerging industry standard" implies adoption breadth and trajectory that these documentation mentions alone do not fully establish.

### 2.4 Efficacy of Discovery Approaches

#### Passive/Prompt-Based Discovery

No framework examined relies on passive, prompt-based skill discovery as its primary tool onboarding mechanism. All frameworks use explicit registration, declarative configuration, or queryable registries [[8]](https://docs.langchain.com/oss/python/langchain/models) [[9]](https://docs.langchain.com/oss/python/langchain/agents) [[13]](https://docs.llamaindex.ai/en/stable/understanding/agent/tools/) [[15]](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/models.html) [[17]](https://docs.crewai.com/concepts/agents) [[18]](https://docs.haystack.deepset.ai/docs/agents).

The report states that passive/prompt-based tool discovery is not recommended for production systems due to high hallucination risk. **This is the report's own inference, not an explicit recommendation from any source.** The evidence shows no framework implements passive discovery as a primary mechanism, but absence of implementation does not equal an explicit recommendation against it. The hallucination risk reasoning is the report's analysis, not sourced from framework documentation.

#### Explicit Registration (Workflows-as-Code)

All frameworks support explicit tool registration as the baseline approach. In LangChain, tools are passed directly to `create_agent` [[9]](https://docs.langchain.com/oss/python/langchain/agents). In LlamaIndex, tools are registered at agent construction [[12]](https://docs.llamaindex.ai/en/stable/understanding/agent/). In AutoGen, tools are assigned to specific agents in teams [[16]](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html). In CrewAI, tools are declared in JSONC/YAML configuration [[17]](https://docs.crewai.com/concepts/agents). In Haystack, tools are created via decorators or classes and registered [[18]](https://docs.haystack.deepset.ai/docs/agents). This approach provides a safety net — only explicitly registered tools are available — but does not scale well to 100+ tool catalogs without supplemental discovery mechanisms.

#### Queryable Registries (Workflows-as-Data)

Haystack's `SearchableToolset` is the only framework mechanism found that provides built-in, queryable discovery. It uses keyword-based filtering so the LLM only sees contextually relevant tools at each step [[18]](https://docs.haystack.deepset.ai/docs/agents). LlamaHub provides a centralized registry but functions more as a marketplace for human developers than as a runtime discovery mechanism for agents [[13]](https://docs.llamaindex.ai/en/stable/understanding/agent/tools/).

#### MCP as Discovery Protocol

MCP provides a protocol-level mechanism for tool discovery that works across frameworks. It allows tools to be served from external processes with standardized discovery and invocation patterns. This shifts tool discovery from a framework concern to a protocol concern, enabling frameworks to treat any MCP server as a tool source without framework-specific integration code.

---

## Disagreements and Evidence Gaps

### Design Philosophy Differences (Not Contradictions)

No direct factual contradictions were found between primary sources. All 24 sources are official API/framework documentation, each describing its own system. The differences are architectural choices, not conflicting claims:

- **Anthropic** separates thinking toggle, effort, and steering into three distinct API surfaces [[1]](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking) [[2]](https://docs.anthropic.com/en/docs/build-with-claude/thinking-steering-and-cost) [[3]](https://docs.anthropic.com/en/docs/build-with-claude/effort)
- **OpenAI** combines toggle and intensity on reasoning-specific models [[4]](https://platform.openai.com/docs/guides/reasoning)
- **Google** treats thinking as intrinsic model behavior [[5]](https://ai.google.dev/gemini-api/docs/thinking)
- **DeepSeek** offers toggle + effort with a collapsed effort scale [[6]](https://api-docs.deepseek.com/guides/thinking_mode) [[7]](https://api-docs.deepseek.com/)

These are different design philosophies applied to the same domain problem, not contradictory factual claims about any single artifact.

### Terminological Overlap

CrewAI's `reasoning` boolean attribute is agent-level planning behavior, not provider-level thinking pass-through [[17]](https://docs.crewai.com/concepts/agents). This is a different concept sharing the same English word. The report must clearly distinguish these when discussing "reasoning" in agent frameworks vs. "reasoning" in model APIs.

### Evidence Gaps

1. **Semantic Kernel plugin discovery mechanisms:** Only the overview page was available [[19]](https://learn.microsoft.com/en-us/semantic-kernel/overview/). Detailed discovery documentation was not found.
2. **Web search was unavailable throughout this research run.** Some claims that rest on a single vendor source could not be corroborated through independent benchmarks or third-party analysis.
3. **Framework API completeness:** For LangChain, LlamaIndex, CrewAI, and Haystack, the documentation examined consisted of guide/tutorial pages rather than exhaustive API references. Thinking/effort parameters or queryable discovery mechanisms could exist in the full API surface but not be mentioned in the pages examined.

---

## Analysis and Implications

### The Thinking/Effort Abstraction Gap Is a Real Problem

The finding that no major agent framework models thinking or effort as a first-class abstraction has practical consequences:

1. **Portability is blocked.** A workflow tuned for Anthropic's five effort levels cannot be transparently migrated to OpenAI or DeepSeek without rewriting provider-specific configuration at the model initialization layer.

2. **Agent-level reasoning control is unavailable.** An agent orchestrator cannot dynamically adjust how hard a subtask should be reasoned about without plumbing provider-specific parameters through the framework's pass-through mechanism (for example, AutoGen's `extra_create_args` [[24]](https://microsoft.github.io/autogen/stable/reference/python/autogen_core.models.html)).

3. **No framework-level observability.** Thinking token consumption, effort levels, and reasoning content are captured at the provider API boundary but not surfaced as framework-level metrics, making cost attribution and debugging harder.

4. **The conceptual confusion between CrewAI's `reasoning` (agent planning) and Anthropic's `thinking.type: enabled` (model chain-of-thought) illustrates the risk.** The same word means different things at different layers. Frameworks that do not model the distinction force developers to understand provider-specific semantics directly.

### The Discovery Architecture Pattern

For large agentic codebases with 100+ integrations, the architectural pattern emerging from framework designs observed in this research combines four elements. **This recommendation is the report's synthesis of observed patterns, not an industry consensus.**

| Layer | Mechanism | Framework precedent |
|-------|-----------|-------------------|
| **Discovery protocol** | MCP — standardized tool server protocol | LangChain [[8]](https://docs.langchain.com/oss/python/langchain/models), LlamaIndex [[13]](https://docs.llamaindex.ai/en/stable/understanding/agent/tools/), CrewAI [[17]](https://docs.crewai.com/concepts/agents), Haystack [[18]](https://docs.haystack.deepset.ai/docs/agents) |
| **Queryable registry** | `SearchableToolset`-style keyword filtering — LLM sees only relevant tools | Haystack [[18]](https://docs.haystack.deepset.ai/docs/agents) |
| **Explicit registration** | Declarative tool-to-agent binding — safety net preventing hallucinated tools | All frameworks |
| **Rich metadata** | High-quality tool descriptions, parameter schemas, and usage examples | All frameworks |

The rationale: MCP provides the protocol layer for tool availability. A searchable registry filters large catalogs to contextually relevant subsets, preventing context window overflow and reducing hallucination risk. Explicit registration ensures that even if discovery fails, the agent cannot invoke tools it was never granted. Rich metadata improves the LLM's ability to select and use the right tool correctly.

---

## Open Questions

1. **Does Google Gemini actually have no thinking toggle, or is it simply not documented in the Thinking guide?** A full Gemini API reference or model card should be consulted to confirm this claim. Currently it is an inference from absence.

2. **Does DeepSeek's documentation explicitly state that medium and xhigh are collapsed to high, or is this inferred from the effort scale?** If inferred, independent testing would be needed to confirm.

3. **Do LangChain, LlamaIndex, CrewAI, or Haystack have thinking/effort parameters in their full API surfaces that are not documented in their getting-started guides?** Exhaustive API reference pages should be examined to confirm the negative claim.

4. **Do frameworks other than Haystack have queryable tool discovery mechanisms not mentioned in the pages examined?** A comprehensive feature search across each framework's full documentation would be needed for a definitive negative claim.

5. **What is Semantic Kernel's detailed plugin discovery architecture?** Only the overview page was examined. The full AI services and plugins documentation should be consulted.

6. **Can independent benchmarks confirm Anthropic's claim that lower effort produces fewer and terser tool calls?** This claim currently rests solely on Anthropic's own vendor documentation.

7. **What is the actual adoption rate of MCP across framework ecosystems?** The documentation shows support exists but does not quantify usage.

8. **Are there emerging standards for framework-level thinking/effort abstraction?** The current state shows none, but the gap is clear and may be addressed in future framework versions.

---

## References

1. "Extended thinking" — Anthropic Claude Platform Docs — https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking — accessed 2026-09-08 — credibility: high (primary source: official API documentation)
2. "Steering thinking" — Anthropic Claude Platform Docs — https://docs.anthropic.com/en/docs/build-with-claude/thinking-steering-and-cost — accessed 2026-09-08 — credibility: high (primary source: official API documentation)
3. "Effort" — Anthropic Claude Platform Docs — https://docs.anthropic.com/en/docs/build-with-claude/effort — accessed 2026-09-08 — credibility: high (primary source: official API documentation)
4. "Reasoning models" — OpenAI API Docs — https://platform.openai.com/docs/guides/reasoning — accessed 2026-09-08 — credibility: high (primary source: official API documentation)
5. "Gemini thinking" — Google AI for Developers — https://ai.google.dev/gemini-api/docs/thinking — accessed 2026-09-08 — credibility: high (primary source: official API documentation)
6. "Thinking Mode" — DeepSeek API Docs — https://api-docs.deepseek.com/guides/thinking_mode — accessed 2026-09-08 — credibility: high (primary source: official API documentation)
7. "Your First API Call" — DeepSeek API Docs — https://api-docs.deepseek.com/ — accessed 2026-09-08 — credibility: high (primary source: API examples)
8. "Models" — LangChain Docs — https://docs.langchain.com/oss/python/langchain/models — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
9. "Agents" — LangChain Docs — https://docs.langchain.com/oss/python/langchain/agents — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
10. "Modules: composing your own" — DSPy Docs — https://dspy.ai/diving-deeper/modules/ — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
11. "Using LLMs" — LlamaIndex Docs — https://docs.llamaindex.ai/en/stable/module_guides/models/llms/ — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
12. "Building an agent" — LlamaIndex Docs — https://docs.llamaindex.ai/en/stable/understanding/agent/ — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
13. "Using existing tools" — LlamaIndex Docs — https://docs.llamaindex.ai/en/stable/understanding/agent/tools/ — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
14. "Multi-agent patterns" — LlamaIndex Docs — https://docs.llamaindex.ai/en/stable/understanding/agent/multi_agent/ — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
15. "Models" — AutoGen Docs — https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/models.html — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
16. "Teams" — AutoGen Docs — https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
17. "Agents" — CrewAI Docs — https://docs.crewai.com/concepts/agents — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
18. "Agents" — Haystack Docs — https://docs.haystack.deepset.ai/docs/agents — accessed 2026-09-08 — credibility: high (primary source: official framework documentation)
19. "Introduction to Semantic Kernel" — Microsoft Learn — https://learn.microsoft.com/en-us/semantic-kernel/overview/ — accessed 2026-09-08 — credibility: high (primary source: official Microsoft documentation)
20. "LM" — DSPy API Reference — https://dspy.ai/api/models/LM — accessed 2026-09-08 — credibility: high (primary source: official DSPy API reference)
21. "BaseLM" — DSPy API Reference — https://dspy.ai/api/models/BaseLM — accessed 2026-09-08 — credibility: high (primary source: official DSPy API reference)
22. "Normalized LM API Migration" — DSPy Docs — https://dspy.ai/community/normalized-lm-api-migration — accessed 2026-09-08 — credibility: high (primary source: DSPy migration documentation)
23. "Add chat completion services to Semantic Kernel" — Microsoft Learn — https://learn.microsoft.com/en-us/semantic-kernel/concepts/ai-services/chat-completion — accessed 2026-09-08 — credibility: high (primary source: official Microsoft documentation)
24. "autogen_core.models" — AutoGen API Reference — https://microsoft.github.io/autogen/stable/reference/python/autogen_core.models.html — accessed 2026-09-08 — credibility: high (primary source: official AutoGen API reference)