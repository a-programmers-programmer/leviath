# GQL-08 acceptance report — the simple code generation hook

Lane: `L03-gql08`. Branch: `lane/L03-gql08`.
Worktree: `/data/work/graphql-realization/gql-00/wt/L03-gql08`.
Base commit: `7b6e050e89e6aea7b51453e633fbe9209c50a844`.
Report written: 2026-09-15 (lane run). Model path used: `openrouter/deepseek/deepseek-v4.1-flash`.

This lane harvested the saved GQL-08 result and re-verified it. I did not re-author
the hook. I did not regenerate the contract. I did not write outside my declared set.

---

## 1. Verdict

**ACCEPTED, with one clause now closed and one real defect found.**

The prior coordinator ruling was `ACCEPTED-WITH-ONE-CLAUSE-OWED`. The owed clause was
"TypeScript output typechecks" — no `tsc` was found on that box. I found a working
compiler at `/data/lsp/node_modules/typescript/bin/tsc`. That clause now PASSES on my
own fresh output.

Independently of the DoD, I found a **real path defect in `verify.py`**. Details in
section 6. It does not break the DoD clauses, because the outputs themselves do
compile and typecheck. It does break the `verify` subcommand when `--out` is a
relative path.

---

## 2. What I accepted

| DoD clause (verbatim) | Result | How I checked it |
| --- | --- | --- |
| Second invocation produces no diff | **PASS** | Two `generate` runs into one dir; sha256 of all 17 files after each; `diff` = `NO_DIFF` |
| Stale contract identity is rejected | **PASS** | Harvested e2e.log D4: tampered SDL exits 1, no output tree left behind; wrong `--expected-fingerprint` exits 1 |
| Handwritten resolvers survive regeneration | **PASS** | Harvested e2e.log D5: markers kept; my run 2 reports `KEEP` for all 3 handwritten files |
| Python output compiles/typechecks | **PASS** | `compileall` exit 0; `mypy --strict` exit 0 |
| TypeScript output typechecks | **PASS** | Vendored `tsc -p` exit 0 on my fresh output |
| Generated SDL/operations agree with accepted contract | **PASS** | Generated `contract/schema.graphql` sha256 == accepted pin; ops validate with 0 errors |

All measurements below are on **my own fresh output** at
`crates/leviath-package/.gql08-verify/out/`, produced from the harvested contract at
`/data/work/graphql-realization/gql-08/contract`.

Accepted contract identity (observed, not assumed):

```
ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559   contract/schema.graphql
ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559   accepted pin
identical = True
```

---

## 3. Exact commands, outputs, exit codes

### 3.1 Clauses 1 and 3 — determinism and handwritten survival

```
$ python3 -m leviath_codegen generate --contract <gql-08>/contract --out $S/out
generate: PASS contract=ff9df4e32b05747e operations=9
  written: 17  unchanged: 0
  SEED  handwritten/README.md
  SEED  handwritten/python/leviath_handwritten/resolvers.py
  SEED  handwritten/typescript/resolvers.ts
EXIT=0
snap1 files: 17

$ python3 -m leviath_codegen generate --contract <gql-08>/contract --out $S/out
generate: PASS contract=ff9df4e32b05747e operations=9
  output: crates/leviath-package/.gql08-verify/out
  written: 0  unchanged: 17
  KEEP  handwritten/README.md
  KEEP  handwritten/python/leviath_handwritten/resolvers.py
  KEEP  handwritten/typescript/resolvers.ts
EXIT=0

$ diff -u snap1.txt snap2.txt
NO_DIFF (17 files byte-identical)
```

### 3.2 Clause 4 — Python compiles and typechecks

```
$ python3 -m compileall -q .../out/python/leviath_generated
COMPILEALL_EXIT=0
$ python3 -m mypy --strict --no-error-summary --no-incremental \
    --cache-dir .../out/.mypy-cache .../out/python/leviath_generated
MYPY_EXIT=0
```

### 3.3 Clause 5 — TypeScript typechecks (the owed clause, now closed)

```
$ node /data/lsp/node_modules/typescript/bin/tsc -p .../out/typescript
TSC_EXIT=0
$ node /data/lsp/node_modules/typescript/bin/tsc --version
Version 7.0.2
```

