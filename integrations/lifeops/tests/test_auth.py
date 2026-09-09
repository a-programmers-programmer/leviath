from copy import deepcopy
import re
import time
from urllib.parse import urlsplit
import httpx
import pytest
from fastmcp.server.auth import OAuthProxy
from lifeops_mcp.auth import allowed_identity, LifeOpsGoogleProvider
from lifeops_mcp.config import Settings, origin
from lifeops_mcp.server import create_server


def test_identity_requires_google_audience_verified_email_and_subject(settings, google_token):
    assert allowed_identity(google_token, settings.google_client_id, settings.allowed_emails)
    alterations = [
        lambda t: setattr(t, "client_id", "other-app"),
        lambda t: setattr(t, "expires_at", int(time.time()) - 1),
        lambda t: setattr(t, "expires_at", None),
        lambda t: t.claims["google_token_info"].update(audience="other-app"),
        lambda t: t.claims["google_user_data"].update(verified_email=False),
        lambda t: t.claims["google_user_data"].update(verified_email="true"),
        lambda t: t.claims["google_user_data"].update(email="stranger@example.test"),
        lambda t: t.claims.update(sub="different-user"),
        lambda t: t.claims.pop("google_user_data"),
    ]
    for alter in alterations:
        token = deepcopy(google_token)
        alter(token)
        assert not allowed_identity(token, settings.google_client_id, settings.allowed_emails)
    assert not allowed_identity(None, settings.google_client_id, settings.allowed_emails)


async def test_oauth_discovery_challenge_and_durable_client_registration(settings):
    server = create_server(settings)
    app = server.http_app(json_response=True, stateless_http=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_url) as client:
            assert (await client.get("/healthz")).json() == {"status": "ok", "service": "lifeops-mcp"}
            unauth = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            assert unauth.status_code == 401
            challenge = unauth.headers["www-authenticate"]
            metadata_url = re.search(r'resource_metadata="([^"]+)"', challenge).group(1)
            resource = (await client.get(urlsplit(metadata_url).path)).json()
            assert resource["resource"] == settings.public_url + "/mcp"
            assert resource["authorization_servers"] == [settings.public_url + "/"] or resource["authorization_servers"] == [settings.public_url]
            metadata = (await client.get("/.well-known/oauth-authorization-server")).json()
            assert "S256" in metadata["code_challenge_methods_supported"]
            assert metadata["authorization_endpoint"].startswith(settings.public_url)
            reg = await client.post(urlsplit(metadata["registration_endpoint"]).path, json={
                "client_name": "Offline test client", "redirect_uris": ["https://client.example.test/callback"],
                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            })
            assert reg.status_code == 201, reg.text
            client_id = reg.json()["client_id"]
    restarted = LifeOpsGoogleProvider(settings)
    assert await restarted.get_client(client_id) is not None


async def test_http_token_gate_denies_unlisted_google_account(settings, google_token, monkeypatch):
    # Keep the real HTTP auth middleware and our Google gate; only simulate the
    # successful provider token swap. No real Google secrets or traffic are used.
    async def swap(self, raw):
        token = deepcopy(google_token)
        if raw == "unlisted":
            token.claims["google_user_data"]["email"] = "stranger@example.test"
            token.claims["email"] = "stranger@example.test"
        elif raw != "allowed":
            return None
        return token
    monkeypatch.setattr(OAuthProxy, "load_access_token", swap)
    app = create_server(settings).http_app(json_response=True, stateless_http=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_url) as client:
            for bearer, expected in [("unlisted", 401), ("invalid", 401), ("allowed", 200)]:
                response = await client.post("/mcp", headers={"Authorization": "Bearer " + bearer,
                                                             "Accept": "application/json, text/event-stream"},
                                             json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                                                 "protocolVersion": "2025-06-18", "capabilities": {},
                                                 "clientInfo": {"name": "test", "version": "1"}}})
                assert response.status_code == expected, response.text
                if expected == 200:
                    assert response.json()["result"]["serverInfo"]["name"] == "LifeOps"


@pytest.mark.parametrize("value", ["http://remote.example", "https://user:pass@host", "https://host/path", "https://host?token=x", "//host"])
def test_origin_rejects_insecure_or_credential_urls(value):
    with pytest.raises(ValueError):
        origin(value, "TEST")


def test_startup_fails_without_explicit_oauth_configuration():
    with pytest.raises(ValueError, match="LIFEOPS_ALLOWED_EMAILS"):
        Settings.from_env({})
