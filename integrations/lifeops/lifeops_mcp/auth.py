"""Use FastMCP's OAuth implementation, with explicit Google identity checks."""

import time
from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.disk import DiskStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from .config import Settings


def allowed_identity(token, client_id, emails):
    if token is None or token.expires_at is None or token.expires_at <= time.time():
        return False
    claims = token.claims
    info = claims.get("google_token_info", {})
    user = claims.get("google_user_data", {})
    if not isinstance(info, dict) or not isinstance(user, dict):
        return False
    # FastMCP 2.14's Google verifier records, but does not enforce, audience.
    if info.get("audience") != client_id or token.client_id != client_id:
        return False
    email = user.get("email")
    return (user.get("verified_email") is True and isinstance(email, str)
            and email.lower() in emails and claims.get("email") == email
            and bool(user.get("id")) and claims.get("sub") == user.get("id"))


class LifeOpsGoogleProvider(GoogleProvider):
    def __init__(self, settings: Settings):
        self.expected_client = settings.google_client_id
        self.allowed_emails = settings.allowed_emails
        settings.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        super().__init__(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            base_url=settings.public_url,
            required_scopes=["openid", "https://www.googleapis.com/auth/userinfo.email"],
            jwt_signing_key=settings.jwt_key,
            client_storage=FernetEncryptionWrapper(
                DiskStore(directory=settings.state_dir / "oauth"),
                fernet=Fernet(settings.encryption_key.encode()),
            ),
            require_authorization_consent=True,
        )

    async def load_access_token(self, token):
        verified = await super().load_access_token(token)
        if not allowed_identity(verified, self.expected_client, self.allowed_emails):
            return None
        return verified