This is the same command the saved `evidence/e2e.log` line 55 records. That line says
`EXIT=0`; I reproduced the same exit code on a tree I generated myself. The compiler
is TypeScript version 7.0.2.

### 3.4 Clause 6 — generated SDL/operations agree with the accepted contract

```
$ python3 -m leviath_codegen verify --out <absolute path>/out --json
"result": "PASS"
"contract_fingerprint": "ff9df4e3...3559"
"pinned_fingerprint":   "ff9df4e3...3559"
"manifest":   {"result": "PASS", "problems": []}
"python":     {"result": "PASS", compileall exit 0, mypy exit 0}
"typescript": {"result": "PASS", tsc exit 0}
"agreement":  {"result": "PASS", "errors": [],
               "root_fields": 9, "covered_root_fields": 9}
```

The generated operations cover all 9 root fields with zero validation errors.

### 3.5 Clause 2 — stale identity rejected (harvested, read-only)

From the saved `evidence/e2e.log`:

```
$ python3 -m leviath_codegen generate --contract .../contract-drift --out .../out-drift
generate: FAIL: contract identity rejected: contract pin mismatch ...:
  observed 68f2f156818df5530096fd0f6d6883f297b9b21b73cd409d8f9793a9f2b46a0f
  != pinned ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559
EXIT=1
out-drift exists = False
no half-written output tree: OK
```

The rejected run leaves no output directory. That is the fail-closed behavior the
clause asks for. I did not rerun this mutation myself; I report it as harvested.

---

## 4. Source identity, hashes, and my diff

Source/base commit, observed by `git rev-parse HEAD` in my worktree:

```
7b6e050e89e6aea7b51453e633fbe9209c50a844
```

My writes vs base, inside my declared set only:

```
?? crates/leviath-package/.gql08-verify/     (62 files, 2.8 MB — verification scratch)
?? crates/leviath-package/GQL-08-ACCEPTANCE-REPORT.md   (this report)
```

I wrote nothing else. I did not touch `docs/workflow/schema-workflow.graphql` or
`docs/workflow/workflow-schema-decisions.md`. Those two files are the pin host and
the accepted contract. Preserving them is the correct action, so my declared set is
used only by the report and the scratch directory.

### 4.1 Harvested source artifact hashes (read-only)

```
fbb049086b56ce5b6b20053a2a9f72ba42cdd1f86bc3fbdf2f38614600f479c8  contract/edge-invariant-rules.json
2cb5a7653f706bed42d3cd5787a3896701fce24884072afa9063a297db835a4f  contract/schema-workflow.graphql
8c3056bbb91a2fd52af261c25768bc9645ccde24620a59b6a2e3fa32e5f5c9e5  contract/workflow-schema-decisions.md
d85171ae01572804eeb492879a8631580d3499eedb9763f3b7ef23df499954cd  evidence/e2e.log              (9281 B)
3161ed988d904e1d36be47645db74b032d77b2cc9191fbdedc2b75d40b336697  evidence/pin_probe.txt        (149 B)
d80ff1ce7714aef77fdc51d14515166efdfa9a5c34fdf0952185f66652665f5a  python/leviath_codegen/__init__.py
74475b8f5539bf38bf7d13f1dd202557368a1c5bb41e5f79a8aae25daa1b3b93  python/leviath_codegen/__main__.py
8a49bf12768b4bd8e600c12ec1c0e8d39eac50219bc43ff1aa1745adec9e5a0d  python/leviath_codegen/cli.py
75bdff29ae2617aea5ad4202379125b0aa6c514d3990185ae2f754b1cce1b037  python/leviath_codegen/generate.py
faabf6c86aea611aa2cb67ef8ae163f45f196413aa306f21a82faaab01c313b4  python/leviath_codegen/model.py
94c060fb1ae2d0d0d763497d26c92b256f69f3e87a794422e4503573167f90c8  python/leviath_codegen/render.py
1e45316d13a622cfd299d603118ad7e684791caa5da11cb8237df95e8a0228aa  python/leviath_codegen/test_codegen.py
e0e94948c47f7f84bd8eb501f1939684351d8a81e4b2f2cc4f202ebb7e164567  python/leviath_codegen/verify.py
```

### 4.2 The 17 generated files from my own fresh run

