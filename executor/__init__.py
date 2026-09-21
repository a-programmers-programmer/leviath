"""Executor surface: spawn, status, cancel, artifact.

One shared core, three surfaces:

* ``executor.core.ExecutorCore`` -- identity + auth + lifecycle live here once
* ``executor.graphql_surface``  -- GraphQL resolvers bound to the core
* ``executor.cli_surface``      -- CLI verbs bound to the core
* ``executor.mcp_surface``      -- MCP tools bound to the core
"""
from .auth import AuditLog, AuthContext
from .core import (
    Backend, BackendError, ExecutorCore, Job, LevDaemonBackend, SpawnRequest,
)
from .identity import (
    ALL_SCOPES, AuthError, Principal, SCOPE_ARTIFACT, SCOPE_CANCEL, SCOPE_SPAWN,
    SCOPE_STATUS, make_principal, mint_token, scopes_for_role, verify_token,
)
from .lifecycle import LifecycleError, RunState

__all__ = [
    "AuditLog", "AuthContext", "AuthError", "Backend", "BackendError",
    "ExecutorCore", "Job", "LevDaemonBackend", "LifecycleError", "Principal",
    "RunState", "SpawnRequest", "ALL_SCOPES", "SCOPE_ARTIFACT", "SCOPE_CANCEL",
    "SCOPE_SPAWN", "SCOPE_STATUS", "make_principal", "mint_token",
    "scopes_for_role", "verify_token",
]
