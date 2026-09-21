# REPORT — GQL-04: typed-code language server with a Python adapter

## What was done

I built the second language-server process for typed code. The Python adapter is
first, because the admin site uses Python.

Owned path: `repo/python/leviath_typed_ls/`.

| Module | Job |
| --- | --- |
| `typed_code_adapter.py` | reads declared callable ports, params, returns, confirmation wrappers with `ast` only |
| `schema_reader.py` | reads SDL into the same facts, plus callable facts and `@user_confirmation` |
| `projection.py` | the one bridge: code facts <-> schema facts, and SDL render |
| `codegen.py` | a schema edit -> concrete code edits with source spans |
| `gate.py` | contract pin re-verification and the obligation block |
| `__main__.py` | JSON-RPC 2.0 stdio server (`serve`) |

Reuse, not redo: the package imports GQL-02's accepted engine
(`leviath_reconcile.adapters.python_adapter`, `schema_adapter`, `crosswalk`,
`model`). No engine code is copied. The contract pin reuses GQL-02's
`contract_fingerprint`, so both gates compute the same bytes.

Static analysis only. No import, exec or eval of the artifact under edit. A
dynamic construct (`__getattr__`, `globals()[`, `setattr(`, `eval(`, `exec(`,
`from x import *`) becomes an explicit `UnsupportedConstruct` finding with path,
line, reason and obligation, and it blocks the extraction.

## Where

- Repo: `a-programmers-programmer/leviath`
- Base commit: `15dc182e0bc6c28dfa93f4b8dd3c423372808f61`
- Branch in the source checkout: `gql04/typed-code-ls` (HEAD `7b6e050e`)
- Working directory: `/data/wt/T01M2KX1Z7XVMJC8VBJ2KGJMNFA-a1`
- Run id: `R01M2MCP7R9C7DT6WH7ZV27B0VX`
- Task: `T01M2KX1Z7XVMJC8VBJ2KGJMNFA`

Base evidence: `15dc182e` is an ancestor of the branch HEAD, and
`git ls-tree 15dc182e -- python` lists no `python/leviath_reconcile` and no
`python/leviath_typed_ls`. Both paths are new work on top of the base.

I did not commit or push. Policy forbids outbound communication and writing
outside my working directory, so the source checkout keeps the package
untracked. The package itself is in this working directory:
`repo/python/leviath_typed_ls/`.

Prior work resumed: I read `/data/work/graphql-realization/gql-04/HANDOFF.md`
and `REPORT-GQL-04.md`. Their still-valid evidence is copied into `evidence/`
with a `-prior` suffix on the two documents. I did not redo finished steps.

## Definition of done — clause by clause

| Clause | Where it is proved |
| --- | --- |
| second language server for typed code, Python first | `python3 -m leviath_typed_ls serve`; `initialize` reports `leviath-typed-code-ls` |
| declared callable ports and metadata by static analysis | `typed_code_adapter.extract_typed` with `ast`; `callable.port` from the base list |
| no execution of application imports | clause 6 of `probe_dod.py` parses every shipped module and fails on `exec`/`eval`/`compile`/`__import__`/`import_module`, `importlib`, `subprocess`, `from x import *` |
| Python edit updates the schema projection | clause 1 of `probe_dod.py`; `evidence/probe_server.txt` over a real process |
| schema edit produces concrete code edits | clause 2 of `probe_dod.py`; the applied text re-extracts to the schema facts |
| unsupported dynamic constructs reported explicitly | clause 4; each finding has path, line, reason, obligation, and `require_unblocked` raises `ObligationBlocked` |
| signatures, nullability, confirmation wrappers round-trip with source locations | clause 3; every fact carries `SourceRef(path, line, col)` |
| contract fingerprint pinned before dependent work, re-verified before acceptance | clause 5; `gate.verify_contract` matches the GQL-01 pin and drift raises `ContractDrift` |

## RED -> GREEN

The suite is `repo/python/leviath_typed_ls/tests/test_roundtrip.py`, 6 tests.
It was written RED first and the RED runs are kept: `evidence/red.txt` (6 failed,
exit 1) and `evidence/red-before-wiring.txt` (the first RED, before the wiring).
GREEN: `evidence/green.txt` (6 passed, exit 0) and `evidence/green_verbose.txt`
(per-test names).

The RED run exposed three real defects, all fixed: the printed SDL dropped the
`@user_confirmation` directive and the `!` marker; the schema-side argument type
read nullability in the wrong place; the schema edit did not emit a parameter
code edit. The tests still assert what they asserted at RED; no test was
weakened or skipped.

## Evidence

- `evidence/probe_dod.txt` — 35 assertions over all six clauses, exit 0.
- `evidence/probe_server.txt` — a real server process, real bytes, exit 0.
- `evidence/probe_roundtrip.txt` — the round trip, the block, the pin, exit 0.
- `evidence/red.txt`, `red-before-wiring.txt`, `green.txt`, `green_verbose.txt`.
- `evidence/import.txt`, `usage.txt`, `serve_eof.txt`.
- `evidence/commands.log` — exact commands, outputs and exit codes.
- `evidence/REPORT-GQL-04-prior.md`, `evidence/HANDOFF-prior.md`,
  `evidence/commands-prior.log` — copied prior evidence.

## What is left

Nothing in GQL-04 is blocked. A later order can add a second language (the
adapter is Python-first) or wire the server into an editor. Both are outside
GQL-04.

No spend: no paid model, no provider call, no paid probe. Cost is $0.

## Constraints held

- Static analysis only. No artifact execution.
- No new verifier, framework, queue or blueprint.
- No merge, no force-push, no production deployment, no destructive migration.
- One writer per file set; every write is under
  `repo/python/leviath_typed_ls/` in this working directory.
