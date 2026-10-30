#!/usr/bin/env python3
"""Portable path resolution for the HarnessQL bake-off lanes (work order GQL-09).

Before GQL-09 every driver hard-coded `/data/work/leviath/study`,
`/data/work/leviath/crates/...` and read the model key out of a plaintext key
file under `/data/state`. None of that survives a fresh install, and the
key read was a secret by path. This module is the single place that
answers "where is the guide / the seed / the workdir / the `lev` binary", and it
answers from the agent directory itself, so an install anywhere works.

Resolution order for every path:

  1. the explicit `HARNESSQL_*` environment override, if set;
  2. the agent's own directory (this file's parent's parent);
  3. for the workdir only, a scratch-free default of `<agent>/work` .

The legacy scratch layout is *available* but never a default: set
`HARNESSQL_LEGACY_STUDY=/data/work/leviath/study` to keep an old checkout
working unchanged. Nothing here reads a key file: `lev` owns its own secrets and
the driver never injects one.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# `<agent>/scripts/harnessql_paths.py` -> `<agent>`
_THIS_FILE = Path(__file__).resolve()
_DEFAULT_AGENT_DIR = _THIS_FILE.parent.parent


class PathError(RuntimeError):
    """A required input is missing; the caller must report `blocked`, not guess."""


def agent_dir() -> Path:
    """The bake-off agent directory (the one holding `agent.leviath`)."""
    raw = os.environ.get("HARNESSQL_AGENT_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return _DEFAULT_AGENT_DIR


def bake_merge_dir() -> Path:
    """The bake-merge agent directory, a sibling of this agent."""
    raw = os.environ.get("HARNESSQL_BAKE_MERGE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return agent_dir().parent / "bake-merge"


def legacy_study() -> Path | None:
    """The pre-GQL-09 scratch study directory, only when explicitly opted in."""
    raw = os.environ.get("HARNESSQL_LEGACY_STUDY")
    return Path(raw).expanduser().resolve() if raw else None


def guide_path() -> Path:
    """The Composition Guide as a file (the lanes read it through a tool instead)."""
    return agent_dir() / "references" / "composition-guide.md"


def merge_format_path() -> Path:
    return bake_merge_dir() / "references" / "merge-decision-format.md"


def default_seed() -> Path:
    """The seed schema. In-repo by default, legacy scratch only if asked for."""
    raw = os.environ.get("HARNESSQL_SEED")
    if raw:
        return Path(raw).expanduser().resolve()
    legacy = legacy_study()
    if legacy and (legacy / "B2.graphql").is_file():
        return legacy / "B2.graphql"
    return agent_dir() / "study" / "B2.graphql"


def workdir() -> Path:
    """Where a run's file tools are confined, and where evidence is written."""
    raw = os.environ.get("HARNESSQL_WORKDIR")
    if raw:
        return Path(raw).expanduser().resolve()
    legacy = legacy_study()
    if legacy and (legacy / "bakeoff").is_dir():
        return (legacy / "bakeoff").resolve()
    return agent_dir() / "work"


def evidence_dir() -> Path:
    raw = os.environ.get("HARNESSQL_EVIDENCE_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return workdir() / "evidence"


def lev_bin() -> str:
    """The `lev` binary: explicit override, PATH, then the box's usual location."""
    raw = os.environ.get("HARNESSQL_LEV") or os.environ.get("LEV_BIN")
    if raw:
        return raw
    found = shutil.which("lev")
    if found:
        return found
    for candidate in ("/data/bin/lev", "/usr/local/bin/lev"):
        if os.access(candidate, os.X_OK):
            return candidate
    raise PathError(
        "no `lev` binary found: put it on PATH, or set HARNESSQL_LEV / LEV_BIN"
    )


def agent_path(name: str) -> str:
    """Resolve a lane name to the in-repo blueprint PATH, never a bare name.

    `lev run <name>` resolves a bare name against the INSTALLED agent directory
    (`/data/.leviath/agents/<name>`), which on this box still holds the old,
    permissive `bake-merge` v0.1.0: `[read_paths] allow = ["~/", ...]` and the
    tools `read_file`, `list_dir`, `write_file`. Dispatching that would hand the
    merge reviewer exactly the raw-candidate read and full-schema write the
    definition of done forbids. So the driver always passes the PATH of the
    in-repo blueprint, which is the restricted v0.2.0. An already-absolute path
    (or one that exists on disk) is returned unchanged.
    """
    if os.path.isabs(name) or os.path.exists(name):
        return name
    if name == "bake-merge":
        return str(bake_merge_dir())
    if name == "bake-off":
        return str(agent_dir())
    return name


def script_dir() -> Path:
    return _THIS_FILE.parent


def sdk_dir() -> Path | None:
    """The optional Python SDK (`import leviath`), when this install ships one.

    `HARNESSQL_SDK_DIR` names it explicitly; otherwise a sibling `python/` of the
    repo root is used if it has the package. `None` means "not available", and a
    driver that needs it reports `blocked` rather than guessing a scratch path.
    """
    raw = os.environ.get("HARNESSQL_SDK_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    for base in (agent_dir().parents[2] if len(agent_dir().parents) > 2 else agent_dir(),
                 agent_dir().parent):
        candidate = base / "python"
        if (candidate / "leviath" / "__init__.py").is_file():
            return candidate
    return None


def ensure_on_path() -> None:
    """Make the sibling modules importable however the script was launched."""
    here = str(script_dir())
    if here not in sys.path:
        sys.path.insert(0, here)


def summary() -> dict:
    """A JSON-able description of every resolved path, for the evidence log."""
    return {
        "agent_dir": str(agent_dir()),
        "bake_merge_dir": str(bake_merge_dir()),
        "guide": str(guide_path()),
        "merge_format": str(merge_format_path()),
        "seed": str(default_seed()),
        "workdir": str(workdir()),
        "evidence_dir": str(evidence_dir()),
        "lev": lev_bin(),
        "sdk_dir": str(sdk_dir()) if sdk_dir() else None,
        "legacy_study": str(legacy_study()) if legacy_study() else None,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(summary(), indent=2, sort_keys=True))
