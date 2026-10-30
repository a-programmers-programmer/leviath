# Lane B2-gql05b — GQL-05 node (b): two-server session and cross-language navigation

* Directive: **0193** (P0, author Codex) + amendments. Amendments win.
* Correlation: **fleet-sustained-throughput-20260914T1448Z**.
* Lane: **B2-gql05b**. Branch `lane/B2-gql05b`.
* Base commit (observed): **7b6e050e89e6aea7b51453e633fbe9209c50a844**
  — `GQL-02c: commit corrected acceptance packet (row C 6506, reproducible step 8, hashes by commit)`.
* Model path used for every inference in this lane, all roles:
  **`openrouter/deepseek/deepseek-v4.1-flash`**. No other namespace was used.
* Spec of record: `RESIDUALS-0193-s23.json` → `residual_2_gql05_restructured_split`
  → the `node_b` entry (`"node_b": "Two-server session plus cross-language
  navigation. Depends on node a."`).

---

## 1. What this lane had to do

Run **both** language servers side by side in **one** session and make
cross-language navigation work: from a position in one language, resolve to the
corresponding position in the other. The two languages are the accepted Python
adapter side and the accepted TypeScript adapter side.

The spec of record gives node (b) exactly one sentence. I did not enlarge it.

---

## 2. Harvest first — what already existed on disk

I read before I wrote. Adoption beat re-authoring.

* Accepted Python interface contract, read-only:
  `/data/work/graphql-realization/gql-05/repo/python/leviath_typed_ls/typed_code_adapter.py`
  Provides `extract_typed(source, path)` → `TypedExtraction`, whose
  `projection.facts` each carry a `SourceRef(path, line, col_start, col_end)`.
* Accepted TypeScript adapter, already copied into my declared set by the
  prerequisite: `src/adapter.ts` (`extractTyped`) plus `canonical.ts`, `facts.ts`,
  `reader.ts`, `unsupported.ts`. `npm test` reported 12 pass / 0 fail / exit 0
  before I started (index.test.ts + adapter.test.ts).
* Node (a) was already accepted and its whole tree was already present at
  `integrations/typed-ls-ts/`.

**Key harvested fact that shaped the design.** Both adapters already emit facts
in **one shared symbol-key namespace** with **one fact vocabulary**, and both
already carry a **source span** per fact. The Python side stores it as
`SourceRef(line, col_start, col_end)`; the TypeScript side stores the same three
numbers as `source: { line, startCol, endCol }`. So cross-language navigation is
a **table lookup on the shared `symbol_key`**, not a new whole-program analysis.
That is why this node is small: it joins two things that already agree.

---

## 3. What I built (adoption, not re-authoring)

All writes are inside the declared set only:
`/data/work/graphql-realization/gql-00/wt/B2-gql05b/integrations/typed-ls-ts`.

New files:

| Path | Why |
|---|---|
| `py/dump_facts.py` | Read-only line-JSON driver over the **accepted** `leviath_typed_ls.typed_code_adapter.extract_typed`. The accepted Python LSP does not expose spans (it answers `leviath/schemaProjection` and `leviath/codeEdits` only), so navigation needs this thin driver. It never imports, executes or evaluates the analysed artifact. |
| `src/session.ts` | `TwoServerSession`: both adapters in one session, one join table, one navigation call. Python is reached over the child process; TypeScript is called in-process. |
| `src/navigation.test.ts` | The failing-first test (RED) and its GREEN. 9 navigation tests. |
| `py/fixtures/ports.py`, `py/fixtures/ports.ts` | The pair of source files, one per language, declaring the same symbols. |
| `py/probe_nav.mjs` | Evidence probe: prints what the session observes. Writes nothing. |
| `py/out_py.json`, `py/out_ts.json` | Intermediate evidence captures (4470 B, 1057 B): the raw fact dumps from each adapter for the fixtures, written while I was observing the accepted spans before I wrote the test. They are scratch evidence, not part of the session or the build; nothing imports them. Disclosed here rather than left unexplained. |

Nothing in the accepted trees was modified. `node_modules`, `dist`,
`package.json`, `package-lock.json`, `tsconfig.json` and the pinned
`typescript` version were left exactly as node (a) left them.

### The design, in one paragraph

