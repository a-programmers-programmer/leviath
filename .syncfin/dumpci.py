import sys, json
sys.path.insert(0, '/data/work/leviath/.syncfin')
from gh import req, jobs

out = {}
for rid in (34666224646, 34666224645):
    s, d = req(f'/actions/runs/{rid}')
    out[rid] = {
        'name': d['name'], 'status': d['status'], 'conclusion': d['conclusion'],
        'sha': d['head_sha'], 'url': d['html_url'], 'created': d['created_at'],
        'event': d['event'],
        'jobs': [{'name': j['name'], 'status': j['status'],
                  'conclusion': j['conclusion'], 'id': j['id']} for j in jobs(rid)],
    }
json.dump(out, open('/data/work/leviath/.syncfin/ci_runs.json', 'w'), indent=2)
for rid, r in out.items():
    print('===', rid, r['name'], r['status'], r['conclusion'], r['sha'][:8], r['event'])
    for j in r['jobs']:
        print('   %-50s %s' % (j['name'], j['conclusion']))