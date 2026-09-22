# T01M34X90864V78Z5FVNMM33M9J — fix E0063 missing field in struct initializer

## Merge
`git merge --no-edit fleet/T01M34W830V3KGC208HVTTKW98A` fast-forwarded
d22a2ebc -> fa3c7163. No conflicts.

## Error
```
error[E0063]: missing field `items_region` in initializer of `blueprint::stage::FanOutConfig`
    --> crates/leviath-core/src/blueprint/mod.rs:2446:9
```

## Fix
- File: `crates/leviath-core/src/blueprint/mod.rs`
- Line: 2446 (inside test helper `fanout_config()`)
- Field added: `items_region: None`

Value `None` matches the other constructions of `FanOutConfig` on master
(`crates/leviath-core/src/manifest/tests.rs:2386`, `:2419`, `:2494` all use
`items_region: None`). The field is an optional authoritative context region
(`crates/leviath-core/src/blueprint/stage.rs:200`, `Option<String>`), so the
test helper keeps the default of "no items region".

No other file was touched.

## Verification
`CARGO_TARGET_DIR=/data/work/leviath/target cargo test -p leviath-core --no-run 2>&1 | grep -B3 -A14 'error\[E0063\]'`
prints nothing after the fix.