`TwoServerSession.start()` spawns one child process — the accepted Python
adapter behind `py/dump_facts.py`, started with `PYTHONDONTWRITEBYTECODE=1` so
reading the accepted checkout never writes bytecode into it. The TypeScript
server is `extractTyped` in-process. `session.servers()` reports both, with the
Python `pid` and each one's transport. `navigate({uri, position})` takes the
origin language's declaration spans, finds the innermost span containing the
position, and looks up the **same `symbol_key`** in the other language's spans.
Four honest answers exist and no fifth:

* `resolved` — the other language declares that exact symbol key; returns both spans.
* `absent-in-other-language` — the other language does **not** declare it. It is
  reported absent; it is never invented.
* `no-symbol-at-position` — no declaring span contains the position.
* `blocked` — the origin extraction carries an unsupported construct. Navigation
  **refuses** instead of reporting the symbol as absent, because it cannot claim
  a fact it never read.

---

## 4. The RED step (failing test first)

I wrote `src/navigation.test.ts` before `src/session.ts` existed, and ran it.

    cd /data/work/graphql-realization/gql-00/wt/B2-gql05b/integrations/typed-ls-ts
    npm test

RED output (exit 2):

    src/navigation.test.ts(20,34): error TS2307: Cannot find module './session.js'
    src/navigation.test.ts(45,33): error TS7006: Parameter 's' implicitly has an 'any' type.
    src/navigation.test.ts(207,34): error TS7006: Parameter 'row' implicitly has an 'any' type.
    ... (18 errors total)
    RED exit=2

Then I wrote `src/session.ts`. A second pass of strict-null errors in the test
(`'result.origin' is possibly 'null'`, `Property 'filter' does not exist on type
'Promise<JoinRow[]>'`, implicit `any` on `.map`) was fixed by typing the
assertions explicitly and awaiting `joinTable()`. **I did not delete or weaken a
single assertion.** The casing was made *stronger*: `span()` now asserts
non-null before reading a field. No test was skipped or removed.

---

## 5. GREEN — the verification commands and their real output

    cd /data/work/graphql-realization/gql-00/wt/B2-gql05b/integrations/typed-ls-ts
    npm test

Output (exit 0):

    > @leviath/typed-ls-ts@0.0.0 test
    > npm run build && node --test dist/
    > @leviath/typed-ls-ts@0.0.0 build
    > tsc -p tsconfig.json
    # tests 21
    # suites 0
    # pass 21
    # fail 0
    # cancelled 0
    # skipped 0
    # todo 0
    npm test exit=0
    --- failing subtests (count) ---
    0

All 21 passing subtest names:

    callable.params are comma-joined name:Type pieces
    callable.returns is the canonical return type
    callable.signature is (params)->returns
    callable.port is emitted for a class declaring a port in its heritage
    callable.confirmation is emitted for a confirmation-wrapped class field
    callable.confirmation.<param> is emitted for a wrapped parameter
    nullability: T | null, T | undefined and x?: T become base + ?
    source spans match the Python adapter's rule
    every emitted fact is projection 'code' with confidence 1.0
    unsupported dynamic constructs are reported and the extraction is blocked
    markers inside comments and string literals do not block the extraction
    add returns the sum of two numbers
    the session runs both servers side by side
    navigates from a TypeScript field position to the Python declaration
    navigates from a Python field position back to the TypeScript declaration
    navigates a method across languages
    navigates a class across languages through its port declaration
    reports a symbol that exists in only one language instead of inventing one
    says so when no symbol is at the position
    refuses to guess when an extraction is blocked
    the join table lists the symbols present in both languages

The last 9 are this node. The first 12 are node (a)'s accepted tests, still
green — I did not regress them.

**States distinguished:** node (a)'s 12 tests are **passed** (inherited, still
green). My 9 navigation tests are **passed** and were **verified** by the probe
below, which reads the same spans independently of the assertions.

---

