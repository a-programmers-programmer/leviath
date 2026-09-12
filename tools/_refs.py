import re,sys
path=sys.argv[1]
lines=open(path).read().split("\n")
if lines and lines[-1]=="": lines=lines[:-1]
ITEM=re.compile(r'^(pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(fn|const|static|struct|enum|trait|type|impl|union)\b')
items=[];i=0;n=len(lines)
while i<n:
    if lines[i].startswith("#[cfg(test)]"): break
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
        m=ITEM.match(lines[i]);kind=m.group(2)
        if kind=="impl":
            mm=re.match(r'^impl(?:<[^>]*>)?\s+(?:[A-Za-z_:<>, \'\[\];]+?\s+for\s+)?([A-Za-z_]\w*)',lines[i])
            name="impl:"+(mm.group(1) if mm else "?")
        else:
            mm=re.match(r'^(?:pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(?:fn|const|static|struct|enum|trait|type|union)\s+([A-Za-z_]\w*)',lines[i])
            name=mm.group(1) if mm else "?"
        txt=lines[i]
        vis="pub" if re.match(r'^pub\s',txt) else ("pub(crate)" if txt.startswith("pub(crate)") else ("pub(super)" if txt.startswith("pub(super)") else "priv"))
        items.append(dict(name=name,vis=vis,s=s,e=e))
        i=e+1;continue
    i+=1
teststart=next(i for i,l in enumerate(lines) if l.startswith("#[cfg(test)]"))
testtxt="\n".join(lines[teststart:])
def R(n,t): return re.search(r'(?<![A-Za-z0-9_])'+re.escape(n)+r'(?![A-Za-z0-9_])',t) is not None
print(f"### {path}: tests reference these items")
for it in items:
    base=it["name"][5:] if it["name"].startswith("impl:") else it["name"]
    if R(base,testtxt):
        print(f"   {it['vis']:10} {it['name']}")
