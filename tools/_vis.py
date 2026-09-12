import re,os,glob
# map: module path -> file, for crates/*/src
def modfile(root):
    # returns dict modname->path for a crate src dir
    pass
files=[p for p in glob.glob("crates/*/src/**/*.rs",recursive=True)]
# collect declared visibility of every named item, per file
decl={}
for f in files:
    for m in re.finditer(r'^(pub(?:\([a-z:()]+\))?\s+)?(?:async\s+)?(fn|struct|enum|trait|type|const|static|union)\s+([A-Za-z_]\w*)', open(f).read(), re.M):
        vis = m.group(1).strip() if m.group(1) else "priv"
        decl.setdefault(f,{})[m.group(3)]=vis
# find re-exports: use <path>::<Name>;
rx=re.compile(r'^(pub(?:\([a-z:()]+\))?)\s+use\s+((?:[a-z_][\w]*::)*)([A-Za-z_]\w*)\s*;', re.M)
found=[]
for f in files:
    for m in rx.finditer(open(f).read()):
        vis=m.group(1); path=m.group(2); name=m.group(3)
        if not path: continue
        # only sibling/local (single segment) re-exports
        segs=path.rstrip(':').split('::')
        if len(segs)!=1: continue
        child=segs[0]
        cand=os.path.join(os.path.dirname(f), child+".rs")
        cand2=os.path.join(os.path.dirname(f), child, "mod.rs")
        for c in (cand,cand2):
            if os.path.exists(c) and name in decl.get(c,{}):
                found.append((vis, decl[c][name], f, child, name))
print("=== re-exports where item vis != re-export vis ===")
for vis,ivis,f,child,name in found:
    if vis!=ivis:
        print(f"  reexport={vis:12} item={ivis:10} {f}  -> {child}::{name}")
print("=== counts ===")
from collections import Counter
print(Counter((v,i) for v,i,_,_,_ in found))
