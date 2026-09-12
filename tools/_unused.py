import re,sys,os,glob
ITEM=re.compile(r'^(pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(fn|const|static|struct|enum|trait|type|impl|union)\b')
def spans(lines):
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
            items.append((s,e));i=e+1;continue
        i+=1
    return items
def check(parent):
    lines=open(parent).read().split("\n")
    sp=spans(lines)
    body="\n".join("\n".join(lines[s:e+1]) for s,e in sp)
    imports=[]
    for m in re.finditer(r'^use\s+([^;]+);', "\n".join(lines), re.M):
        txt=m.group(1)
        # collect imported names
        names=[]
        for part in re.findall(r'\{([^}]*)\}', txt):
            names += [x.strip() for x in part.split(",") if x.strip() and " as " not in x]
        head=re.sub(r'\{[^}]*\}','',txt).strip().strip(',').split()
        for tok in head:
            if tok and tok!="use" and tok not in ("crate","std","super","self") and not tok.startswith("::"):
                if re.fullmatch(r'[A-Za-z_]\w*', tok): names.append(tok)
        for n in names:
            n=n.split("::")[-1]
            if not re.fullmatch(r'[A-Za-z_]\w*', n): continue
            used_in_parent = re.search(r'(?<![A-Za-z0-9_:])'+re.escape(n)+r'(?![A-Za-z0-9_])', body) is not None
            imports.append((n,used_in_parent))
    # children
    d=os.path.dirname(parent); base=os.path.basename(parent)[:-3]
    sibdir = d if base=="mod" else os.path.join(d,base)
    kids=[]
    if os.path.isdir(sibdir):
        kids=[os.path.join(sibdir,f) for f in os.listdir(sibdir) if f.endswith(".rs")]
    orphan=[]
    for n,used in imports:
        if used: continue
        in_kids=[os.path.basename(k) for k in kids if re.search(r'(?<![A-Za-z0-9_])'+re.escape(n)+r'(?![A-Za-z0-9_])', open(k).read())] if kids else []
        orphan.append((n,in_kids))
    print(f"--- {parent}: imports never used by a parent item ({len(orphan)}) [name -> children that use it]")
    for n,k in orphan: print(f"      {n:28} {k}")

for p in ["crates/leviath-runtime/src/pipeline/mod.rs","crates/leviath-core/src/manifest.rs","crates/leviath-runtime/src/host/mod.rs","crates/leviath-cli/src/config/mod.rs","crates/leviath-providers/src/provider.rs","crates/leviath-runtime/src/fanout.rs","crates/leviath-core/src/manifest/stage.rs"]:
    check(p)