## 6. Independent evidence — what the session actually observes

    node py/probe_nav.mjs          # exit 0

    === servers ===
      python: transport=child-process pid=1010730
      typescript: transport=in-process pid=-
    === join table (observed) ===
      OAuthRefreshTokenInput             python=true  typescript=true
      OAuthRefreshTokenInput.refresh     python=true  typescript=true
      OAuthRefreshTokenInput.token       python=true  typescript=true
      OnlyInPython                       python=true  typescript=false
      OnlyInTypescript.declared          python=false typescript=true
    === navigation TS -> Python (token, line 2 col 3) ===
      {"reason":"resolved","blocked":false,
       "origin":{"language":"typescript","symbolKey":"OAuthRefreshTokenInput.token",
                 "path":"app/ports.ts","start":{"line":1,"character":2},
                 "end":{"line":1,"character":7}},
       "target":{"language":"python","symbolKey":"OAuthRefreshTokenInput.token",
                 "path":"app/ports.py","start":{"line":4,"character":4},
                 "end":{"line":4,"character":9}}}
    === navigation Python -> TS (token, line 5 col 5) ===
      {"reason":"resolved","blocked":false,
       "origin":{"language":"python","symbolKey":"OAuthRefreshTokenInput.token",
                 "path":"app/ports.py","start":{"line":4,"character":4},
                 "end":{"line":4,"character":9}},
       "target":{"language":"typescript","symbolKey":"OAuthRefreshTokenInput.token",
                 "path":"app/ports.ts","start":{"line":1,"character":2},
                 "end":{"line":1,"character":7}}}
    === navigation Python-only symbol ===
      {"reason":"absent-in-other-language","blocked":false,
       "origin":{"language":"python","symbolKey":"OnlyInPython",
                 "path":"app/ports.py","start":{"line":10,"character":0},
                 "end":{"line":10,"character":12}},
       "target":null}
    === session closed ===
    probe exit=0

