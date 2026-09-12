import sys
sys.path.insert(0, '/data/work/leviath/study')
from graphql_diff import diff_types, apply_merge, parse_schema
from graphql_diff import _strip_duplicate_defs, _strip_orphan_docstrings

seed = open('/data/work/leviath/study/B2.graphql').read()
print("Seed:", len(seed), "bytes")

diff = diff_types(seed, seed)
merged = apply_merge(seed, seed, [], renames={}, detected_renames={})
print("apply_merge:", len(merged), "bytes")
print("Lost:", len(seed) - len(merged), "bytes")

# Now trace the internal steps
a_types = parse_schema(seed)
sorted_a = sorted(a_types, key=lambda t: t.start)

parts = []
last = 0
for t in sorted_a:
    pref = seed[last:t.start]
    parts.append(pref)
    parts.append(t.body)
    last = t.end
parts.append(seed[last:])
raw = ''.join(parts)
print("Raw before dedup:", len(raw), "bytes")

deduped = _strip_duplicate_defs(raw)
print("After dedup:", len(deduped), "bytes")

orphaned = _strip_orphan_docstrings(deduped)
print("After orphan:", len(orphaned), "bytes")

# The gap is in dedup or orphan stripping
# Check what duplicate_defs removes
import re
lines_raw = raw.split('\n')
lines_dedup = deduped.split('\n')
diff_lines = set(lines_raw) - set(lines_dedup)
print("Lines removed by dedup:")
for l in sorted(list(diff_lines))[:15]:
    if l.strip():
        print("  ", l[:80])