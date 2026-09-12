#!/usr/bin/env python3
"""Validate that each planned group is reference-closed enough to move with
ZERO edits to the moved items.

Rules enforced:
  R1. A moved item that is private may have NO reference from outside its group
      (a private item in a child is invisible to the parent and to tests), unless
      the reference comes from a descendant of the child.
  R2. A moved item that is pub/pub(crate) is fine: the parent re-exports it.
  R3. No staying item may reference a moved private item.
"""
import re
import sys

ITEM = re.compile(r'^(?:(pub(?:\([a-z:()]+\))?)\s+)?(?:async\s+)?'
                  r'(fn|const|static|struct|enum|trait|type|impl|union)\b')


def parse(path):
    lines = open(path).read().split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    teststart = next(i for i, l in enumerate(lines) if l.startswith("#[cfg(test)]"))
    items, i = [], 0
    while i < teststart:
        m = ITEM.match(lines[i])
        if m:
            s = i
            while s - 1 >= 0 and (lines[s - 1].startswith("///") or lines[s - 1].startswith("#[")):
                s -= 1
            if lines[i].rstrip().endswith(";"):
                e = i
            else:
                depth, e = 0, i
                while e < teststart:
                    depth += lines[e].count("{") - lines[e].count("}")
                    st = lines[e].rstrip()
                    if depth <= 0 and (st.endswith("}") or st.endswith(");") or st.endswith("];")):
                        break
                    e += 1
            kind = m.group(2)
            if kind == "impl":
                mm = re.match(r'^impl(?:<[^>]*>)?\s+(?:[A-Za-z_:<>, \'\[\];]+?\s+for\s+)?([A-Za-z_]\w*)', lines[i])
                name = "impl:" + (mm.group(1) if mm else "?")
            else:
                mm = re.match(r'^(?:pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?'
                              r'(?:fn|const|static|struct|enum|trait|type|union)\s+([A-Za-z_]\w*)', lines[i])
                name = mm.group(1) if mm else "?"
            vis = m.group(1) or "priv"
            items.append(dict(name=name, vis=vis, s=s, e=e, kind=kind,
                              doc="\n".join(lines[s:i])))
            i = e + 1
            continue
        i += 1
    return lines, items, teststart


def uses(name, text):
    return re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', text) is not None


def check(path, groups):
    lines, items, teststart = parse(path)
    byname = {it["name"]: it for it in items}
    moved = {n: g for g, ns in groups.items() for n in ns}
    missing = [n for n in moved if n not in byname]
    if missing:
        sys.exit(f"{path}: not found: {missing}")
    testtxt = "\n".join(lines[teststart:])
    staying = [it for it in items if it["name"] not in moved]

    print(f"###### {path}   (production test-module at L{teststart+1}, {len(items)} items)")
    problems = []
    # R1: private moved items referenced from outside the group
    print("  [R1] private moved items, and every reference outside their own group:")
    for n, g in moved.items():
        it = byname[n]
        if it["vis"] != "priv":
            continue
        outside = []
        for other in items:
            if other["name"] == n or other["name"] in groups[g]:
                continue
            body = "\n".join(lines[other["s"]:other["e"] + 1])
            if uses(n, body):
                outside.append(f"item:{other['name']}({'moved:' + moved[other['name']] if other['name'] in moved else 'STAYS'})")
        if uses(n, testtxt):
            outside.append("inline-tests")
        print(f"      {'OK ' if not outside else 'BAD'} {n:34} {outside}")
        if outside:
            problems.append(f"R1 {n}: {outside}")
    # R3: staying items referencing moved names (fine if pub/pub(crate))
    print("  [R3] staying items that reference a moved name:")
    seen = False
    for it in staying:
        body = "\n".join(lines[it["s"]:it["e"] + 1])
        hit = [n for n in moved if uses(n, body)]
        if hit:
            seen = True
            bad = [n for n in hit if byname[n]["vis"] == "priv"]
            flag = "BAD " if bad else "OK  "
            print(f"      {flag}{it['name']:30} -> {sorted(hit)}" + (f"   PRIVATE:{bad}" if bad else ""))
            if bad:
                problems.append(f"R3 {it['name']} -> {bad}")
    if not seen:
        print("      (none)")
    # group-by-group line budget
    print("  line budget:")
    tot = 0
    for g, ns in groups.items():
        n = sum(byname[x]["e"] - byname[x]["s"] + 1 for x in ns)
        tot += n
        print(f"      {g:14} {n:5d} lines  ({len(ns)} items)")
    print(f"      {'MOVED':14} {tot:5d} lines")
    print(f"      {'LEAVES':14} {teststart - tot:5d} production lines in the parent")
    # intra-doc links in moved items that point at names left behind
    print("  [docs] intra-doc links inside moved items:")
    anylink = False
    for n in moved:
        it = byname[n]
        doc = "\n".join(lines[it["s"]:it["e"] + 1])
        for m in re.finditer(r'\[`?([A-Za-z_][A-Za-z0-9_]*)`?\]', doc):
            other = m.group(1)
            if other in byname and other not in moved:
                anylink = True
                print(f"      {n:30} -> [{other}] (stays in parent; reachable via `use super::*`)")
            elif other in byname and moved[other] != moved[n]:
                anylink = True
                print(f"      {n:30} -> [{other}] (other group {moved[other]}; reachable via parent's re-export)")
    if not anylink:
        print("      (none)")
    print()
    return problems


p = []
p += check("crates/leviath-runtime/src/fanout.rs", {
    "framing": ["FRAMED_PREVIOUS_ITEMS", "FrameSplitRoundQuery", "frame_split_round",
                "split_round_framing"],
    "authoritative": ["AuthoritativeFanOutPending", "prepare_authoritative_fanouts",
                      "start_authoritative_fanouts"],
    "requests": ["FanOutRequest", "is_fan_out_tool", "parse_fan_out_call", "config_for",
                 "PendingFanOut", "start_pending_fan_outs", "begin_fan_out"],
    "collect": ["MAX_WORKER_STARTS_PER_PASS", "fan_out_collect", "MergedWorker",
                "slim_merged_workers", "finish_fan_out", "finish_stage_fan_out",
                "finish_tool_fan_out", "leave_fan_out"],
})
p += check("crates/leviath-core/src/manifest/stage.rs", {
    "transitions": ["parse_transitions", "parse_transition_gate", "parse_stuck_config"],
    "mode": ["apply_stage_mode", "fan_out_number"],
})
print("PROBLEMS:", problems if (problems := p) else "none")