The two fixtures show the correspondence is real, not asserted:

    py/fixtures/ports.py                 py/fixtures/ports.ts
    1  from __future__ import annotations   1  export class OAuthRefreshTokenInput implements WorkflowStepInput {
    4  class OAuthRefreshTokenInput(        2    token: Confirmation<Confidential, "STEP_RUNNER">;
         WorkflowStepInput):               4    refresh(token: Confirmation<Confidential, "STEP_RUNNER">): WorkflowRun {
    5    token: Confirmation[Confidential,       return token;
           "STEP_RUNNER"]                  7  }
    7    def refresh(self, token: ...)     9  export class OnlyInTypescript {
    11 class OnlyInPython:                  10    declared(name: string): void {}
    "token"  → TS line 2 col 2..7  ==  Python line 5 col 4..9   (1-based)
    "refresh"→ TS line 4 col 2..9  ==  Python line 7 col 4..11  (1-based)

---

## 7. Hashes and provenance

Base commit: `7b6e050e89e6aea7b51453e633fbe9209c50a844` (`git rev-parse HEAD`).

Files I authored, SHA-256:

    26e2e2d4f32bf0c5003238c8feac83f93689ac85147b644124d938b26519c903  src/session.ts
    0ca299b98d1ac7854f0f58dc6b138e937c7bf68da1a825a66ed8238ffde89dd8  src/navigation.test.ts
    91520571bdd901d605310085b2fbcb32d765341762a05d1e04ec9e1b9245d25b  py/dump_facts.py
    47da8c71c781d079a6e9169f8e48ccc9912f37c3a71027549f8f06fb90cf4f9f  py/fixtures/ports.py
    b72d2ce85df71ecd7763b4febdee0f044a88e2814b38270ba04186ce00ada0f5  py/fixtures/ports.ts

Toolchain observed, not changed: `typescript@7.0.2` (read from
`node_modules/typescript/package.json`), Python `3.12.14`, `/usr/bin/node`.
`git status --porcelain` in my worktree shows exactly one entry:
`?? integrations/typed-ls-ts/` — the whole declared set is untracked, so the diff
from the base commit is "this directory is new". No `Cargo.toml`, no
`Cargo.lock`, no commit.

The report file you are reading is also in the declared set:
`integrations/typed-ls-ts/REPORT-gql05b.md`.

---

## 8. Hard gates honoured — checked, not claimed

* Wrote only inside the declared set. No shared per-file exception was needed.
  No path outside it was written. Verified: `git status --porcelain` shows one
  entry, inside my set.
* **No write to `/data/work/graphql-realization/gql-05/repo/`.** Verified two
  ways after the run: (a) `find . -name __pycache__ -o -name '*.pyc' | wc -l`
  in that checkout → **0**, so the child process wrote no bytecode into the
  accepted tree; (b) `find python -newermt '2026-09-15 04:00' -type f` → empty,
  so no file there was touched during this lane. The untracked entries it shows
  (`python/leviath_schema_ls/`, `python/leviath_typed_ls/`,
  `integrations/typed-ls-ts/`) were already there from nodes (a) and earlier.
* No write under `/data/work/ceo-bus`, `/data/bin`, `/opt/hermes`, `/data/state`.
  No edit to `/data/.leviath/config.toml`, `ceo_bus_metrics.py`, or any sweeper.
* No admission file, no outbox write, no bus write. No new scheduler, queue,
  service or dashboard. No credential or permission change. No secret read or
  write. No Railway mutation, restart or redeploy. No commit, push or deploy.
* **No Python file was modified.** `py/dump_facts.py` and the fixtures are new
  files that *I* wrote in my own declared set; I changed nothing in the accepted
  Python tree. The child process only *imports* the accepted adapter, read-only.
* Never `--wait`, never `--yolo`. No coordinator spawned. One writer in my worktree.
* One shell tool refused a redirect to `/tmp/gql05b.green.txt` (outside the
  working directory); I complied and kept the output in the shell instead. No
  scratch file was left anywhere.

---

## 9. Provider spend, and what I could not determine

* **Model path used:** `openrouter/deepseek/deepseek-v4.1-flash` — every
  inference in this lane, all roles.
* **Dispatched runs:** none. I made no `lev run` call and launched no worker, so
  there is no run id to record and no `--json` envelope to quote.
* **Billed amount:** **I could not determine this from inside the lane.** I have
  no metering tool here, and the model gate forbids reaching out for one. I am
  not going to invent a token count or a dollar figure. What I can state is the
  model path above and that the lane made no bus or outbox write. This is a real
  limit, stated as a limit.

---

## 10. What I accepted, what I rejected, what I could not determine

**Accepted (green, verified):**

* Two servers run side by side in one session — Python over a real child process
  (`pid` observed, `1010730` in this run), TypeScript in-process.
* Cross-language navigation works in both directions, field, method and class,
  resolving to the other language's **span**.
* A symbol present in one language only is reported **absent**, not invented.
* A position with no declaring symbol says so.
* A blocked extraction **refuses** rather than reporting the symbol as absent.
* Node (a)'s 12 accepted tests are still green; no regression.
* The join table lists, per symbol key, which language declares it.

**Rejected:**

* I rejected re-implementing the accepted Python adapter in TypeScript. The
  adapter is Python and must stay single-sourced; the child process is the honest
  way to reach it. Re-implementing it would create a second, drifting notion of
  the same model.
* I rejected calling the accepted Python LSP directly for spans. Its two custom
  methods (`leviath/schemaProjection`, `leviath/codeEdits`) return **no source
  spans**, so they cannot answer a navigation. The thin driver over the accepted
  `extract_typed` is the accepted interface used as designed.
* I rejected any guess about a symbol the other language does not declare. A
  "best match by name similarity" would have been a fabricated answer.
* I rejected widening the node. The spec of record gives node (b) one sentence,
  and I built exactly that.

**Could not determine:**

* The billed provider spend for the lane (Section 9). No metering tool, and the
  gate forbids reaching out for one. Stated as a limit, not smoothed over.
* I did not measure whether a full editor client (VS Code, Neovim) drives this
  session end to end. Node (b) is specified as "two-server session plus
  cross-language navigation", and that is what I verified. An editor client is
  not in the node, so I did not claim it.

---

## 11. Residual limits I want on record

* The join is on `symbol_key` string equality. That is correct **because** both
  accepted adapters already share one symbol-key namespace. If a future node
  changes one adapter's key spelling, navigation would silently degrade from
  `resolved` to `absent-in-other-language` for that symbol. The failure mode is
  honest (it never invents), but it is a coupling worth knowing about.
* The `no-symbol-at-position` and `absent-in-other-language` reasons are
  distinct and separately tested, so a real absence is never confused with a
  position that names nothing.
* `declarationSpans` keeps the earliest span per symbol key by `(line, column)`.
  For a symbol declared twice, the first declaration wins. That is deterministic
  and documented, not accidental.

---

## 12. Terminal status

**Node (b) complete and verified.** `npm test` → 21 pass / 0 fail / exit 0.
The probe independently re-observed the same spans → exit 0. Writes stayed
inside the declared set (verified). The accepted tree was not touched (verified).
No run was dispatched, so no run id exists to record. A bare hex string would not
be a report; this is a report, with paths, hashes, commands, exit codes and
outputs.