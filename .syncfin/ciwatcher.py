import sys, time
sys.path.insert(0, '/data/work/leviath/.syncfin')
from gh import runs, jobs, req

TIP = '15dc182e'
for r in runs(per_page=10):
    if r['head_sha'].startswith(TIP) or r['head_sha'] == TIP:
        print(r['id'], r['name'], r['status'], r['conclusion'], r['head_sha'][:8], r['html_url'])
        js = jobs(r['id'])
        print('  jobs:', len(js))
        for j in js:
            print('   %-46s | %-11s | %s' % (j['name'], j['status'], j['conclusion']))