"""GraphQL surface for the executor.

This module binds a real GraphQL schema (graphql-core) directly onto
ExecutorCore. It holds no auth logic, no lifecycle logic and no identity
logic of its own: resolvers call the same core the CLI and MCP surfaces call.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from graphql import (
    GraphQLError, GraphQLSchema, build_schema, graphql_sync,
)

from .auth import AuthContext
from .core import BackendError, ExecutorCore, SpawnRequest
from .identity import AuthError, Principal
from .lifecycle import LifecycleError

SCHEMA_SDL = """
type Principal {
  principalId: ID!
  role: String!
  kind: String!
  scopes: [String!]!
}

type Artifact {
  name: String!
  kind: String!
  size: Int
  sha256: String
  contentBase64: String
}

type LifecycleEvent {
  kind: String!
  from: String
  to: String
  ts: Float!
}

type Job {
  jobId: ID!
  principalId: String!
  agent: String!
  task: String!
  state: String!
  workdir: String
  requestId: String
  artifacts: [Artifact!]!
  events: [LifecycleEvent!]!
  errorCode: String
  errorMessage: String
}

type Error {
  code: String!
  message: String!
}

type SpawnPayload {
  ok: Boolean!
  job: Job
  error: Error
}

type StatusPayload {
  ok: Boolean!
  job: Job
  error: Error
}

type CancelPayload {
  ok: Boolean!
  job: Job
  error: Error
}

type ArtifactPayload {
  ok: Boolean!
  artifact: Artifact
  error: Error
}

type Query {
  # who the presented credential is; null when unauthenticated
  whoami(token: String): Principal

  status(token: String, jobId: ID!): StatusPayload!
  jobs(token: String): [Job!]!
  artifact(token: String, jobId: ID!, name: String!): ArtifactPayload!
}

type Mutation {
  spawn(token: String, agent: String!, task: String!, workdir: String,
        yolo: Boolean, requestId: String): SpawnPayload!
  cancel(token: String, jobId: ID!, force: Boolean): CancelPayload!
}
"""


def _job_to_gql(job) -> Dict[str, Any]:
    data = job.to_dict()
    return {
        "jobId": data["job_id"],
        "principalId": data["principal_id"],
        "agent": data["agent"],
        "task": data["task"],
        "state": data["state"],
        "workdir": data["workdir"],
        "requestId": data["request_id"],
        "artifacts": [
            {"name": a["name"], "kind": a["kind"], "size": a.get("size"),
             "sha256": a.get("sha256"), "contentBase64": a.get("content_base64")}
            for a in data["artifacts"]
        ],
        "events": [
            {"kind": e["kind"], "from": e.get("from"), "to": e.get("to"),
             "ts": e.get("ts", 0.0)}
            for e in data["events"]
        ],
        "errorCode": (data["error"] or {}).get("code"),
        "errorMessage": (data["error"] or {}).get("message"),
    }


def _artifact_to_gql(artifact: dict) -> Dict[str, Any]:
    """Map the core's snake_case artifact dict onto GraphQL field names."""
    return {
        "name": artifact["name"],
        "kind": artifact.get("kind", "artifact"),
        "size": artifact.get("size"),
        "sha256": artifact.get("sha256"),
        "contentBase64": artifact.get("content_base64"),
    }


def _fail(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, (AuthError, LifecycleError, BackendError)):
        return {"ok": False, "error": exc.to_dict()}
    raise exc


class ExecutorGraphQL:
    """The GraphQL surface. One instance wraps one shared ExecutorCore."""

    def __init__(self, core: ExecutorCore, auth: Optional[AuthContext] = None):
        self.core = core
        self.auth = auth or core.auth
        self.schema: GraphQLSchema = build_schema(SCHEMA_SDL)
        self._bind()

    # -- resolver helpers -------------------------------------------------
    def _principal(self, token: Optional[str]) -> Principal:
        return self.auth.authenticate(token)

    def _bind(self) -> None:
        schema = self.schema

        def whoami(_root, _info, token=None):
            try:
                principal = self._principal(token)
            except AuthError:
                return None
            return {
                "principalId": principal.principal_id,
                "role": principal.role,
                "kind": principal.kind,
                "scopes": list(principal.scopes),
            }

        def status(_root, _info, token=None, jobId=None):
            try:
                principal = self._principal(token)
                return {"ok": True, "job": _job_to_gql(self.core.status(principal, jobId))}
            except (AuthError, BackendError, LifecycleError) as exc:
                return _fail(exc)

        def jobs(_root, _info, token=None):
            try:
                principal = self._principal(token)
                return [_job_to_gql(j) for j in self.core.list_jobs(principal)]
            except (AuthError, BackendError, LifecycleError):
                return []

        def artifact(_root, _info, token=None, jobId=None, name=None):
            try:
                principal = self._principal(token)
                return {"ok": True,
                        "artifact": _artifact_to_gql(self.core.artifact(principal, jobId, name))}
            except (AuthError, BackendError, LifecycleError) as exc:
                return _fail(exc)

        def spawn(_root, _info, token=None, agent=None, task=None, workdir=None,
                  yolo=True, requestId=None):
            try:
                principal = self._principal(token)
                job = self.core.spawn(principal, SpawnRequest(
                    agent=agent, task=task, workdir=workdir,
                    yolo=bool(yolo), request_id=requestId,
                ))
                return {"ok": job.error is None, "job": _job_to_gql(job),
                        "error": job.error}
            except (AuthError, BackendError, LifecycleError) as exc:
                return _fail(exc)

        def cancel(_root, _info, token=None, jobId=None, force=False):
            try:
                principal = self._principal(token)
                job = self.core.cancel(principal, jobId, force=bool(force))
                return {"ok": job.error is None, "job": _job_to_gql(job),
                        "error": job.error}
            except (AuthError, BackendError, LifecycleError) as exc:
                return _fail(exc)

        schema.type_map["Query"].fields["whoami"].resolve = whoami
        schema.type_map["Query"].fields["status"].resolve = status
        schema.type_map["Query"].fields["jobs"].resolve = jobs
        schema.type_map["Query"].fields["artifact"].resolve = artifact
        schema.type_map["Mutation"].fields["spawn"].resolve = spawn
        schema.type_map["Mutation"].fields["cancel"].resolve = cancel

    # -- entry point ------------------------------------------------------
    def execute(self, query: str, variables: Optional[dict] = None,
                operation_name: Optional[str] = None) -> dict:
        """Run a GraphQL document and return a JSON-safe result."""
        result = graphql_sync(
            self.schema, query, variable_values=variables or {},
            operation_name=operation_name,
        )
        return {
            "data": result.data,
            "errors": (
                [{"message": str(e.message)} for e in result.errors]
                if result.errors else None
            ),
        }
