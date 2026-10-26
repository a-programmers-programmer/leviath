# Rustls security lockfile repair report

## Scope and change

Updated the workspace `Cargo.lock` rustls registry package from `0.23.45` to the official patched release `0.23.46` for RUSTSEC-2026-0346/0347/0348/0349. The crate checksum changed from `0d41d731c7d2f962d1ccc364cec258de3c0e93b38c2fb3ba97ac74513048d634` to `48e13bd8c0e9365c43cfa5c9e8f9ad49d3c8444926c9aac819e0e4dc503c8fdf`. Release reference: https://github.com/rustls/rustls/releases/tag/v%2F0.23.46 .

Only `Cargo.lock` is modified. Cargo's update produced one unrelated package dependency-selection difference in `tempfile` (`getrandom 0.4.3` to `0.3.4`) in addition to the intended rustls changes. The scope explicitly forbids unrelated selection changes, so I restored the original `tempfile` selection after confirming it resolves under the lockfile. The final diff contains only the rustls package version and checksum edits.

## Exact file hashes

- Before: `bb34cdbefd63290f2ef3815415aced42564e6a4c8b1646d28b0cef89afbad335` (`Cargo.lock` at checkout before update)
- After: `301ef9487798193d7498c461d4603e8f56ecb81d1b1a290852a96e3e8c0b6068` (`Cargo.lock` final candidate)

## Exact final diff

```diff
@@
 name = "rustls"
-version = "0.23.45"
+version = "0.23.46"
 source = "registry+https://github.com/rust-lang/crates.io-index"
-checksum = "0d41d731c7d2f962d1ccc364cec258de3c0e93b38c2fb3ba97ac74513048d634"
+checksum = "48e13bd8c0e9365c43cfa5c9e8f9ad49d3c8444926c9aac819e0e4dc503c8fdf"
```

The `tempfile` line is unchanged in the final candidate (`getrandom 0.4.3`).

## Commands and actual results

1. `python3 /data/fleet/fleet/harness.py precheck --db /data/fleet/graph.db --task T01M4XFJEV2MWJ4B280JE9NDTEC --workdir "$PWD"` — `CHECK_DEFERRED`, reason `long test precheck budget` (45-second budget; dispatcher owns the long check).
2. `cargo update -p rustls@0.23.45 --precise 0.23.46` — exit 0; output: `Updating rustls v0.23.45 -> v0.23.46`. Registry package downloaded from crates.io during subsequent build. Cargo also changed `tempfile`'s selected getrandom edge; restored that unrelated edge in lockfile to maintain exact scope.
3. Mechanical lockfile assertion:
   ```sh
   python3 - <<'PY'
   import pathlib,tomllib
   p=tomllib.loads(pathlib.Path('Cargo.lock').read_text())['package']
   r=[x for x in p if x['name']=='rustls']
   assert len(r)==1 and r[0]['version']=='0.23.46',r
   PY
   ```
   Result: passed on the candidate.
4. `cargo deny --all-features check` — exit 101: Cargo reports `no such command: deny`; `cargo-deny` is not installed in the managed Cargo environment. No installation or governance bypass was attempted.
5. `cargo check --locked -p leviath-cli --lib` — invoked on final candidate and still running at report capture. It compiled/checked rustls 0.23.46 and reached compilation/checking of Leviath packages; no completed exit status was captured. Do not treat this as a full green result.
6. `cargo test --locked -p leviath-cli --lib tls -- --nocapture` — not run. The required mechanical command chain stops at the unavailable `cargo deny` command; the preceding `cargo check` did not yet have a recorded exit status.
7. Final SHA and diff inspection: `sha256sum Cargo.lock`; `git diff -- Cargo.lock` — hash matches the after hash above and shows only rustls version/checksum. Git status showed only `M Cargo.lock` before this required report was written.

Known standalone docs/suppression failures are baseline-only and were not modified. The source lockfile update does not establish that any deployed binary contains the patched rustls.
