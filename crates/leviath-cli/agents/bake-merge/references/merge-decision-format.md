# HarnessQL merge decision format (`harnessql-merge-decisions/v1`)

The merge lane never reads a candidate schema and never writes a schema. It is
shown a compact diff in its task and it returns **one JSON document** as its
final output. Everything below is enforced mechanically by
`bake-off/scripts/merge_decisions.py`; a document that breaks a rule is rejected
with a code, and the rejection names the offending type, field or pair.

## The document

```json
{
  "contract": "harnessql-merge-decisions/v1",
  "diff": { "sha256": "<the sha256 of the diff text you were shown>" },
  "candidates": {
    "a": { "path": "<path as given in the task>", "sha256": "<candidate A sha256 from the task>" },
    "b": { "path": "<path as given in the task>", "sha256": "<candidate B sha256 from the task>" }
  },
  "types": [
    {
      "name": "Proposal",
      "disposition": "reconcile",
      "fields": { "id": "a", "title": "a", "reviewerBinding": "b" },
      "implements": { "Node": "a" },
      "reason": "one line: what this keeps and why"
    }
  ],
  "renames": [
    { "from": "ConfirmationInput", "to": "ConfirmationGateInput",
      "disposition": "rename", "reason": "one line" }
  ]
}
```

`fields` / `values` / `members` / `implements` is a map from a name to the
candidate that supplies it: `"a"` or `"b"`. There is no third option and no
implicit default.

## The rules, and the code each violation gets

**omit** — `omitted`
- every divergent type listed in the task must appear exactly once in `types`;
- every key of the merged type must appear in its map with a source;
- every rename pair proposed in the task must appear in `renames`;
- every entry needs a non-empty `reason`; `diff.sha256` is required.

**duplicate** — `duplicate`
- the same type name twice, the same rename pair twice, or a repeated JSON key
  anywhere in the document (the second value is not allowed to win).

**unknown** — `unknown`
- a type that is not divergent for these candidates (an only-A or only-B type
  needs no decision; its definition is kept as-is);
- a field, enum value, union member or interface that neither candidate has;
- a source other than `a` or `b`; a disposition outside
  `keep_a | keep_b | reconcile`; a rename pair that was not proposed for these
  candidates; a `contract` string that is not this contract.

**conflict** — `conflict`
- `keep_a` with any field taken from B (or `keep_b` with any field taken from A);
- `reconcile` that takes *everything* from one side — that is a whole-side pick,
  so declare `keep_a`/`keep_b` and say why;
- `reconcile` on a name the two candidates define with different kinds;
- two renames that share a `from` or a `to`, or a rename chain (`X -> Y` and
  `Y -> Z`), or a rename target that already exists as a type.

**stale** — `stale`
- `candidates.a.sha256` / `candidates.b.sha256` / `diff.sha256` do not match the
  bytes in front of the applier: the decision describes different content, so it
  is rejected rather than applied to the wrong schema.

## What the disposition means for the merged type

| disposition | merged type |
| --- | --- |
| `keep_a` | A's definition, verbatim. Its map must name exactly A's keys. |
| `keep_b` | B's definition, verbatim. Its map must name exactly B's keys. |
| `reconcile` | A superset: every key from A **and** B, each from the side the map names. Fields present on only one side are therefore kept, and a field on both sides keeps the shape the map names. |

`keep_a` / `keep_b` is the exception, not the default: it discards the other
side's fields, and the `reason` must say which structural difference makes
combination wrong.

## What is *not* read from the document

Prose. `reason` is stored, hashed and reported as evidence. It is never parsed
for intent — no "adopt B's fields", no "restore X from A". If a merge needs a
field from the other side, the map says so.

## Determinism

The same two candidate files and the same document produce byte-identical output
in any process and any directory: type order follows candidate A, then types
only in B in B's order; every map is applied by key, never by iteration order;
the JSON is canonicalized with sorted keys for hashing.
