#!/usr/bin/env python3
"""Offline tests for the REAL lane dispatch path (DIRECTIVE 0184z, GQL-09 residual).

Run:  python3 scripts/test_lane_dispatch.py

The GQL-09 report recorded `lane_dispatch` as blocked and the dispatch path as
UNVERIFIED. This suite closes the verification gap that matters most: WHICH
blueprint a real `lev run` dispatches. `harnessql_driver.lev_run()` used to pass
the bare agent NAME ("bake-merge"), and a bare name resolves to
`/data/.leviath/agents/bake-merge/agent.leviath` - the OLD INSTALLED v0.1.0
blueprint, whose `[read_paths] allow = ["~/", ...]` and whose tools include
`read_file`, `list_dir` and `write_file`. That is exactly the permissive
reviewer the definition of done forbids.

These checks stub `subprocess.run`, so they call no model and cost nothing:

  T1 a dispatch names the RESTRICTED in-repo blueprint by PATH, not by bare name;
  T2 that blueprint really is restricted (empty read_paths, no file tools);
  T3 the conductor's own lane constants resolve to those same paths;
  T4 `lev_run` still parses `lev run --json` and returns the run id.

Nothing here touches the network, and nothing here reads a secret.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name} {detail}")


def capture_dispatch(agent: str, tmp: Path) -> list:
    """Call driver.lev_run with subprocess.run stubbed; return the argv it built."""
    import harnessql_driver as driver

    captured: dict = {}

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"run_id": "run-stub-1", "status": "COMPLETE",
                             "final_output": "OK"})
        stderr = ""

    def fake_run(cmd, capture_output=True, text=True, timeout=None):
        captured["cmd"] = list(cmd)
        return FakeProc()

    old = driver.subprocess.run
    driver.subprocess.run = fake_run
    try:
        result = driver.lev_run(agent, "task text", tmp)
    finally:
        driver.subprocess.run = old
    captured["result"] = result
    return captured


def test_dispatch_uses_repo_path(tmp: Path) -> None:
    print("\n[T1] a dispatch names the restricted in-repo blueprint by path")
    import harnessql_paths

    cap = capture_dispatch("bake-merge", tmp)
    cmd = cap["cmd"]
    # cmd = [lev, "run", <agent>, "--yolo", ...]
    check("T1a the argv is a lev run", len(cmd) >= 3 and cmd[1] == "run", str(cmd[:3]))
    agent_arg = cmd[2]
    check("T1b the agent argument is an absolute path",
          os.path.isabs(agent_arg), agent_arg)
    check("T1c it is NOT the bare installed name",
          agent_arg != "bake-merge", agent_arg)
    check("T1d it resolves to the in-repo bake-merge directory",
          Path(agent_arg).resolve() == harnessql_paths.bake_merge_dir().resolve(),
          f"{agent_arg} vs {harnessql_paths.bake_merge_dir()}")
    check("T1e the in-repo directory is not the installed one",
          "/.leviath/agents/" not in agent_arg, agent_arg)

    cap2 = capture_dispatch("bake-off", tmp)
    check("T1f bake-off resolves to the in-repo bake-off directory",
          Path(cap2["cmd"][2]).resolve() == harnessql_paths.agent_dir().resolve(),
          cap2["cmd"][2])


def test_blueprint_is_restricted() -> None:
    print("\n[T2] the dispatched blueprint really is the restricted reviewer")
    import harnessql_paths

    text = (harnessql_paths.bake_merge_dir() / "agent.leviath").read_text()
    check("T2a [read_paths] allow is empty", "allow = []" in text)
    check("T2b no read_file tool is available", '"read_file"' not in text)
    check("T2c no list_dir tool is available", '"list_dir"' not in text)
    check("T2d no write_file tool is available", '"write_file"' not in text)
    check("T2e the only tools are the format tool and submit_output",
          '"merge_decision_format", "submit_output"' in text)


def test_conductor_constants_resolve(tmp: Path) -> None:
    print("\n[T3] the conductor's lane constants dispatch the same paths")
    import harnessql_paths
    import run_bakeoff3 as r3

    check("T3a MERGE_AGENT is the restricted lane name", r3.MERGE_AGENT == "bake-merge",
          r3.MERGE_AGENT)
    cap = capture_dispatch(r3.MERGE_AGENT, tmp)
    check("T3b the conductor's merge lane dispatches the in-repo path",
          Path(cap["cmd"][2]).resolve() == harnessql_paths.bake_merge_dir().resolve(),
          cap["cmd"][2])


def test_lev_run_parses_json(tmp: Path) -> None:
    print("\n[T4] lev_run still returns the run id and final output")
    cap = capture_dispatch("bake-merge", tmp)
    res = cap["result"]
    check("T4a the run id is returned", res.get("run_id") == "run-stub-1", str(res))
    check("T4b the final output is returned", res.get("final_output") == "OK", str(res))


def main() -> int:
    print("GQL-09 real-lane-dispatch tests (offline, no model calls)")
    with tempfile.TemporaryDirectory(prefix="harnessql-dispatch-") as tmpdir:
        tmp = Path(tmpdir)
        test_dispatch_uses_repo_path(tmp)
        test_blueprint_is_restricted()
        test_conductor_constants_resolve(tmp)
        test_lev_run_parses_json(tmp)
    print()
    if FAILURES:
        print(f"=== {len(FAILURES)} CHECK(S) FAILED ===")
        for name in FAILURES:
            print(f"  FAILED {name}")
        return 1
    print("=== ALL LANE-DISPATCH CHECKS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())