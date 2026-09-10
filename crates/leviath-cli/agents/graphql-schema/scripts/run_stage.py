"""Application pipeline entrypoint: run Leviath, then require a fresh accepted contract.

Run from an isolated application checkout. A successful exit is required before
backend/frontend workers start. This does not grant tool approvals or deploy code.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from gate import dispatch


def run(task, lev="lev", blueprint="graphql-schema"):
    root = Path("graphql-contract")
    if root.is_symlink():
        raise ValueError("Contract directory cannot be symlinked")
    receipt = root / "accepted.json"
    if receipt.is_symlink():
        raise ValueError("Receipt cannot be symlinked")
    if receipt.exists():
        receipt.unlink()
    # No global --model: preserve cheap workers and expensive design/review.
    completed = subprocess.run([lev, "run", blueprint, "--task", task, "--wait", "--json"],
                               stdout=sys.stderr, check=False)
    if completed.returncode:
        raise ValueError(f"Leviath run failed with exit {completed.returncode}")
    result = dispatch({"op": "verify"})
    if result["status"] != "accepted":
        raise ValueError("Contract did not pass acceptance: " + json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="Application brief or existing brief file path")
    parser.add_argument("--lev", default="lev")
    parser.add_argument("--blueprint", default="graphql-schema")
    args = parser.parse_args()
    try:
        result = run(args.task, args.lev, args.blueprint)
    except Exception as error:
        print(json.dumps({"status": "blocked", "errors": [str(error)]}))
        sys.exit(1)
    print(json.dumps(result, sort_keys=True))
