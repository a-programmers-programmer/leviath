import re,sys
src=open("crates/leviath-runtime/src/fanout.rs").read()
lines=src.split("\n")
testline=next(i for i,l in enumerate(lines,1) if l=="#[cfg(test)]")
items=[];i=0
while i<len(lines):
    l=lines[i]
    if l.startswith("#[cfg(test)]"): break
    if l.startswith("///") or l.startswith("#["):
        k=i
        while k<len(lines) and (lines[k].startswith("///") or lines[k].startswith("#[")): k+=1
        code=lines[k] if k<len(lines) else ""
        m=re.match(r'^(pub(\([a-z:()]+\))?\s+)?(async\s+)?(const|static|fn|struct|enum|trait|type|impl)\b',code)
        if m:
            d=0;e=k
            while e<len(lines):
                d+=lines[e].count("{")-lines[e].count("}")
                if d<=0 and (lines[e].rstrip().endswith(";") or lines[e].rstrip().endswith("}") or lines[e].strip()=="}"): break
                e+=1
            kind=m.group(4)
            nm=re.match(r'^(?:pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(?:const|static|fn|struct|enum|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*)',code)
            name=nm.group(1) if nm else None
            if kind=="impl":
                nm2=re.match(r'^impl(?:<[^>]*>)?\s+(?:[A-Za-z_:<>, \'\[\];]+?\s+for\s+)?([A-Za-z_][A-Za-z0-9_]*)',code)
                name="impl:"+(nm2.group(1) if nm2 else "?")
            vis="pub" if re.match(r'^pub\s',code) else ("pubcrate" if code.startswith("pub(crate)") else ("pubsuper" if code.startswith("pub(super)") else "priv"))
            items.append(dict(name=name,vis=vis,kind=kind,s=i+1,e=e+1))
            i=e+1;continue
    i+=1
testtxt="\n".join(lines[testline-1:])
def R(n,t): return re.search(r'(?<![A-Za-z0-9_])'+re.escape(n)+r'(?![A-Za-z0-9_])',t) is not None
def base(n): return n[5:] if n.startswith("impl:") else n
adj={}
for it in items:
    body="\n".join(lines[it["s"]-1:it["e"]])
    adj[it["name"]]=[o["name"] for o in items if o is not it and R(base(o["name"]),body)]
adj["TESTS"]=[it["name"] for it in items if R(base(it["name"]),testtxt)]
index={};low={};onstk={};stk=[];comps=[];c=[0]
def sc(v):
    index[v]=low[v]=c[0];c[0]+=1;stk.append(v);onstk[v]=True
    for w in adj.get(v,[]):
        if w not in index: sc(w);low[v]=min(low[v],low[w])
        elif onstk.get(w): low[v]=min(low[v],index[w])
    if low[v]==index[v]:
        cc=[]
        while True:
            w=stk.pop();onstk[w]=False;cc.append(w)
            if w==v: break
        comps.append(cc)
for v in list(adj):
    if v not in index: sc(v)
def size(x):
    for it in items:
        if it["name"]==x: return it["e"]-it["s"]+1
    return 0
def start(x):
    for it in items:
        if it["name"]==x: return it["s"]
    return 0
comps.sort(key=lambda cc:-sum(size(x) for x in cc))
for cc in comps:
    tot=sum(size(x) for x in cc)
    tag="  <<< CONTAINS TESTS" if "TESTS" in cc else ""
    print(f"[{tot:4d} lines]{tag}")
    for x in sorted(cc,key=start):
        v=next(it["vis"] for it in items if it["name"]==x)
        print(f"      L{start(x):5d} {v:9} {x}")
