import sys, time
sys.path.insert(0, '/data/work/leviath/.syncfin')
from gh import req, runs

wf = sys.argv[1]            # workflow file name or id
ref = sys.argv[2]           # branch
body = None
if len(sys.argv) > 3 and sys.argv[3] != '-':
    import json
    body = json.loads(sys.argv[3])

before = {r['id'] for r in runs(branch=ref, per_page=20)}
s, d = req(f'/actions/workflows/{wf}/dispatches', method='POST',
           body=body if body is not None else {'ref': ref})
print('dispatch status', s, d)
if s not in (204, 200):
    sys.exit(1)
new = None
for _ in range(24):
    time.sleep(10)
    for r in runs(branch=ref, per_page=20):
        if r['id'] not in before and r['event'] == 'workflow_dispatch':
            new = r
            break
    if new:
        break
if new:
    print('NEW RUN', new['id'], new['name'], '|', new['status'], '|', new['head_sha'][:8], '|', new['html_url'])
else:
    print('could not identify new run')
