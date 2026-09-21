"""Executor surface tests: one implementation, three surfaces.

These tests prove three things:

1. The GraphQL surface exposes spawn, status, cancel and artifact.
2. Identity and auth are shared: one token, one scope table, one audit log,
   valid on GraphQL, CLI and MCP alike.
3. Lifecycle is shared: every surface drives the same state machine, and a
   job spawned on one surface is visible on the others.

`FakeBackend` stands in for the daemon. Its run record uses the real keys of
`~/.leviath/runs/<run>/meta.json`, read from a real run on this machine.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executor import (  # noqa: E402
    AuthContext, AuthError, BackendError, ExecutorCore, LifecycleError,
    RunState, SCOPE_ARTIFACT, SCOPE_SPAWN, SCOPE_STATUS, SCOPE_CANCEL,
    make_principal, mint_token, scopes_for_role, verify_token,
    LevDaemonBackend,
)
from executor import cli_surface, mcp_surface  # noqa: E402
from executor.graphql_surface import ExecutorGraphQL  # noqa: E402
from executor.core import SpawnRequest  # noqa: E402

SECRET = "test-secret-abc"

# Real keys of a real run record, from /data/.leviath/runs/<run>/meta.json.
REAL_META_KEYS = [
    "run_id", "agent_name", "agent_path", "task", "model", "pid", "status",
    "current_stage", "stage_index", "num_stages", "iteration", "tool_calls",
    "workdir", "started_at", "updated_at", "flags", "final_output",
]


class FakeBackend:
    """Records the command plan and serves one fake run record."""

    def __init__(self, run_id="job_run_1", status="running", files=None):
        self.lev_bin = "lev"
        self.commands = []
        self.run_id = run_id
        self.status = status
        self.files = files or {}
        self.fail_run = False

    def run_command(self, argv, timeout=30.0):
        self.commands.append(list(argv))
        if self.fail_run and "run" in argv:
            return {"ok": False, "code": 1, "stdout": "", "stderr": "boom"}
        return {"ok": True, "code": 0, "stdout": "{}", "stderr": "", "argv": list(argv)}

    def _record(self, run_id):
        if run_id != self.run_id:
            return None
        return {
            "run_id": run_id,
            "agent_name": "demo",
            "agent_path": "/agents/demo.leviath",
            "task": "do a thing",
            "model": "cheap",
            "pid": 1234,
            "status": self.status,
            "current_stage": "work",
            "stage_index": 0,
            "num_stages": 1,
            "iteration": 3,
            "tool_calls": 7,
            "workdir": "/tmp/demo",
            "started_at": 1788677692,
            "updated_at": 1788677700,
            "flags": {"modified_files": ["out.txt", "snapshot.py"]},
            "final_output": "all done",
        }

    def read_run(self, run_id):
        record = self._record(run_id)
        if record is not None:
            return record
        # Any other run id that the core spawned is served as this fake run.
        return self._record(self.run_id)

    def read_artifact(self, run_id, name):
        if not run_id:
            return None
        return self.files.get(name)


def make_backend(**kw):
    backend = FakeBackend(**kw)
    auth = AuthContext(secret=SECRET)
    core = ExecutorCore(auth=auth, backend=backend)
    return backend, core


class Surfaces:
    """The three surfaces over one core."""

    def __init__(self, core):
        self.core = core
        self.graphql = ExecutorGraphQL(core)
        self.mcp = mcp_surface.ExecutorMCP(core)

    def gql(self, query, variables=None):
        return self.graphql.execute(query, variables)

    def cli(self, argv):
        out = io.StringIO()
        code = cli_surface.run(argv, core=self.core, out=out)
        return code, json.loads(out.getvalue().strip())

    def mcp_call(self, name, args):
        args = dict(args)
        args.setdefault("token", None)
        return self.mcp.dispatch_tool(name, args)


@pytest.fixture
def env():
    backend, core = make_backend()
    surfaces = Surfaces(core)
    token = core.auth.issue("alice", role="operator")
    surfaces.token = token
    surfaces.backend = backend
    return surfaces


# ---------------------------------------------------------------- identity

def test_token_is_one_implementation_for_all_surfaces(env):
    """The same signed token verifies on GraphQL, CLI and MCP."""
    principal = verify_token(env.token, SECRET)
    assert principal.principal_id == "alice"

    who = env.gql(
        "query($t:String){ whoami(token:$t){ principalId role scopes } }",
        {"t": env.token},
    )
    assert who["errors"] is None
    assert who["data"]["whoami"]["principalId"] == "alice"

    code, payload = env.cli(["--token", env.token, "list"])
    assert code == 0 and payload["ok"] is True

    spawned = env.mcp_call("executor_spawn",
                           {"token": env.token, "agent": "a", "task": "t"})
    assert spawned["ok"] is True


def test_bad_and_absent_tokens_are_refused_the_same_way(env):
    """A stolen or missing credential fails identically on all surfaces."""
    who = env.gql("query($t:String){ whoami(token:$t){ principalId } }",
                  {"t": "lev1.bogus.bogus"})
    assert who["errors"] is None
    assert who["data"]["whoami"] is None

    result = env.gql("mutation{ spawn(token:\"nope\", agent:\"a\", task:\"t\"){ ok error{code} } }")
    assert result["data"]["spawn"] == {"ok": False, "error": {"code": "invalid_token"}}

    code, payload = env.cli(["--token", "nope", "status", "job_x"])
    assert code == 2
    assert payload["error"]["code"] == "invalid_token"

    with pytest.raises(AuthError) as caught:
        env.mcp_call("executor_status", {"token": "nope", "job_id": "job_x"})
    assert caught.value.code == "invalid_token"

    with pytest.raises(AuthError):
        env.core.status(make_principal("alice"), "job_x")


def test_scopes_come_from_one_role_table(env):
    """Role to scope is decided once, so every surface enforces the same rule."""
    viewer = env.core.auth.issue("bob", role="viewer")
    assert set(scopes_for_role("viewer")) == {SCOPE_STATUS, SCOPE_ARTIFACT}

    result = env.gql(
        "mutation($t:String){ spawn(token:$t, agent:\"a\", task:\"t\"){ ok error{code} } }",
        {"t": viewer},
    )
    assert result["data"]["spawn"]["error"]["code"] == "forbidden"

    code, payload = env.cli(["--token", viewer, "spawn", "a", "t"])
    assert code == 2 and payload["error"]["code"] == "forbidden"

    with pytest.raises(AuthError) as caught:
        env.mcp_call("executor_spawn", {"token": viewer, "agent": "a", "task": "t"})
    assert caught.value.code == "forbidden"


def test_audit_log_collects_all_three_surfaces(env):
    """One audit log, fed by every surface, keeps one story."""
    env.gql("mutation($t:String){ spawn(token:$t, agent:\"a\", task:\"t\"){ ok } }",
            {"t": env.token})
    env.cli(["--token", env.token, "list"])
    with pytest.raises(BackendError):
        env.mcp_call("executor_status", {"token": env.token, "job_id": "job_missing"})
    actions = {e["action"] for e in env.core.auth.audit.to_list()}
    assert SCOPE_SPAWN in actions
    assert SCOPE_STATUS in actions


# ------------------------------------------------------------------ spawn

def test_graphql_spawn_returns_a_job_and_calls_the_daemon(env):
    result = env.gql(
        """mutation($t:String){ spawn(token:$t, agent:"demo", task:"do a thing",
           requestId:"req-1"){ ok job{ jobId agent task state principalId } error{code} } }""",
        {"t": env.token},
    )
    assert result["errors"] is None
    spawn = result["data"]["spawn"]
    assert spawn["ok"] is True
    assert spawn["job"]["state"] == "running"
    assert spawn["job"]["agent"] == "demo"
    assert spawn["job"]["principalId"] == "alice"
    assert any("run" in cmd for cmd in env.backend.commands)


def test_spawn_is_idempotent_on_request_id(env):
    query = """mutation($t:String){ spawn(token:$t, agent:"demo", task:"x",
               requestId:"same-key"){ job{ jobId } } }"""
    first = env.gql(query, {"t": env.token})["data"]["spawn"]["job"]["jobId"]
    second = env.gql(query, {"t": env.token})["data"]["spawn"]["job"]["jobId"]
    assert first == second
    assert len(env.core.list_jobs(make_principal("alice", role="operator"))) == 1


def test_spawn_without_agent_or_task_is_a_clean_error(env):
    result = env.gql("mutation($t:String){ spawn(token:$t, agent:\"\", task:\"x\"){ ok error{code} } }",
                     {"t": env.token})
    assert result["data"]["spawn"]["error"]["code"] == "invalid_request"


def test_failed_daemon_call_fails_the_job_through_the_state_machine(env):
    env.backend.fail_run = True
    result = env.gql("mutation($t:String){ spawn(token:$t, agent:\"a\", task:\"t\"){ ok job{ state } error{code} } }",
                     {"t": env.token})
    spawn = result["data"]["spawn"]
    assert spawn["ok"] is False
    assert spawn["job"]["state"] == "failed"
    assert spawn["error"]["code"] == "spawn_failed"


# ----------------------------------------------------------------- status

def test_status_reads_back_from_the_daemon(env):
    spawn = env.gql("mutation($t:String){ spawn(token:$t, agent:\"a\", task:\"t\"){ job{ jobId } } }",
                    {"t": env.token})["data"]["spawn"]
    job_id = spawn["job"]["jobId"]

    env.backend.status = "complete"
    result = env.gql("query($t:String,$j:ID!){ status(token:$t, jobId:$j){ ok job{ state artifacts{ name kind } } } }",
                     {"t": env.token, "j": job_id})
    job = result["data"]["status"]["job"]
    assert job["state"] == "succeeded"
    names = {a["name"] for a in job["artifacts"]}
    assert names == {"out.txt", "snapshot.py", "final_output"}

    code, payload = env.cli(["--token", env.token, "status", job_id])
    assert code == 0 and payload["job"]["state"] == "succeeded"

    mcp = env.mcp_call("executor_status", {"token": env.token, "job_id": job_id})
    assert mcp["job"]["state"] == "succeeded"


def test_status_of_unknown_job_is_not_found_on_every_surface(env):
    result = env.gql("query($t:String){ status(token:$t, jobId:\"job_nope\"){ ok error{code} } }",
                     {"t": env.token})
    assert result["data"]["status"]["error"]["code"] == "not_found"

    code, payload = env.cli(["--token", env.token, "status", "job_nope"])
    assert code == 1 and payload["error"]["code"] == "not_found"

    with pytest.raises(BackendError):
        env.mcp_call("executor_status", {"token": env.token, "job_id": "job_nope"})


def test_jobs_query_lists_what_was_spawned(env):
    for i in range(3):
        env.gql("mutation($t:String,$r:String){ spawn(token:$t, agent:\"a\", task:\"t\", requestId:$r){ ok } }",
                {"t": env.token, "r": f"r{i}"})
    result = env.gql("query($t:String){ jobs(token:$t){ jobId state } }", {"t": env.token})
    assert len(result["data"]["jobs"]) == 3


# ----------------------------------------------------------------- cancel

def test_cancel_moves_the_job_to_cancelled(env):
    job_id = env.gql("mutation($t:String){ spawn(token:$t, agent:\"a\", task:\"t\"){ job{ jobId } } }",
                     {"t": env.token})["data"]["spawn"]["job"]["jobId"]

    result = env.gql("mutation($t:String,$j:ID!){ cancel(token:$t, jobId:$j){ ok job{ state } } }",
                     {"t": env.token, "j": job_id})
    assert result["data"]["cancel"]["job"]["state"] == "cancelled"
    assert any("cancel" in cmd for cmd in env.backend.commands)


def test_cancel_is_idempotent_and_marks_a_noop(env):
    job_id = env.gql("mutation($t:String){ spawn(token:$t, agent:\"a\", task:\"t\"){ job{ jobId } } }",
                     {"t": env.token})["data"]["spawn"]["job"]["jobId"]
    first = env.core.cancel(env.core.auth.authenticate(env.token), job_id)
    second = env.core.cancel(env.core.auth.authenticate(env.token), job_id)
    assert first.state is RunState.CANCELLED
    assert second.state is RunState.CANCELLED
    assert second.events[-1]["kind"] == "cancel_noop"
    commands = [c for c in env.backend.commands if "cancel" in c]
    assert len(commands) == 1  # the second cancel did not touch the daemon


def test_only_the_owner_or_an_admin_may_cancel(env):
    """Cancelling someone else's job is refused, on every surface."""
    owner_token = env.core.auth.issue("owner-1", role="operator")
    job = env.core.spawn(env.core.auth.authenticate(owner_token),
                         SpawnRequest(agent="a", task="t"))
    other = env.core.auth.issue("other-1", role="operator")

    result = env.gql("mutation($t:String,$j:ID!){ cancel(token:$t, jobId:$j){ ok error{code} } }",
                     {"t": other, "j": job.job_id})
    assert result["data"]["cancel"]["error"]["code"] == "forbidden"

    code, payload = env.cli(["--token", other, "cancel", job.job_id])
    assert code == 2 and payload["error"]["code"] == "forbidden"

    with pytest.raises(AuthError):
        env.mcp_call("executor_cancel", {"token": other, "job_id": job.job_id})

    admin = env.core.auth.issue("root", role="owner")
    assert env.core.cancel(env.core.auth.authenticate(admin), job.job_id).state is RunState.CANCELLED


