import subprocess, os

ART = '/data/work/leviath/.syncfin/artifact/x/lev'
AGENT = '/data/work/leviath/crates/leviath-cli/agents/graphql-schema/agent.leviath'

def run(cmd, label):
    print('$ ' + ' '.join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, cwd='/data/work/leviath')
    out = (p.stdout + p.stderr)
    print(out)
    print('EXIT=%d' % p.returncode)
    print('=' * 70)
    return out, p.returncode

res = {}
res['old_version'], _ = run(['/usr/local/bin/lev', '--version'], 'old version')
res['old_validate'], rc_old = run(['/usr/local/bin/lev', 'validate', AGENT], 'old validate')
res['new_version'], _ = run([ART, '--version'], 'new version')
res['new_validate'], rc_new = run([ART, 'validate', AGENT], 'new validate')

print('OLD stale error present? ', 'available_global_tools' in res['old_validate'])
print('NEW stale error present? ', 'available_global_tools' in res['new_validate'])
print('old validate exit', rc_old, 'new validate exit', rc_new)