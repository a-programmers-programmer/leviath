# GQL-08 — simple code generation hook

## What was asked

Build one deterministic code generation hook. It takes the reconciled contract
snapshot plus an output directory. It emits typed interfaces, operation clients,
scalar mappings and source maps. Handwritten behavior must live outside the
generated files.

## What is done

I resumed the earlier GQL-08 attempt from `/data/work/graphql-realization/gql-08`.
The package was already written. I did four things.

1. Copied the package and the contract snapshot into this working directory.
2. Fixed `verify.py` so it resolves paths against the output directory and runs
   `tsc` with the right working directory.
3. Fixed a bash syntax error in the copied proof script.
4. Wrote this report and `EVIDENCE.json`.

The deliverable is `repo/python/leviath_codegen/`. It holds eight modules.

- `model.py` loads the contract snapshot and verifies the pin.
- `render.py` renders Python, TypeScript, SDL, operations, source map.
- `generate.py` is the one command. It writes only changed files.
- `verify.py` compiles Python, typechecks TypeScript, checks SDL agreement.
- `cli.py` and `__main__.py` expose `generate` and `verify`.
- `test_codegen.py` holds 22 pinned acceptance tests.

## Where

- Working directory: `/data/wt/T01M2KX1Z7YX3TYJEB76GFKMA5M-a1`
- Repo: `repo/` (own git repo), branch `fleet/gql-08`
- Base commit: `2d9e476` "base: vendored GQL-02 reconcile package (upstream 7b6e050e)"
- Change commit: `5c21587` "GQL-08: add leviath_codegen deterministic code generation hook"
- Contract snapshot: `contract/`, pin `ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559`

## Definition of done, clause by clause

- Second invocation produces no diff. `written: 0, unchanged: 17`; `diff -r` of
  the tree before and after exits 0.
- Stale contract identity is rejected. A tampered snapshot exits 1. The run
  leaves no output tree behind.
- Handwritten resolvers survive regeneration. Edited resolver files keep their
  bytes and the report lists them as `handwritten_kept`.
- Python output compiles and typechecks. `compileall` exit 0; `mypy --strict`
  exit 0.
- TypeScript output typechecks. `tsc -p` exit 0.
- Generated SDL and operations agree with the accepted contract. Generated SDL
  hashes to the pin. `graphql.validate` returns no errors. All 9 root fields are
  covered.

## Evidence

`proof/run_proof.sh` runs all seven clauses and prints each command with its real
exit code. It ends with `PROOF PASS`. The full log is `proof/proof.log`.
`EVIDENCE.json` lists the commands the dispatcher re-runs.

## Cost

No provider calls. No network. Actual provider spend: $0.

## Blocked / not done

- `bd` is not installed in this environment, so I could not claim a bead or note
  on it. No bead ID exists for this run.
- The change is not pushed. No PR was opened. I never push to master and the
  policy forbids outbound communication.

## Residual risk

- The proof needs `mypy`, `graphql-core` and a `tsc` binary. All three are
  present here. A machine without them reports `SKIP` for TypeScript, not PASS.