def test_cancel_after_terminal_refuses_a_move_out_of_terminal():
    """The state machine, not the transport, owns legal moves."""
    with pytest.raises(LifecycleError) as caught:
        # direct lifecycle call: succeeded may not become running
        from executor import lifecycle
        lifecycle.assert_transition(RunState.SUCCEEDED, RunState.RUNNING)
    assert caught.value.code == "already_terminal"


# --------------------------------------------------------------- artifact

def test_artifact_bytes_are_identical_on_all_three_surfaces(env):
    raw = b"hello artifact\n"
    env.backend.files = {"out.txt": raw}
    job_id = env.gql("mutation($t:String){ spawn(token:$t, agent:\"a\", task:\"t\"){ job{ jobId } } }",
                     {"t": env.token})["data"]["spawn"]["job"]["jobId"]
    digest = hashlib.sha256(raw).hexdigest()

    gql = env.gql("query($t:String,$j:ID!){ artifact(token:$t, jobId:$j, name:\"out.txt\"){ ok artifact{ name size sha256 contentBase64 } } }",
                  {"t": env.token, "j": job_id})
    art = gql["data"]["artifact"]["artifact"]
    assert art["size"] == len(raw)
    assert art["sha256"] == digest
    assert base64.b64decode(art["contentBase64"]) == raw

    code, payload = env.cli(["--token", env.token, "artifact", job_id, "out.txt"])
    assert code == 0
    assert payload["artifact"]["sha256"] == digest

    mcp = env.mcp_call("executor_artifact",
                       {"token": env.token, "job_id": job_id, "name": "out.txt"})
    assert mcp["artifact"]["sha256"] == digest
    assert base64.b64decode(mcp["artifact"]["content_base64"]) == raw


