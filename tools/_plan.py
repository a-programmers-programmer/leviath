import re,sys

ITEM=re.compile(r'^(?:(pub(?:\([a-z:()]+\))?)\s+)?(?:async\s+)?(fn|const|static|struct|enum|trait|type|impl|union)\b')

def parse(path):
    lines=open(path).read().split("\n")
    if lines and lines[-1]=="": lines=lines[:-1]
    teststart=next(i for i,l in enumerate(lines) if l.startswith("#[cfg(test)]"))
    items=[];i=0
    while i<teststart:
        m=ITEM.match(lines[i])
        if m:
            s=i
            while s-1>=0 and (lines[s-1].startswith("///") or lines[s-1].startswith("#[")): s-=1
            if lines[i].rstrip().endswith(";"): e=i
            else:
                d=0;e=i
                while e<teststart:
                    d+=lines[e].count("{")-lines[e].count("}")
                    st=lines[e].rstrip()
                    if d<=0 and (st.endswith("}") or st.endswith(");") or st.endswith("];")): break
                    e+=1
            kind=m.group(2)
            if kind=="impl":
                mm=re.match(r'^impl(?:<[^>]*>)?\s+(?:[A-Za-z_:<>, \'\[\];]+?\s+for\s+)?([A-Za-z_]\w*)',lines[i])
                name="impl:"+(mm.group(1) if mm else "?")
            else:
                mm=re.match(r'^(?:pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(?:fn|const|static|struct|enum|trait|type|union)\s+([A-Za-z_]\w*)',lines[i])
                name=mm.group(1) if mm else "?"
            vis=m.group(1) or "priv"
            items.append(dict(name=name,vis=vis,s=s,e=e,kind=kind))
            i=e+1;continue
        i+=1
    return lines,items,teststart

def analyze(path, move_groups):
    lines,items,teststart=parse(path)
    moved={n:g for g,ns in move_groups.items() for n in ns}
    byname={it["name"]:it for it in items}
    missing=[n for n in moved if n not in byname]
    if missing: print("MISSING",missing);sys.exit(1)
    testtxt="\n".join(lines[teststart:])
    def uses(name,text):
        return re.search(r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])',text) is not None
    print(f"### {path}")
    print(f"  production test-module starts L{teststart+1}; items={len(items)}")
    # 1. remaining parent items referencing moved names
    print("  -- REMAINING parent items that reference a moved name (needs re-export):")
    any_=False
    for it in items:
        if it["name"] in moved: continue
        body="\n".join(lines[it["s"]:it["e"]+1])
        hit=[n for n in moved if uses(n,body) and not n.startswith("impl:")]
        if hit:
            any_=True
            print(f"     {it['name']:34} -> {sorted(hit)}")
    if not any_: print("     (none)")
    print("  -- INLINE TEST module references a moved name (needs re-export):")
    for n in moved:
        if uses(n,testtxt): print(f"     tests -> {n}")
    print("  -- moved items referenced by ANOTHER moved item in a DIFFERENT group:")
    cross=False
    for n,g in moved.items():
        it=byname[n]; body="\n".join(lines[it["s"]:it["e"]+1])
        hit=[m2 for m2,g2 in moved.items() if g2!=g and m2!=n and uses(m2,body)]
        if hit: cross=True; print(f"     {n:34} ({g}) -> {sorted(hit)}")
    if not cross: print("     (none)")
    print("  -- moved items NOT visible outside their new module (vis=priv):")
    for n,g in moved.items():
        print(f"     {g:14} {n:34} {byname[n]['vis']:10} L{byname[n]['s']+1}-{byname[n]['e']+1} ({byname[n]['e']-byname[n]['s']+1} lines)")

analyze("crates/leviath-runtime/src/fanout.rs", {
 "framing":["FRAMED_PREVIOUS_ITEMS","FrameSplitRoundQuery","frame_split_round","split_round_framing"],
 "authoritative":["AuthoritativeFanOutPending","prepare_authoritative_fanouts","start_authoritative_fanouts"],
 "requests":["WorkItem","FanOutRequest","is_fan_out_tool","parse_fan_out_call","FanOutBudget","config_for","PendingFanOut","start_pending_fan_outs","begin_fan_out"],
})
analyze("crates/leviath-core/src/manifest/stage.rs", {
 "transitions":["parse_transitions","parse_transition_gate","parse_stuck_config"],
 "mode":["apply_stage_mode","fan_out_number"],
})
