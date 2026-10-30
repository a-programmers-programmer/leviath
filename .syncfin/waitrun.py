import sys, time
sys.path.insert(0, '/data/work/leviath/.syncfin')
from gh import req, jobs

RUN = int(sys.argv[1])
deadline = time.time() + float(sys.argv[2] if len(sys.argv) > 2 else 600)
while time.time() < deadline:
    s, d = req(f'/actions/runs/{RUN}')
    if isinstance(d, dict) and d.get('status') == 'completed':
        break
    time.sleep(20)
s, d = req(f'/actions/runs/{RUN}')
print('RUN', RUN, d['name'], '|', d['status'], '|', d['conclusion'], '|', d['head_sha'][:8])
js = jobs(RUN)
print('jobs:', len(js))
for j in js:
    print('  %-46s | %-11s | %s' % (j['name'], j['status'], j['conclusion']))