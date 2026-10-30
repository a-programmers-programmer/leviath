import sys, time
sys.path.insert(0, '/data/work/leviath/.syncfin')
from gh import req, jobs

RIDS = [int(x) for x in sys.argv[1:]]
deadline = time.time() + 1500
while time.time() < deadline:
    done = 0
    for rid in RIDS:
        s, d = req(f'/actions/runs/{rid}')
        if isinstance(d, dict) and d.get('status') == 'completed':
            done += 1
    if done == len(RIDS):
        break
    time.sleep(30)

for rid in RIDS:
    s, d = req(f'/actions/runs/{rid}')
    print('===', rid, d['name'], '|', d['status'], '|', d['conclusion'], '|', d['head_sha'][:8], '|', d['html_url'])
    for j in jobs(rid):
        print('   %-46s | %-11s | %s' % (j['name'], j['status'], j['conclusion']))
    print()