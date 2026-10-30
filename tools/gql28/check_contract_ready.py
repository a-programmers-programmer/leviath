#!/usr/bin/env python3
"""GQL-28 contract-readiness gate. Deterministic code, no model, no network.

This gate owns the bits and refuses everything else:

* Which source paths the contract is derived from must exist.
* The tRPC marker must be present in the product router files.
* A real contract artifact (a ``.graphql`` SDL file) must be observed on disk.
* Every observed artifact is hashed, so acceptance can pin a fingerprint.

The gate never says a contract is *good*. That is a judgment, and a model makes
it. The gate only says which of these facts are present or missing, and exits:

    0  ready    - every fact present; a contract artifact was observed
    2  blocked  - at least one fact is missing
    1  error    - the gate itself could not run (bad spec, bad args)

Usage:
    python3 check_contract_ready.py --root <dir> --spec <spec.json> [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

#: File names that mean "this repository uses tRPC".
TRPC_MARKERS = ("createTRPCRouter", "trpc", "initTRPC", "publicProcedure")

#: Glob patterns that mean "this is a graphql contract artifact".
CONTRACT_GLOBS = ("*.graphql", "*.graphqls", "**/*.graphql", "**/*.graphqls")

#: Directories never walked while looking for artifacts.
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}

#: Where a checkout of the spec's repository may sit, relative to --root.
CHECKOUT_CANDIDATES = ("parlour", "repo", ".")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_checkout(root: Path, spec: dict) -> tuple[Path | None, Path | None]:
    """Return (checkout, first existing source path) or (None, None)."""
    name = spec.get("repository", "").rsplit("/", 1)[-1]
    candidates = [name, *CHECKOUT_CANDIDATES]
    for cand in candidates:
        base = (root / cand).resolve() if cand != "." else root.resolve()
        for rel in spec.get("existing_paths", []):
            if (base / rel).exists():
                return base, base / rel
    return None, None


def walk_contracts(base: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in CONTRACT_GLOBS:
        for path in base.glob(pattern):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.is_file():
                found.add(path.resolve())
    return sorted(found)


def build_report(root: Path, spec: dict) -> dict:
    checks: list[dict] = []
    checkout, _first = find_checkout(root, spec)

    checks.append(
        {
            "name": "checkout",
            "state": "present" if checkout else "missing",
            "detail": str(checkout) if checkout else f"no checkout under {root}",
        }
    )

    base = checkout or root
    for rel in spec.get("existing_paths", []):
        path = base / rel
        checks.append(
            {
                "name": f"existing_path:{rel}",
                "state": "present" if path.exists() else "missing",
                "detail": str(path),
            }
        )

    # The tRPC marker must appear in at least one product router file.
    marker_hits: list[str] = []
    marker_files = [
        base / rel
        for rel in spec.get("existing_paths", [])
        if rel.endswith((".ts", ".tsx", ".js", ".mjs"))
    ]
    for path in marker_files:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(m in text for m in TRPC_MARKERS):
            marker_hits.append(str(path))
    checks.append(
        {
            "name": "trpc_markers",
            "state": "present" if marker_hits else "missing",
            "detail": ",".join(marker_hits) if marker_hits else "no tRPC marker in any router file",
        }
    )

    artifacts: list[dict] = []
    if checkout:
        for path in walk_contracts(checkout):
            artifacts.append(
                {
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    checks.append(
        {
            "name": "contract_artifact",
            "state": "present" if artifacts else "missing",
            "detail": artifacts[0]["path"] if artifacts else "no .graphql artifact observed",
        }
    )

    ready = all(c["state"] == "present" for c in checks)
    return {
        "node": spec.get("id", "unknown"),
        "repository": spec.get("repository"),
        "root": str(root),
        "checkout": str(checkout) if checkout else None,
        "state": "ready" if ready else "blocked",
        "contract_accepted": bool(ready and artifacts),
        "checks": checks,
        "contract_artifacts": artifacts,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="GQL-28 contract-readiness gate")
    parser.add_argument("--root", required=True, help="root to scan for a checkout")
    parser.add_argument("--spec", required=True, help="spec JSON describing the node")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    if not spec_path.is_file():
        print(f"error: spec not found: {spec_path}", file=sys.stderr)
        return 1
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: spec unreadable: {exc}", file=sys.stderr)
        return 1

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: root is not a directory: {root}", file=sys.stderr)
        return 1

    report = build_report(root, spec)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["state"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())