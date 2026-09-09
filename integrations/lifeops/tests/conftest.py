import time
from dataclasses import replace
import pytest
from cryptography.fernet import Fernet
from fastmcp.server.auth import AccessToken
from lifeops_mcp.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        public_url="https://mcp.example.test", google_client_id="client.apps.googleusercontent.com",
        google_client_secret="test-secret-not-real", allowed_emails=frozenset({"operator@example.test"}),
        jwt_key="a" * 48, encryption_key=Fernet.generate_key().decode(), state_dir=tmp_path,
        desk_url="https://desk.example.test", desk_token="desk-secret-not-real",
        leviath_url="http://127.0.0.1:3000", leviath_token="lev-secret-not-real",
        projects={"leviath": "/data/work/leviath"}, blueprints=frozenset({"qwen-worker", "fixer"}),
        writes_enabled=True,
    )


@pytest.fixture
def google_token(settings):
    return AccessToken(token="not-a-real-token", client_id=settings.google_client_id,
                       scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
                       expires_at=int(time.time()) + 300,
                       claims={"sub": "123", "email": "operator@example.test",
                               "google_token_info": {"audience": settings.google_client_id},
                               "google_user_data": {"id": "123", "email": "operator@example.test",
                                                    "verified_email": True}})
