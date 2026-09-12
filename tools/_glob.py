import re,os,glob
# For each parent file with `mod C;` and a PRIVATE `use C::*;`, find sibling
# children that use `use super::*;` and reference names defined in C.
def items_in(f):
    out=set()
    for m in re.finditer(r'^(?:pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(fn|struct|enum|trait|type|const|static|union)\s+([A-Za-z_]\w*)', open(f).read(), re.M):
        out.add(m.group(2))
    return out
parents=glob.glob("crates/*/src/**/*.rs",recursive=True)
for p in parents:
    try: txt=open(p).read()
    except: continue
    privglobs=re.findall(r'^\s*use\s+([a-z_]\w*)::\*;', txt, re.M)
    pubglobs=re.findall(r'^\s*pub(?:\([a-z:()]+\))?\s+use\s+([a-z_]\w*)::\*;', txt, re.M)
    privglobs=[c for c in privglobs if c not in pubglobs]
    if not privglobs: continue
    d=os.path.dirname(p); base=os.path.basename(p)[:-3]
    # children live in d/base/ or as d/<child>.rs  (if p is mod.rs, siblings are in d)
    if base=="mod":
        sibdir=d; siblings=[f for f in glob.glob(sibdir+"/*.rs") if f!=p] + [os.path.join(x,"mod.rs") for x in glob.glob(sibdir+"/*") if os.path.isdir(x)]
    else:
        sibdir=os.path.join(d,base); siblings=[f for f in glob.glob(sibdir+"/*.rs")]
    for child in privglobs:
        cf=os.path.join(sibdir,child+".rs")
        cf2=os.path.join(sibdir,child,"mod.rs")
        cpath=cf if os.path.exists(cf) else (cf2 if os.path.exists(cf2) else None)
        if not cpath: continue
        citems=items_in(cpath)
        for s in siblings:
            if s==cpath or os.path.exists(s)==False: continue
            st=open(s).read()
            if "use super::*;" not in st: continue
            used=[n for n in citems if re.search(r'(?<![A-Za-z0-9_])'+re.escape(n)+r'(?![A-Za-z0-9_])', st)]
            if used:
                print(f"PRECEDENT: {p}\n   private use {child}::*;  sibling {s} via use super::* uses {used}")