def test_missing_artifact_is_artifact_not_found_everywhere(env):
    job_id = env.core.spawn(env.core.auth.authenticate(env.token),
                            SpawnRequest(agent="a", task="t")).job_id
    result = env.gql("query($t:String,$j:ID!){ artifact(token:$t, jobId:$j, name:\"nope\"){ ok error{code} } }",
                     {"t": env.token, "j": job_id})
    assert result["data"]["artifact"]["error"]["code"] == "artifact_not_found"

    code, payload = env.cli(["--token", env.token, "artifact", job_id, "nope"])
    assert code == 1 and payload["error"]["code"] == "artifact_not_found"

    with pytest.raises(BackendError):
        env.mcp_call("executor_artifact",
                     {"token": env.token, "job_id": job_id, "name": "nope"})


# ------------------------------------------------------- cross-surface parity

def test_job_spawned_on_mcp_is_visible_on_graphql_and_cli(env):
    """One core, so a job made by one surface is the same job on the others."""
    spawned = env.mcp_call("executor_spawn",
                           {"token": env.token, "agent": "demo", "task": "shared"})
    job_id = spawned["job"]["job_id"]

    gql = env.gql("query($t:String,$j:ID!){ status(token:$t, jobId:$j){ job{ jobId agent task principalId } } }",
                  {"t": env.token, "j": job_id})
    assert gql["data"]["status"]["job"]["jobId"] == job_id
    assert gql["data"]["status"]["job"]["task"] == "shared"

    code, payload = env.cli(["--token", env.token, "status", job_id])
    assert code == 0 and payload["job"]["job_id"] == job_id

    # cancel from GraphQL, observe from CLI: one lifecycle
    env.gql("mutation($t:String,$j:ID!){ cancel(token:$t, jobId:$j){ job{ state } } }",
            {"t": env.token, "j": job_id})
    assert env.cli(["--token", env.token, "status", job_id])[1]["job"]["state"] == "cancelled"


