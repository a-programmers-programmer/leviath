#!/usr/bin/env python3
"""Probe 2: SDL parse detail, gql-02 vs gql-03 diff, shared-checker features."""
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GQL02 = Path("/data/work/graphql-realization/gql-02/repo")
SRC = Path("/data/work/graphql-realization/gql-02")


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


out = {}
# 1. parse error detail for the worktree SDL
sdls = {
    "gql03": REPO / "docs/workflow/schema-workflow.graphql",
    "gql02": GQL02 / "docs/workflow/schema-workflow.graphql",
}
from graphql import parse

for name, path in sdls.items():
    try:
        doc = parse(path.read_text(encoding="utf-8"))
        out[name] = {"parse": "OK", "definitions": len(doc.definitions)}
    except Exception as exc:  # noqa: BLE001
        out[name] = {"parse": "FAIL", "error": str(exc).split("\n\n")[0]}
        msg = str(exc)
        m = re.search(r"GraphQL request:(\d+):(\d+)", msg)
        if m:
            line = int(m.group(1))
            text = path.read_text(encoding="utf-8").splitlines()
            lo, hi = max(0, line - 4), min(len(text), line + 2)
            out[name]["context"] = [f"{i+1}: {text[i]}" for i in range(lo, hi)]

# 2. gql-02 worktree cleanliness + SDL diff
out["gql02_status"] = sh(["git", "status", "--porcelain"], GQL02).stdout.splitlines()
out["gql03_status"] = sh(["git", "status", "--porcelain"], REPO).stdout.splitlines()
out["gql02_head"] = sh(["git", "rev-parse", "HEAD"], GQL02).stdout.strip()
out["sdl_same_as_gql02"] = (
    sdls["gql03"].read_bytes() == sdls["gql02"].read_bytes()
)
out["tracked_sdl_sha"] = sh(
    ["git", "show", "HEAD:docs/workflow/schema-workflow.graphql"], REPO
).stdout[:0] or sh(
    ["bash", "-lc", "git show HEAD:docs/workflow/schema-workflow.graphql | sha256sum"], REPO
).stdout.strip()

# 3. shared checker: does it know about confirmation/sensitive?
grep = sh(["grep", "-rn", "-i", "confirm", "python/leviath_reconcile"], GQL02).stdout.splitlines()
out["grep_confirmation"] = grep[:20]
grep2 = sh(["grep", "-rn", "-i", "sensitiv", "python/leviath_reconcile"], GQL02).stdout.splitlines()
out["grep_sensitive"] = grep2[:20]

# 4. invariants API surface
sys.path.insert(0, str(GQL02 / "python"))
import leviath_reconcile  # noqa: E402
from leviath_reconcile import invariants  # noqa: E402

out["invariants_public"] = sorted(
    n for n in dir(invariants) if not n.startswith("_")
)
out["reconcile_public"] = sorted(
    n for n in dir(leviath_reconcile.reconcile) if not n.startswith("_")
)[:60]
# 5. the real edge-invariant rules file on disk
rules_path = GQL02 / "docs/workflow/edge-invariant-rules.json"
out["rules_exists"] = rules_path.is_file()
if rules_path.is_file():
    out["rules"] = json.loads(rules_path.read_text(encoding="utf-8"))
out["gql02_docs_workflow"] = sorted(p.name for p in (GQL02 / "docs/workflow").iterdir())
out["gql02_root"] = sorted(p.name for p in SRC.iterdir())
json.dump(out, sys.stdout, indent=2)
sys.stdout.write("\n")