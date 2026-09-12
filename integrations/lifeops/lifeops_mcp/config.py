"""Deployment configuration. Secrets are supplied only by the operator."""

import json
import os
from dataclasses import dataclass, field
from pathlib import PurePosixPath, Path
from urllib.parse import urlsplit


def origin(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError(f"{name} must be an origin without credentials, path, or query")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError(f"{name} requires HTTPS except on loopback")
    return value.rstrip("/")


def required(env, name):
    value = env.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def flag(env, name):
    value = env.get(name, "0")
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be 0 or 1")
    return value == "1"


@dataclass(frozen=True)
class Settings:
    public_url: str
    google_client_id: str
    google_client_secret: str = field(repr=False)
    allowed_emails: frozenset[str]
    jwt_key: str = field(repr=False)
    encryption_key: str = field(repr=False)
    state_dir: Path
    desk_url: str
    desk_token: str = field(repr=False)
    leviath_url: str
    leviath_token: str = field(repr=False)
    projects: dict[str, str]
    blueprints: frozenset[str]
    writes_enabled: bool = False
    unattended: bool = False

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        emails = frozenset(x.strip().lower() for x in required(
            env, "LIFEOPS_ALLOWED_EMAILS").split(",") if x.strip())
        if not emails or any("@" not in e or "*" in e for e in emails):
            raise ValueError("LIFEOPS_ALLOWED_EMAILS requires explicit email addresses")
        projects = json.loads(required(env, "LIFEOPS_PROJECTS_JSON"))
        if not isinstance(projects, dict) or not projects:
            raise ValueError("LIFEOPS_PROJECTS_JSON must map project names to workdirs")
        for name, path in projects.items():
            if (not isinstance(name, str) or not name or not isinstance(path, str)
                    or not PurePosixPath(path).is_absolute() or path == "/"
                    or ".." in PurePosixPath(path).parts):
                raise ValueError("Each project requires a name and a non-root absolute POSIX workdir")
        blueprints = frozenset(x.strip() for x in env.get(
            "LIFEOPS_BLUEPRINTS", "qwen-worker,fixer").split(",") if x.strip())
        if not blueprints or any("/" in b or "\\" in b or b in {".", ".."} for b in blueprints):
            raise ValueError("LIFEOPS_BLUEPRINTS must contain registered blueprint names")
        key = required(env, "LIFEOPS_JWT_SIGNING_KEY")
        if len(key) < 32:
            raise ValueError("LIFEOPS_JWT_SIGNING_KEY requires at least 32 characters")
        state_dir = Path(required(env, "LIFEOPS_STATE_DIR"))
        if not state_dir.is_absolute():
            raise ValueError("LIFEOPS_STATE_DIR must be an absolute persistent directory")
        return cls(
            public_url=origin(required(env, "LIFEOPS_PUBLIC_URL"), "LIFEOPS_PUBLIC_URL"),
            google_client_id=required(env, "LIFEOPS_GOOGLE_CLIENT_ID"),
            google_client_secret=required(env, "LIFEOPS_GOOGLE_CLIENT_SECRET"),
            allowed_emails=emails, jwt_key=key,
            encryption_key=required(env, "LIFEOPS_STORAGE_ENCRYPTION_KEY"),
            state_dir=state_dir,
            desk_url=origin(required(env, "LIFEOPS_DESK_URL"), "LIFEOPS_DESK_URL"),
            desk_token=required(env, "XELOR_SYNC_TOKEN"),
            leviath_url=origin(required(env, "LEVIATH_API_URL"), "LEVIATH_API_URL"),
            leviath_token=required(env, "LEVIATH_API_TOKEN"),
            projects=projects, blueprints=blueprints,
            writes_enabled=flag(env, "LIFEOPS_ENABLE_WRITES"),
            unattended=flag(env, "LIFEOPS_UNATTENDED"),
        )
