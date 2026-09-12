import re
def load(p):
    lines=open(p).read().split("\n")
    if lines and lines[-1]=="": lines=lines[:-1]
    return lines

def item_spans(lines, stop_at_cfgtest=True):
    import re
    ITEM=re.compile(r'^(pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(fn|const|static|struct|enum|trait|type|impl|union)\b')
    items=[];i=0;n=len(lines)
    while i<n:
        if stop_at_cfgtest and lines[i].startswith("#[cfg(test)]"): break
        if ITEM.match(lines[i]):
            s=i
            while s-1>=0 and (lines[s-1].startswith("///") or lines[s-1].startswith("#[")): s-=1
            if lines[i].rstrip().endswith(";"): e=i
            else:
                d=0;e=i
                while e<n:
                    d+=lines[e].count("{")-lines[e].count("}")
                    st=lines[e].rstrip()
                    if d<=0 and (st.endswith("}") or st.endswith(");") or st.endswith("];")): break
                    e+=1
            m=ITEM.match(lines[i])
            kind=m.group(2)
            if kind=="impl":
                mm=re.match(r'^impl(?:<[^>]*>)?\s+(?:[A-Za-z_:<>, \'\[\];]+?\s+for\s+)?([A-Za-z_]\w*)',lines[i])
                name="impl:"+(mm.group(1) if mm else "?")
            else:
                mm=re.match(r'^(?:pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(?:fn|const|static|struct|enum|trait|type|union)\s+([A-Za-z_]\w*)',lines[i])
                name=mm.group(1) if mm else "?"
            items.append((name,s,e,i));i=e+1;continue
        i+=1
    return items

def who(lines, items, target):
    """return list of item names whose span references target (excluding itself)"""
    out=[]
    for name,s,e,code in items:
        if name==target: continue
        body="\n".join(lines[s:e+1])
        if re.search(r'(?<![A-Za-z0-9_])'+re.escape(target)+r'(?![A-Za-z0-9_])',body): out.append(name)
    return out

for path, groups in [
  ("crates/leviath-runtime/src/fanout.rs", {
    "limits":["DEFAULT_FANOUT_DEPTH","run_tree_size","blueprint_fan_out_max_items"],
    "worker":["start_worker","child_output_content","worker_terminal_result","worker_requires_output"],
    "finish":["finish_fan_out","finish_stage_fan_out","finish_tool_fan_out","leave_fan_out"],
    "authoritative":["authoritative_items"],
  }),
  ("crates/leviath-core/src/manifest/stage.rs", {
    "overrides":["parse_tool_override"],
    "mode":["apply_stage_mode","fan_out_number"],
    "hooks":["parse_stage_hooks"],
    "transitions":["parse_transitions","parse_transition_gate","parse_stuck_config"],
  }),
]:
    lines=load(path);items=item_spans(lines)
    testtxt="\n".join(lines[items[-1][2]+1:])
    print("######",path)
    for g,names in groups.items():
        print(f"--- group {g}: {names}")
        for nm in names:
            it=next((x for x in items if x[0]==nm),None)
            if not it: print(f"    !! {nm} NOT FOUND"); continue
            refs_prod=[x for x in who(lines,items,nm)]
            refs_test=bool(re.search(r'(?<![A-Za-z0-9_])'+re.escape(nm)+r'(?![A-Za-z0-9_])',testtxt))
            outside=[r for r in refs_prod if r not in names]
            print(f"    {nm:32} prod-refs={outside if outside else '(same group only)'}  tests={refs_test}")
