import sys, os, zipfile, http.client, urllib.request
sys.path.insert(0, '/data/work/leviath/.syncfin')
from gh import req

TOK = open('/data/state/github_token_device.txt').read().strip()
RUN = 34667066022
OUT = '/data/work/leviath/.syncfin/artifact'
os.makedirs(OUT, exist_ok=True)

s, d = req(f'/actions/runs/{RUN}/artifacts')
arts = d.get('artifacts', [])
aid = [a for a in arts if a['name'] == 'leviath-linux-x64'][0]['id']
url = f'https://api.github.com/repos/a-programmers-programmer/leviath/actions/artifacts/{aid}/zip'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


op = urllib.request.build_opener(NoRedirect)
r = urllib.request.Request(url)
r.add_header('Authorization', 'Bearer ' + TOK)
try:
    op.open(r)
    raise SystemExit('expected a redirect')
except urllib.error.HTTPError as e:
    if e.code not in (301, 302, 303, 307, 308):
        raise
    loc = e.headers['Location']
    print('redirected to', loc.split('?')[0])

# The storage URL is pre-signed; sending the API token to it is what 401s.
r2 = urllib.request.Request(loc)
zpath = os.path.join(OUT, 'leviath-linux-x64.zip')
with urllib.request.urlopen(r2) as resp, open(zpath, 'wb') as f:
    f.write(resp.read())
print('downloaded', zpath, os.path.getsize(zpath), 'bytes')
with zipfile.ZipFile(zpath) as z:
    print('zip entries:', z.namelist())
    z.extractall(OUT)
for root, dirs, files in os.walk(OUT):
    for fn in files:
        p = os.path.join(root, fn)
        print('  ', p, os.path.getsize(p))