```
1642968ac695c5087c6a99636d4b28e595ec6ee27f450e3a77e3c63a897e3cc7  contract/operations.graphql
ff9df4e32b05747ee3605042407dcc7e17a347f41bc1464ce567c2f484813559  contract/schema.graphql
5ac5212f939ee535c81dbe8874cdfb3b6ee5030bdbbb17f70d44876f8b51de2c  handwritten/README.md
3550009ee766ac8b7c09f4a0dc556e6f3cbb6f1653acd3b942e187762526dd21  handwritten/python/leviath_handwritten/resolvers.py
6323c919ef5962fc1bd3cbf93caca860940b68f1a5c2320fb4a2941f24a662f2  handwritten/typescript/resolvers.ts
3dda3b0016eac5193dfd61d2da55df006ad6c653afeaba84ef378ca1f2246e68  manifest.json
2d65f2ff1d1c4aaefa0ea343cd774fadfdb39455a7bccd83596096785b774d2f  python/leviath_generated/__init__.py
f0410e9797eea6d4226c44bfc94d3ca69bab4f8580f96410770d0967b96ba43c  python/leviath_generated/interfaces.py
1b991eb477f332a14d2a5f7b1a70ca677bf0a344d8ec52a42676688f6fb7db15  python/leviath_generated/operations.py
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  python/leviath_generated/py.typed
a4802729802868043e16fb648a697d26e3e082278b72d6e9e64c61b5a164dcca  python/leviath_generated/scalars.py
44e82acdf283a3a419be8c86d82ced894d2b9b3a7fcaa862bb7c3a724552f49c  sourcemap.json
ddeee04f7b73d3a6a9d9d4ec8ab21d626c902f2d6cb8bbd7f4394fe49d6812fa  typescript/src/index.ts
8ef0f9b13bcf4e3ea65a0ef8ee3ae7687e6170ed640d3c56dca0d5b95db4f3cf  typescript/src/interfaces.ts
8fc236b762c293bf4a4032cda50906e8ea11cd81f103fca5f93b869c0cfa6f8a  typescript/src/operations.ts
db0b6d5ab7c36d602158fb6e0dcf2b739a9d8098c02a90803a567b25f82ed98d  typescript/src/scalars.ts
ae0c8c05523371bf974d4402269475f9eee28480fd5b9b8edb96d46a4f9d33b0  typescript/tsconfig.json
```

### 4.3 The hook's own test suite (read-only, harvested)

```
$ PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<gql-08>/repo/python \
    python3 -m pytest leviath_codegen/test_codegen.py -q -p no:cacheprovider
......................                                                   [100%]
22 passed in 3.59s
PYTEST_EXIT=0
```

I ran this read-only. I did not edit the test file. I did not delete or weaken a test.

---

## 5. Provider spend (harvested, read-only)

I searched `/data/.leviath/runs/` for runs naming GQL-08. One run names GQL-08.
Three other runs share the same start timestamp but are different tasks.

| Run ID | Status | Model | Cost USD | Exact | Unpriced |
| --- | --- | --- | --- | --- | --- |
| `worker-1789398179-b080e86ab55a` | complete | openrouter/deepseek/deepseek-v4.1-flash | 2.396865426 | true | 0 |
| `worker-1789398179-22e651cfaa51` | complete | openrouter/deepseek/deepseek-v4.1-flash | 0.824681472 | true | 0 |
| `worker-1789398179-65232ff3727e` | complete | openrouter/deepseek/deepseek-v4.1-flash | 1.942363574 | true | 0 |
| `worker-1789398179-84e93273e280` | complete | openrouter/deepseek/deepseek-v4.1-flash | 1.064991680 | true | 0 |

Only the first row is GQL-08 spend. Its task string starts
`# TASK: GQL-08 — Build the simple code generation hook`. The other three carry the
tasks GQL-12, GQL-04, and GQL-35, so they are **not** GQL-08 spend and I do not
add them.

**Actual GQL-08 provider spend: 2.396865426 USD, one run, `cost_is_exact=true`,
`unpriced_calls=0`.** The run's single `execute` stage banked 8326 active seconds and
raised a runaway warning. That is a real fact from `stages.json`, reported plainly.

My lane made no provider call. All work here was offline shell, hashes, and file reads.

I saw no provider throttling in this lane, so I have no raw log line to report.

---

## 6. The defect I found (new, not in the original DoD)

`verify.py` fails on a **relative** `--out`. It passes on an **absolute** `--out`.

