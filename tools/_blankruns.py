for f in ["crates/leviath-runtime/src/fanout.rs","crates/leviath-core/src/manifest/stage.rs"]:
    lines=open(f).read().split("\n")
    runs=[];i=0
    while i<len(lines):
        if lines[i]=="":
            j=i
            while j<len(lines) and lines[j]=="": j+=1
            if j-i>1: runs.append((i+1,j-i))
            i=j
        else: i+=1
    print(f, "blank runs >1:", runs)
