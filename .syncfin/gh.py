import json, sys, urllib.request, urllib.error

TOK = open('/data/state/github_token_device.txt').read().strip()
BASE = 'https://api.github.com/repos/a-programmers-programmer/leviath'


def req(path, method='GET', body=None, accept='application/vnd.github+json'):
    url = path if path.startswith('http') else BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header('Authorization', 'Bearer ' + TOK)
    r.add_header('Accept', accept)
    r.add_header('X-GitHub-Api-Version', '2022-11-28')
    if data:
        r.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:800]


def runs(branch='sync/upstream-0.6.0', per_page=20):
    _, d = req(f'/actions/runs?branch={branch}&per_page={per_page}')
    return d.get('workflow_runs', [])


def jobs(run_id):
    _, d = req(f'/actions/runs/{run_id}/jobs?per_page=100')
    return d.get('jobs', [])


if __name__ == '__main__':
    print(req(sys.argv[1])[0])