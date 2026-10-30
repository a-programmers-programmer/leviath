import sys, re
sys.path.insert(0, '/data/work/leviath/study')
from graphql_diff import _strip_orphan_docstrings

seed = open('/data/work/leviath/study/B2.graphql').read()
result = _strip_orphan_docstrings(seed)
print(f"Seed: {len(seed)}B, After strip: {len(result)}B, Removed: {len(seed)-len(result)}B")

# Find first few removed docstrings
lines_seed = seed.split('\n')
lines_res = result.split('\n')

# Compare line by line
removed_blocks = []
i_seed = 0
i_res = 0
while i_seed < len(lines_seed) and i_res < len(lines_res):
    if lines_seed[i_seed] == lines_res[i_res]:
        i_seed += 1
        i_res += 1
    else:
        # Lines differ - capture the removed block
        block = []
        while i_seed < len(lines_seed) and (i_res >= len(lines_res) or lines_seed[i_seed] != lines_res[i_res]):
            block.append((i_seed+1, lines_seed[i_seed]))
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