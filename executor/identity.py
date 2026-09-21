"""Shared identity for the executor surface.

One implementation of "who is calling". The GraphQL, CLI and MCP surfaces
all authenticate through these functions, so identity is not re-invented per
transport. A caller is a signed token; nothing else identifies it.

Token format:  lev1.<base64url(json payload)>.<base64url(hmac-sha256)>
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

TOKEN_PREFIX = "lev1"


class AuthError(Exception):
    """Authentication or authorisation failure with a stable machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


# Scope names are shared by every surface.
SCOPE_SPAWN = "executor:spawn"
SCOPE_STATUS = "executor:status"
SCOPE_CANCEL = "executor:cancel"
SCOPE_ARTIFACT = "executor:artifact"
SCOPE_ADMIN = "executor:admin"

ALL_SCOPES = (SCOPE_SPAWN, SCOPE_STATUS, SCOPE_CANCEL, SCOPE_ARTIFACT, SCOPE_ADMIN)

# One role table for all surfaces. Roles never grant by transport.
ROLE_SCOPES: Dict[str, Tuple[str, ...]] = {
    "owner": ALL_SCOPES,
    "operator": (SCOPE_SPAWN, SCOPE_STATUS, SCOPE_CANCEL, SCOPE_ARTIFACT),
    "viewer": (SCOPE_STATUS, SCOPE_ARTIFACT),
    "none": (),
}

# A caller that names no role gets no scopes. Identity is a signed token, so a
# bare principal carries no authority on any surface.
DEFAULT_ROLE = "none"


def scopes_for_role(role: str) -> Tuple[str, ...]:
    return ROLE_SCOPES.get(role, ROLE_SCOPES[DEFAULT_ROLE])


@dataclass(frozen=True)
class Principal:
    """An authenticated caller identity."""

    principal_id: str
    role: str = DEFAULT_ROLE
    kind: str = "agent"  # user | agent | service
    scopes: Tuple[str, ...] = ()
    issued_at: int = 0
    expires_at: int = 0

    def has_scope(self, scope: str) -> bool:
        if scope in self.scopes:
            return True
        return SCOPE_ADMIN in self.scopes

    def to_dict(self) -> dict:
        return {
            "principal_id": self.principal_id,
            "role": self.role,
            "kind": self.kind,
            "scopes": list(self.scopes),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256)
    return _b64e(mac.digest())


def make_principal(principal_id: str, role: str = DEFAULT_ROLE, kind: str = "agent",
                   scopes: Optional[Tuple[str, ...]] = None, now: Optional[int] = None) -> Principal:
    """Build a principal. Scopes default to the role table, never to all."""
    ts = int(now if now is not None else time.time())
    return Principal(
        principal_id=principal_id,
        role=role,
        kind=kind,
        scopes=tuple(scopes) if scopes is not None else scopes_for_role(role),
        issued_at=ts,
        expires_at=0,
    )


def mint_token(principal: Principal, secret: str, ttl: float = 3600.0,
               now: Optional[int] = None) -> str:
    """Mint a signed token for a principal. The secret is the only trust root."""
    ts = int(now if now is not None else time.time())
    payload = principal.to_dict()
    payload["issued_at"] = ts
    payload["expires_at"] = ts + int(ttl)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body = _b64e(raw)
    return f"{TOKEN_PREFIX}.{body}.{_sign(body.encode('ascii'), secret)}"


def verify_token(token: Optional[str], secret: str, now: Optional[int] = None) -> Principal:
    """Verify a token and return the principal. Raises AuthError on any problem."""
    if not token:
        raise AuthError("unauthenticated", "no credential presented")
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        raise AuthError("invalid_token", "malformed token")
    body = parts[1]
    expected = _sign(body.encode("ascii"), secret)
    if not hmac.compare_digest(expected, parts[2]):
        raise AuthError("invalid_token", "bad token signature")
    try:
        payload = json.loads(_b64d(body))
    except Exception:  # noqa: BLE001
        raise AuthError("invalid_token", "unreadable token payload")
    if not isinstance(payload, dict) or not payload.get("principal_id"):
        raise AuthError("invalid_token", "token payload lacks principal_id")
    ts = int(now if now is not None else time.time())
    expires = int(payload.get("expires_at") or 0)
    if expires and ts > expires:
        raise AuthError("expired_token", "token expired")
    return Principal(
        principal_id=payload["principal_id"],
        role=payload.get("role", DEFAULT_ROLE),
        kind=payload.get("kind", "agent"),
        scopes=tuple(payload.get("scopes") or scopes_for_role(payload.get("role", DEFAULT_ROLE))),
        issued_at=int(payload.get("issued_at") or 0),
        expires_at=expires,
    )