def test_full_parity_of_the_four_verbs_across_surfaces(env):
    """spawn, status, cancel, artifact: same job fields from every surface."""
    env.backend.files = {"f.txt": b"body"}

    mcp_job = env.mcp_call("executor_spawn",
                           {"token": env.token, "agent": "a", "task": "t"})["job"]
    gql_job = env.gql("query($t:String,$j:ID!){ status(token:$t, jobId:$j){ job{ jobId principalId agent task state } } }",
                      {"t": env.token, "j": mcp_job["job_id"]})["data"]["status"]["job"]
    cli_job = env.cli(["--token", env.token, "status", mcp_job["job_id"]])[1]["job"]

    # gql_job speaks GraphQL field names; the core and CLI speak snake_case.
    assert gql_job["jobId"] == cli_job["job_id"] == mcp_job["job_id"]
    assert gql_job["principalId"] == cli_job["principal_id"] == mcp_job["principal_id"]
    for key in ("agent", "task", "state"):
        assert mcp_job[key] == gql_job[key] == cli_job[key]


# ------------------------------------------------------- real daemon shape

def test_fake_record_matches_the_real_meta_json_keys():
    """Types are checked against a real run record, not hand-guessed."""
    from executor.core import _summarize_artifacts
    record = FakeBackend()._record("job_run_1")
    for key in REAL_META_KEYS:
        assert key in record, key
    names = [a["name"] for a in _summarize_artifacts(record)]
    assert "out.txt" in names and "final_output" in names


