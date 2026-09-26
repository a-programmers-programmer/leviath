#!/usr/bin/env python3
"""IO driver for the deterministic fleet progress lease core."""
import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from fleet_progress_guard import decide, initial_state

_TERMINAL = {"complete", "completed", "failed", "cancelled", "canceled", "stopped", "error"}
_LIVE = {"running", "active", "queued", "pending", "waiting", "waiting_input", "paused", "idle"}


def _json(path):
    with open(path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        dirfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _row_ids(rows):
    if not isinstance(rows, list):
        raise ValueError("invalid_rows")
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("run_id"), str) or not row["run_id"].strip():
            raise ValueError("invalid_row_identity")
        if row["run_id"] in ids:
            raise ValueError("duplicate_row_identity")
        ids.add(row["run_id"])
    return ids


def observe(policy, native_lev="/data/bin/lev", runs_dir="/data/.leviath/runs", run=subprocess.run):
    """Return core observation and safe summary, or unknown on any weak proof."""
    rid = policy["run_id"]
    try:
        result = run([native_lev, "ps", "--json"], capture_output=True, text=True, timeout=10, check=False)
        if result.returncode != 0:
            raise ValueError("daemon_unreachable")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("health"), dict):
            raise ValueError("invalid_daemon_schema")
        live_ids = _row_ids(payload.get("runs"))
        finished_ids = _row_ids(payload.get("finished"))
        if live_ids & finished_ids:
            raise ValueError("duplicate_run_state")
        live = rid in live_ids
        target_finished = rid in finished_ids
        row_statuses = {row["run_id"]: row.get("status") for row in payload["runs"] + payload["finished"]}
        native_status = row_statuses.get(rid)
        meta_path = Path(runs_dir) / rid / "meta.json"
        meta = _json(meta_path)
        if not isinstance(meta, dict):
            raise ValueError("invalid_meta")
        status = meta.get("status")
        cost = meta.get("cost_usd")
        children = meta.get("children")
        unpriced = meta.get("unpriced_calls")
        if (not isinstance(status, str) or not status.strip() or isinstance(cost, bool) or
                not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0 or
                not isinstance(unpriced, int) or isinstance(unpriced, bool) or unpriced != 0 or
                not isinstance(children, list) or children):
            raise ValueError("invalid_or_unpriced_meta")
        if live and (status.lower() in _TERMINAL or not isinstance(native_status, str) or native_status.lower() in _TERMINAL):
            raise ValueError("native_status_mismatch")
        if live and not target_finished and status.lower() not in _LIVE:
            raise ValueError("native_status_mismatch")
        if live and status.lower() not in _LIVE:
            raise ValueError("native_status_mismatch")
        if not live and not target_finished and status.lower() not in _TERMINAL and status.lower() != "waiting_input":
            raise ValueError("absent_nonterminal_meta")
        return {"status": status, "live": live, "cost_usd": float(cost)}, {
            "known": True, "status": status, "live": live, "cost_usd": float(cost), "error": None,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        message = str(exc) if str(exc) in {"daemon_unreachable", "invalid_daemon_schema", "invalid_rows",
                                           "invalid_row_identity", "duplicate_row_identity", "duplicate_run_state",
                                           "invalid_meta", "invalid_or_unpriced_meta", "native_status_mismatch",
                                           "absent_nonterminal_meta"} else type(exc).__name__
        return None, {"known": False, "status": "unknown", "live": None, "cost_usd": None, "error": message}


def _valid_receipt(path):
    try:
        return _json(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _fresh_wrapper(policy, reason):
    core = initial_state(policy)
    core["cancel_reason"] = reason
    return {"core": core, "cancel_attempts": 0, "last_cancel_at": None,
            "unresolved_cancel": True, "observation": {"known": False, "status": "unknown",
            "live": None, "cost_usd": None, "error": reason}}


def tick(policy, state_path, receipt_path, native_lev="/data/bin/lev", runs_dir="/data/.leviath/runs",
         now=None, run=subprocess.run):
    now = time.time() if now is None else now
    path = Path(state_path)
    reason = None
    try:
        raw = path.read_bytes()
        wrapper = json.loads(raw)
        if not isinstance(wrapper, dict) or set(wrapper) != {"core", "cancel_attempts", "last_cancel_at", "unresolved_cancel", "observation"}:
            raise ValueError("corrupt_state")
        last_cancel_at = wrapper["last_cancel_at"]
        invalid_last_cancel = (last_cancel_at is not None and
                               (isinstance(last_cancel_at, bool) or
                                not isinstance(last_cancel_at, (int, float)) or
                                not math.isfinite(last_cancel_at)))
        if (not isinstance(wrapper["cancel_attempts"], int) or isinstance(wrapper["cancel_attempts"], bool) or
                wrapper["cancel_attempts"] < 0 or not isinstance(wrapper["unresolved_cancel"], bool) or
                invalid_last_cancel):
            raise ValueError("corrupt_state")
    except FileNotFoundError:
        wrapper = _fresh_wrapper(policy, "missing_state")
        reason = "missing_state"
    except (OSError, ValueError, json.JSONDecodeError):
        # Preserve the exact corrupt bytes before writing fail-closed state.
        try:
            evidence = path.with_name(path.name + f".corrupt.{int(now)}")
            fd = os.open(evidence, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except FileExistsError:
            pass
        except UnboundLocalError:
            raw = b""
        wrapper = _fresh_wrapper(policy, "corrupt_state")
        reason = "corrupt_state"

    observation, summary = observe(policy, native_lev, runs_dir, run)
    if observation is None:
        observation = {"status": "unknown", "live": True, "cost_usd": None}
    if reason:
        decision = {"action": "cancel", "state": wrapper["core"]}
    else:
        decision = decide(policy, wrapper["core"], observation, _valid_receipt(receipt_path), now)
    wrapper["core"] = decision["state"]
    wrapper["observation"] = summary
    cancel = decision["action"] == "cancel"
    if cancel:
        wrapper["unresolved_cancel"] = True
    if decision["action"] == "terminal":
        wrapper["unresolved_cancel"] = False
    # Persist decision before any external cancellation call.
    _atomic_json(path, wrapper)
    if cancel and summary["known"] and summary["live"] is False:
        # Core needs terminal meta as well as non-live observation. Unknown becomes no proof.
        pass
    should_cancel = cancel and (not summary["known"] or summary["live"] is not False or
                                summary.get("status", "").lower() == "waiting_input")
    if should_cancel and (wrapper["last_cancel_at"] is None or now - wrapper["last_cancel_at"] >= 30):
        wrapper["cancel_attempts"] += 1
        wrapper["last_cancel_at"] = now
        _atomic_json(path, wrapper)
        try:
            result = run([native_lev, "cancel", policy["run_id"]], capture_output=True, text=True, timeout=10, check=False)
            wrapper["observation"]["cancel_error"] = None if result.returncode == 0 else f"exit_{result.returncode}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            wrapper["observation"]["cancel_error"] = type(exc).__name__
        wrapper["unresolved_cancel"] = True
        _atomic_json(path, wrapper)
    print(json.dumps({"run_id": policy["run_id"], "task_id": policy["task_id"],
                      "spend_usd": wrapper["core"].get("max_cost_usd"), "decision": decision["action"],
                      "checkpoint": wrapper["core"].get("next_checkpoint"),
                      "cancel_attempts": wrapper["cancel_attempts"], "terminal_verified": decision["action"] == "terminal"},
                     sort_keys=True), flush=True)
    return 1 if wrapper["unresolved_cancel"] else 0


def main(argv=None, run=subprocess.run, clock=time.time, sleep=time.sleep):
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--native-lev", default="/data/bin/lev")
    parser.add_argument("--runs-dir", default="/data/.leviath/runs")
    parser.add_argument("--poll-seconds", type=float, default=30)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    policy = _json(args.policy)
    state_path = Path(args.state)
    lock_path = Path(str(state_path) + ".lock")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lockfd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("state lock contention", file=sys.stderr)
            return 2
        if args.init:
            fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"core": initial_state(policy), "cancel_attempts": 0, "last_cancel_at": None,
                           "unresolved_cancel": False, "observation": {"known": False, "status": "unknown",
                           "live": None, "cost_usd": None, "error": "not_observed"}}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            return 0
        while True:
            rc = tick(policy, args.state, args.receipt, args.native_lev, args.runs_dir, now=clock(), run=run)
            if args.once or json.loads(state_path.read_text(encoding="utf-8")).get("core", {}).get("terminal_verified"):
                return 0 if args.once and rc == 0 else rc
            sleep(max(0.1, args.poll_seconds))
    except (OSError, ValueError, TypeError) as exc:
        print(f"progress guard IO error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        os.close(lockfd)


if __name__ == "__main__":
    raise SystemExit(main())
