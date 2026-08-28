"""Интеграционные тесты серверной части OAuth MCP без внешнего Telegram Login."""

import asyncio
from datetime import datetime, timedelta, timezone
import secrets
import unittest

from mcp.server.auth.provider import AuthorizationCode
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import SecretStr
from sqlalchemy import delete

from app.config.settings import get_settings
from app.core.oauth_provider import LifelogOAuthProvider
from app.db.database import SessionLocal
from app.db.models.oauth import OAuthAuthorizationCode, OAuthClient, OAuthRefreshToken
from app.db.models.user import User, UserIdentity


class OAuthProviderTestCase(unittest.TestCase):
    """Проверяет выдачу, проверку и ротацию токенов Lifelog OAuth."""

    def setUp(self) -> None:
        settings = get_settings().model_copy(
            update={
                "auth_enabled": True,
                "oauth_issuer_url": "https://lifelog.test",
                "mcp_resource_url": "https://lifelog.test/mcp",
                "telegram_client_id": "test-telegram-client",
                "telegram_client_secret": SecretStr("test-telegram-secret"),
                "telegram_redirect_uri": "https://lifelog.test/auth/telegram/callback",
                "jwt_signing_key": SecretStr("test-jwt-signing-key-with-32-bytes"),
            }
        )
        self.provider = LifelogOAuthProvider(settings)
        self.client_id = f"oauth-test-{secrets.token_hex(8)}"
        with SessionLocal() as session:
            self.user = session.query(User).join(UserIdentity).filter(
                UserIdentity.provider == "telegram",
                UserIdentity.provider_subject == "6944966420",
            ).one()
            session.expunge(self.user)

    def tearDown(self) -> None:
        with SessionLocal() as session:
            session.execute(delete(OAuthRefreshToken).where(OAuthRefreshToken.client_id == self.client_id))
            session.execute(delete(OAuthAuthorizationCode).where(OAuthAuthorizationCode.client_id == self.client_id))
            session.execute(delete(OAuthClient).where(OAuthClient.client_id == self.client_id))
            session.commit()

    def test_authorization_code_exchanges_to_rotating_tokens(self) -> None:
        """OAuth code yields a valid JWT and a one-time rotatable refresh token."""
        asyncio.run(self._exercise_token_flow())

    async def _exercise_token_flow(self) -> None:
        client = OAuthClientInformationFull(
            client_id=self.client_id,
            redirect_uris=["https://client.example/callback"],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="mcp offline_access",
        )
        await self.provider.register_client(client)

        raw_code = secrets.token_urlsafe(32)
        with SessionLocal() as session:
            session.add(
                OAuthAuthorizationCode(
                    code_hash=self.provider._hash_secret(raw_code),
                    client_id=self.client_id,
                    user_id=self.user.id,
                    scopes=["mcp", "offline_access"],
                    redirect_uri="https://client.example/callback",
                    redirect_uri_provided_explicitly=True,
                    resource="https://lifelog.test/mcp",
                    code_challenge="unused-in-provider-test",
                    expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                )
            )
            session.commit()

        code = AuthorizationCode(
            code=raw_code,
            client_id=self.client_id,
            scopes=["mcp", "offline_access"],
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp(),
            code_challenge="unused-in-provider-test",
            redirect_uri="https://client.example/callback",
            redirect_uri_provided_explicitly=True,
            resource="https://lifelog.test/mcp",
            subject=str(self.user.uuid),
        )
        tokens = await self.provider.exchange_authorization_code(client, code)
        self.assertIsNotNone(tokens.refresh_token)
        access = await self.provider.load_access_token(tokens.access_token)
        self.assertIsNotNone(access)
        assert access is not None
        self.assertEqual(access.subject, str(self.user.uuid))
        self.assertEqual(access.scopes, ["mcp", "offline_access"])

        refresh = await self.provider.load_refresh_token(client, tokens.refresh_token or "")
        self.assertIsNotNone(refresh)
        assert refresh is not None
        rotated = await self.provider.exchange_refresh_token(
            client,
            refresh,
            ["mcp", "offline_access"],
        )
        self.assertNotEqual(rotated.refresh_token, tokens.refresh_token)
        self.assertIsNone(await self.provider.load_refresh_token(client, tokens.refresh_token or ""))