Cause: line 77 sets `cwd=out / "python"`, while line 74 passes the package path as a
string relative to the original working directory. mypy resolves that string against
the new cwd, so it cannot find the package.

Exact evidence:

```
$ python3 -m leviath_codegen verify --out <RELATIVE>/out --json
"python": {"result": "FAIL", "mypy": {"exit": 2,
  "stderr": "mypy: error: Cannot read file
    'crates/leviath-package/.gql08-verify/out/python/leviath_generated':
    No such file or directory"}}
VERIFY_EXIT=1

$ python3 -m leviath_codegen verify --out <ABSOLUTE>/out --json
"result": "PASS", mypy exit 0
```

Reproduced directly with the same cwd and the same relative path:

```
command: .../python3 -m mypy --strict ... crates/leviath-package/.gql08-verify/out/python/leviath_generated
cwd:     crates/leviath-package/.gql08-verify/out/python
exit:    2
stderr:  mypy: error: Cannot read file 'crates/...': No such file or directory
```

Source lines in `verify.py` (sha256 `e0e94948c47f7f84bd8eb501f1939684351d8a81e4b2f2cc4f202ebb7e164567`):

```
66:    mypy_command = [
74:        str(out / ".mypy-cache"),
77:    mypy = _run(mypy_command, cwd=out / "python")
```

Effect: `generate` is unaffected, and the generated outputs do compile and typecheck.
Only the convenience `verify` subcommand misreports. A README example that uses a
relative `--out` would show a false FAIL.

I did **not** fix this. `verify.py` lives at
`/data/work/graphql-realization/gql-08/repo/python/leviath_codegen/verify.py`, which is
**outside my declared set**. Editing it would be a write outside the declared paths.
Per my instructions I stop and report it as a finding instead of writing it.

Suggested one-line fix, for the owner of that directory: make the path absolute, or
drop the `cwd=` argument.

---

## 7. Blocked / passed / verified states, stated separately

* **Verified by me, on my own fresh output:** all six DoD clauses. Clauses 1, 3, 4, 5
  and 6 I measured myself. Clause 2 I report as harvested evidence from the saved
  `e2e.log`, which I read but did not rerun.
* **Passed:** the hook's own suite, 22 tests, exit 0. Generated Python compiles and
  typechecks. Generated TypeScript typechecks. Generated SDL equals the accepted pin.
* **Blocked:** none for the DoD. `bd` is not installed on this box, so I have no bead
  ID to claim and I invent none.
* **Not claimed:** no commit, no push, no deploy. No independent consumer of the
  generated output exists yet. I did not verify runtime behavior of any resolver.
* **Rejected:** nothing about the deliverable itself. The one thing I reject is the
  old `NOT-INDEPENDENTLY-VERIFIED` state of the TypeScript clause. It is now verified.

---

## 8. Could not determine

* Whether the producing run's `tsc` was the same 7.0.2 binary. The log records the
  same path, but not a version. I record my own version and do not assert theirs.
* Whether the generated TypeScript actually runs. Only typechecking is claimed, which
  is what the DoD clause asks for.
* The provenance of `evidence/e2e.log` beyond its own content. It is a saved
  transcript, not a signed attestation. I treat its D4 and D5 sections as harvested
  evidence and say so where I rely on them.

---

## 9. Rollback

Delete the two paths I created:

```
rm -rf crates/leviath-package/.gql08-verify
rm -f  crates/leviath-package/GQL-08-ACCEPTANCE-REPORT.md
```

No destructive migration ran. No production store was written. No commit was made.

---

## 10. Constraints honored

No write outside the declared set. No edit to `Cargo.toml`, `Cargo.lock`, the config,
`sweeper` scripts, or `ceo_bus_metrics.py`. No write under `/data/work/ceo-bus`,
`/data/bin`, `/opt/hermes`, or `/data/state`. No admission, outbox, or bus write. No
new scheduler, queue, service, or dashboard. No credential or permission change. No
secret read or write. No Railway mutation, restart, or redeploy. No commit, push, or
deploy. No `--wait`, no `--yolo`, no second coordinator. I did not touch the live
`typed-ls-ts` subtree in lane GQL-05, nor any other lane's worktree. I did not cite
the retired 0104 ceiling. All inference used `openrouter/deepseek/deepseek-v4.1-flash`.