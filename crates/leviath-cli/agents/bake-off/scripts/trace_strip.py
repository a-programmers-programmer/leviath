#!/usr/bin/env python3
"""Diagnostic: show which docstring/comment lines a strip pass removes (portable, GQL-09).

Run:  python3 scripts/trace_strip.py [schema.graphql]
Defaults to the in-repo seed resolved through harnessql_paths. No key file, no
absolute scratch path.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harnessql_paths
import graphql_diff
from graphql_diff import _strip_orphan_docstrings

seed_path = sys.argv[1] if len(sys.argv) > 1 else str(harnessql_paths.default_seed())
seed = open(seed_path).read()
result = _strip_orphan_docstrings(seed)
print(f"Seed: {seed_path} {len(seed)}B, After strip: {len(result)}B, "
      f"Removed: {len(seed)-len(result)}B")

lines_seed = seed.split('\n')
lines_res = result.split('\n')

removed_blocks = []
i_seed = 0
i_res = 0
while i_seed < len(lines_seed) and i_res < len(lines_res):
    if lines_seed[i_seed] == lines_res[i_res]:
        i_seed += 1
        i_res += 1
    else:
        block = []
        while i_seed < len(lines_seed) and (i_res >= len(lines_res)
                                            or lines_seed[i_seed] != lines_res[i_res]):
            block.append((i_seed + 1, lines_seed[i_seed]))
            i_seed += 1
        if block:
            removed_blocks.append(block)

print(f"\nRemoved blocks: {len(removed_blocks)}")
for i, block in enumerate(removed_blocks[:5]):
    print(f"\nBlock {i} ({len(block)} lines):")
    for ln, line in block[:8]:
        print(f"  L{ln}: {line}")
    if len(block) > 8:
        print(f"  ... ({len(block)-8} more lines)")
