import re,os,glob
ITEM=re.compile(r'^(?:(pub(?:\([a-z:()]+\))?)\s+)?(?:async\s+)?(fn|const|static|struct|enum|trait|type|impl|union)\b')
def priv_items(p):
    lines=open(p).read().split("\n"); out=set(); i=0
    while i<len(lines):
        if lines[i].startswith("#[cfg(test)]"): break
        m=ITEM.match(lines[i])
        if m:
            if m.group(1) is None:
                mm=re.match(r'^(?:async\s+)?(?:fn|const|static|struct|enum|trait|type|union)\s+([A-Za-z_]\w*)',lines[i])
                if mm: out.add(mm.group(1))
            e=i
            if not lines[i].rstrip().endswith(";"):
                d=0
                while e<len(lines):
                    d+=lines[e].count("{")-lines[e].count("}")
                    st=lines[e].rstrip()
                    if d<=0 and (st.endswith("}") or st.endswith(");") or st.endswith("];")): break
                    e+=1
            i=e+1;continue
        i+=1
    return out
hits=0
for p in glob.glob("crates/*/src/**/*.rs",recursive=True):
    d=os.path.dirname(p); base=os.path.basename(p)[:-3]
    sibdir = d if base=="mod" else os.path.join(d,base)
    if not os.path.isdir(sibdir): continue
    priv=priv_items(p)
    if not priv: continue
    for k in sorted(glob.glob(sibdir+"/*.rs")):
        if os.path.abspath(k)==os.path.abspath(p): continue
        kt=open(k).read()
        if "use super::*;" not in kt: continue
        used=sorted(n for n in priv if re.search(r'(?<![A-Za-z0-9_])'+re.escape(n)+r'(?![A-Za-z0-9_])',kt))
        if used:
            hits+=1
            if hits<=8:
                print(f"{p}\n   -> {k}\n      reaches private parent items: {used}")
print("total parent/child pairs relying on this:",hits)
