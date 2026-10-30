import subprocess, hashlib, os, json

AGENT = '/data/work/leviath/crates/leviath-cli/agents/graphql-schema/agent.leviath'
OUT = '/data/work/leviath/.syncfin/evidence.txt'
buf = []

def run(cmd):
    buf.append('$ ' + ' '.join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, cwd='/data/work/leviath')
    buf.append((p.stdout + p.stderr).rstrip('\n'))
    buf.append('EXIT=%d' % p.returncode)
    buf.append('')
    return p.stdout + p.stderr, p.returncode

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()

buf.append('### binary identities')
buf.append('/data/bin/lev -> %s' % os.readlink('/data/bin/lev'))
buf.append('sha256 /data/bin/lev-0.5.9-15dc182e = %s' % sha('/data/bin/lev-0.5.9-15dc182e'))
buf.append('sha256 /data/bin/lev-0.5.9-35210724 = %s' % sha('/data/bin/lev-0.5.9-35210724'))
buf.append('sha256 artifact tarball            = %s' % sha('/data/work/leviath/.syncfin/artifact/leviath-linux-x64.tar.gz'))
buf.append('')

buf.append('### BEFORE-install: the stale 0.5.0 the task described')
old_v, _ = run(['/usr/local/bin/lev', '--version'])
old_x, old_rc = run(['/usr/local/bin/lev', 'validate', AGENT])

buf.append('### AFTER-install: /data/bin/lev (the two required proofs)')
new_v, _ = run(['/data/bin/lev', '--version'])
new_x, new_rc = run(['/data/bin/lev', 'validate', AGENT])

buf.append('### verdict')
buf.append("stale error 'available_global_tools' with /usr/local/bin/lev 0.5.0 : %s" % ('available_global_tools' in old_x))
buf.append("stale error 'available_global_tools' with /data/bin/lev 0.5.9      : %s" % ('available_global_tools' in new_x))
buf.append('old validate exit=%d  new validate exit=%d' % (old_rc, new_rc))

open(OUT, 'w').write('\n'.join(buf) + '\n')
print('\n'.join(buf))