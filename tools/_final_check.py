import re,sys
ITEM=re.compile(r'^(?:(pub(?:\([a-z:()]+\))?)\s+)?(?:async\s+)?(fn|const|static|struct|enum|trait|type|impl|union)\b')
def parse(path):
    lines=open(path).read().split("\n")
    if lines and lines[-1]=="": lines=lines[:-1]
    t=next(i for i,l in enumerate(lines) if l.startswith("#[cfg(test)]"))
    items=[];i=0
    while i<t:
        m=ITEM.match(lines[i])
        if m:
            s=i
            while s-1>=0 and (lines[s-1].startswith("///") or lines[s-1].startswith("#[")): s-=1
            if lines[i].rstrip().endswith(";"): e=i
            else:
                d=0;e=i
                while e<t:
                    d+=lines[e].count("{")-lines[e].count("}")
                    st=lines[e].rstrip()
                    if d<=0 and (st.endswith("}") or st.endswith(");") or st.endswith("];")): break
                    e+=1
            if m.group(2)=="impl":
                mm=re.match(r'^impl(?:<[^>]*>)?\s+(?:[A-Za-z_:<>, \'\[\];]+?\s+for\s+)?([A-Za-z_]\w*)',lines[i])
                name="impl:"+(mm.group(1) if mm else "?")
            else:
                mm=re.match(r'^(?:pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(?:fn|const|static|struct|enum|trait|type|union)\s+([A-Za-z_]\w*)',lines[i])
                name=mm.group(1) if mm else "?"
            items.append(dict(name=name,vis=m.group(1) or "priv",s=s,e=e))
            i=e+1;continue
        i+=1
    return lines,items,t
def uses(n,txt): return re.search(r'(?<![A-Za-z0-9_])'+re.escape(n)+r'(?![A-Za-z0-9_])',txt) is not None
def check(path,groups):
    lines,items,t=parse(path)
    by={it["name"]:it for it in items}
    moved={n:g for g,ns in groups.items() for n in ns}
    testtxt="\n".join(lines[t:])
    print(f"###### {path}")
    bad=[]
    # cross-group references
    for n,g in moved.items():
        body="\n".join(lines[by[n]["s"]:by[n]["e"]+1])
        for m2,g2 in moved.items():
            if m2==n or g2==g: continue
            if uses(m2,body):
                ok = by[m2]["vis"] in ("pub","pub(crate)","pub(super)")
                print(f"   cross {'OK ' if ok else 'BAD'} {g}:{n} -> {g2}:{m2} ({by[m2]['vis']})")
                if not ok: bad.append((n,m2))
    # staying items -> moved priv
    for it in items:
        if it["name"] in moved: continue
        body="\n".join(lines[it["s"]:it["e"]+1])
        for n,g in moved.items():
            if uses(n,body):
                tag="ok " if by[n]["vis"]!="priv" else "OK(child->parent priv)"
                if by[n]["vis"]=="priv":
                    print(f"   stays->movedpriv {'BAD' if False else 'OK'} {it['name']} -> {n} (child reads parent-private via use super::*)")
    # tests -> moved priv  (BAD: tests are in parent, cannot see child priv)
    for n,g in moved.items():
        if uses(n,testtxt):
            if by[n]["vis"]=="priv":
                print(f"   TESTS -> moved priv {n}  BAD")
                bad.append(("tests",n))
            else:
                print(f"   tests -> {n} ok ({by[n]['vis']})")
    # budget
    tot=0
    for g,ns in groups.items():
        c=sum(by[x]["e"]-by[x]["s"]+1 for x in ns); tot+=c
        print(f"   {g:14} {c:5d}")
    print(f"   MOVED {tot}, parent leaves {t-tot} production lines, limit 1200")
    print("   RESULT:", "PROBLEMS "+str(bad) if bad else "no blocking problems")
    print()
check("crates/leviath-runtime/src/fanout.rs",{
 "framing":["FRAMED_PREVIOUS_ITEMS","FrameSplitRoundQuery","frame_split_round","split_round_framing"],
 "authoritative":["AuthoritativeFanOutPending","prepare_authoritative_fanouts","start_authoritative_fanouts"],
 "requests":["FanOutRequest","is_fan_out_tool","parse_fan_out_call","config_for","PendingFanOut","start_pending_fan_outs","begin_fan_out"],
 "collect":["MAX_WORKER_STARTS_PER_PASS","fan_out_collect","MergedWorker","slim_merged_workers","finish_fan_out","finish_stage_fan_out","finish_tool_fan_out","leave_fan_out"],
})
check("crates/leviath-core/src/manifest/stage.rs",{
 "transitions":["parse_transitions","parse_transition_gate","parse_stuck_config"],
 "mode":["apply_stage_mode","fan_out_number"],
})
