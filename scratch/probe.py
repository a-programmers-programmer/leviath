#!/usr/bin/env python3
"""Fact probe: report repo state + SDL parse status as JSON (no side effects)."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GQL02 = Path("/data/work/graphql-realization/gql-02/repo")


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.strip()


def parse_status(path):
    try:
        from graphql import parse

        parse(Path(path).read_text(encoding="utf-8"))
        return "OK"
    except Exception as exc:  # noqa: BLE001
        return f"FAIL {type(exc).__name__}: {exc}"


out = {
    "repo": str(REPO),
    "head": git("rev-parse", "HEAD"),
    "log": git("log", "--oneline", "-3").splitlines(),
    "status": git("status", "--porcelain").splitlines(),
    "docs_workflow_files": sorted(p.name for p in (REPO / "docs/workflow").iterdir()),
    "docs_workflow_git_ls": git("ls-files", "docs/workflow").splitlines(),
    "python_git_ls": git("ls-files", "python").splitlines(),
    "python_dir": sorted(str(p.relative_to(REPO)) for p in (REPO / "python").iterdir()),
    "worktree_sdl_parse": parse_status(REPO / "docs/workflow/schema-workflow.graphql"),
    "worktree_sdl_sha": subprocess.run(
        ["sha256sum", "docs/workflow/schema-workflow.graphql"], cwd=REPO,
        capture_output=True, text=True,
    ).stdout.split()[0],
    "gql02_exists": GQL02.is_dir(),
    "gql02_python_packages": sorted(
        p.name for p in (GQL02 / "python").iterdir()
    ) if (GQL02 / "python").is_dir() else [],
    "gql02_sdl_parse": parse_status(GQL02 / "docs/workflow/schema-workflow.graphql"),
    "workdir_root": sorted(p.name for p in Path("/data/work/graphql-realization/gql-03").iterdir()),
    "workdir_evidence": sorted(
        p.name for p in Path("/data/work/graphql-realization/gql-03/evidence").iterdir()
    ),
    "graphql_core": None,
}
try:
    import graphql

    out["graphql_core"] = graphql.version
except Exception as exc:  # noqa: BLE001
    out["graphql_core"] = f"MISSING {exc}"

json.dump(out, sys.stdout, indent=2)
sys.stdout.write("\n")