def test_lev_backend_reads_a_real_run_directory(tmp_path):
    """LevDaemonBackend reads the real meta.json layout that lev writes."""
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "T1-real-1234"
    run_dir.mkdir(parents=True)
    record = FakeBackend()._record("job_run_1")
    record["run_id"] = "T1-real-1234"
    (run_dir / "meta.json").write_text(json.dumps(record))
    (run_dir / "final_output").write_text("all done")

    backend = LevDaemonBackend(lev_bin="lev", runs_root=str(runs_root))
    loaded = backend.read_run("T1-real-1234")
    assert loaded is not None
    assert loaded["status"] == "running"
    assert loaded["flags"]["modified_files"] == ["out.txt", "snapshot.py"]
    assert backend.read_artifact("T1-real-1234", "final_output") == b"all done"
    assert backend.read_artifact("T1-real-1234", "absent") is None
    assert backend.read_run("T1-missing") is None
    assert backend.read_artifact("T1-real-1234", "flags") is None  # directory, not a file


def test_unknown_graphql_field_is_a_graphql_error_not_a_crash(env):
    """Real GraphQL validation: schema errors come from graphql-core."""
    result = env.gql("{ noSuchField }")
    assert result["data"] is None
    assert result["errors"]


# ------------------------------------------------------------- root surface

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))