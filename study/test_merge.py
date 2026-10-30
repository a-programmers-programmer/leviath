#!/usr/bin/env python3
"""Test the parse gate and validate merge on the seed."""
import sys
sys.path.insert(0, '/data/work/leviath/study')

from graphql import build_schema, GraphQLSyntaxError
from graphql_diff import diff_types, apply_merge

# Load seed
seed_text = open('/data/work/leviath/study/B2.graphql').read()

# Self-diff
diff_self = diff_types(seed_text, seed_text)
print(f"Self-diff: {len(diff_self.only_a)} only-A, {len(diff_self.only_b)} only-B, "
      f"{len(diff_self.both)} identical, {len(diff_self.divergent)} divergent")

# Self-merge (keep_b on all divergent — should be identity)
decisions = [(name, 'keep_b') for name in diff_self.divergent]
merged = apply_merge(seed_text, seed_text, decisions, renames={}, detected_renames={})
print(f"Merged self: {len(merged)}B (seed: {len(seed_text)}B)")

# Parse the self-merge
try:
    build_schema(merged)
    print("SELF-MERGE PARSE: OK")
except Exception as e:
    print(f"SELF-MERGE PARSE FAIL: {e}")

# Check for the specific bugs: duplicate enum ProposalInputRule, orphan | lines
lines = merged.split('\n')

# Duplicate enum check
dup_enums = []
for i, line in enumerate(lines):
    if line.strip().startswith('enum ProposalInputRule'):
        dup_enums.append((i + 1, line))
print(f"\nDuplicate 'enum ProposalInputRule' entries: {len(dup_enums)}")
for ln, line in dup_enums:
    print(f"  L{ln}: {line}")

# Orphan | check
orphans = []
in_union = False
for i, line in enumerate(lines):
    t = line.strip()
    if t.startswith('union '):
        in_union = True
        continue
    if t.startswith('|'):
        if not in_union:
            orphans.append((i + 1, t))
    if t.startswith('type ') or t.startswith('enum ') or t.startswith('input ') or \
       t.startswith('scalar ') or t.startswith('interface '):
        in_union = False
print(f"Orphan '|' lines: {len(orphans)}")
for ln, line in orphans[:5]:
    print(f"  L{ln}: {line}")

# Check ConfirmationInput vs ConfirmationGateInput both existing
conf_a = any('ConfirmationInput' in l for l in lines)
conf_b = any('ConfirmationGateInput' in l for l in lines)
print(f"ConfirmationInput present: {conf_a}, ConfirmationGateInput present: {conf_b}")