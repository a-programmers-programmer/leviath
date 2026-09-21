"""Shared authorisation and audit for the executor surfaces.

Every surface (GraphQL, CLI, MCP) calls `authorize()` before it touches the
core. The scope names come from executor.identity, so a permission decision
made for one transport is the same decision for the others.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .identity import (
    ALL_SCOPES, DEFAULT_ROLE, AuthError, Principal, make_principal, mint_token,
    scopes_for_role, verify_token,
)


@dataclass
class AuditEntry:
    ts: float
    principal_id: str
    action: str
    resource: str
    allowed: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": round(self.ts, 3),
            "principal_id": self.principal_id,
            "action": self.action,
            "resource": self.resource,
            "allowed": self.allowed,
            "reason": self.reason,
        }


class AuditLog:
    """Append-only audit trail shared by all three surfaces."""

    def __init__(self, limit: int = 1000):
        self.limit = limit
        self.entries: List[AuditEntry] = []

    def record(self, principal: Optional[Principal], action: str, resource: str,
               allowed: bool, reason: str = "") -> AuditEntry:
        entry = AuditEntry(
            ts=time.time(),
            principal_id=(principal.principal_id if principal else "<anonymous>"),
            action=action,
            resource=resource,
            allowed=allowed,
            reason=reason,
        )
        self.entries.append(entry)
        if len(self.entries) > self.limit:
            del self.entries[: len(self.entries) - self.limit]
        return entry

    def to_list(self) -> List[dict]:
        return [e.to_dict() for e in self.entries]


@dataclass
class AuthContext:
    """Holds the trust root and the audit log for one executor instance."""

    secret: str
    audit: AuditLog = field(default_factory=AuditLog)
    anonymous_role: Optional[str] = None  # dev convenience, off by default

    def authenticate(self, token: Optional[str]) -> Principal:
        """Turn a credential into a principal. Raises AuthError when it cannot."""
        if not token and self.anonymous_role:
            principal = make_principal("anonymous", role=self.anonymous_role, kind="user")
            self.audit.record(principal, "authenticate", "anonymous", True, "anonymous allowed")
            return principal
        try:
            principal = verify_token(token, self.secret)
        except AuthError as exc:
            self.audit.record(None, "authenticate", "token", False, exc.code)
            raise
        self.audit.record(principal, "authenticate", principal.principal_id, True)
        return principal

    def issue(self, principal_id: str, role: str = DEFAULT_ROLE, kind: str = "agent",
              ttl: float = 3600.0) -> str:
        """Mint a token for a new principal. Roles pick the scope set."""
        return mint_token(make_principal(principal_id, role=role, kind=kind), self.secret, ttl=ttl)

    def authorize(self, principal: Principal, scope: str, resource: str = "",
                  owner: Optional[str] = None) -> Principal:
        """Check a scope, then check ownership when an owner is given."""
        if scope not in ALL_SCOPES:
            self.audit.record(principal, scope, resource, False, "unknown scope")
            raise AuthError("unknown_scope", f"unknown scope {scope}")
        if not principal.has_scope(scope):
            self.audit.record(principal, scope, resource, False, "missing scope")
            raise AuthError("forbidden", f"principal {principal.principal_id} lacks {scope}")
        if owner is not None and owner != principal.principal_id:
            if not principal.has_scope("executor:admin"):
                self.audit.record(principal, scope, resource, False, "not owner")
                raise AuthError("forbidden", "principal is not the owner of this job")
        self.audit.record(principal, scope, resource, True)
        return principal
