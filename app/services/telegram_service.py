"""Telegram OIDC integration used by the Lifelog authorization server."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.config.settings import Settings, get_settings


TELEGRAM_AUTHORIZATION_URL = "https://oauth.telegram.org/auth"
TELEGRAM_TOKEN_URL = "https://oauth.telegram.org/token"
TELEGRAM_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
TELEGRAM_ISSUER = "https://oauth.telegram.org"


class TelegramAuthenticationError(Exception):
    """Raised when Telegram cannot authenticate an OAuth login request."""


class TelegramOIDCService:
    """Runs the authorization-code OIDC flow with Telegram."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the service with validated application settings."""
        self._settings = settings or get_settings()
        self._jwks: dict[str, dict[str, Any]] = {}
        self._jwks_expires_at = datetime.min.replace(tzinfo=timezone.utc)

    def create_login_url(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
    ) -> str:
        """Build a Telegram OIDC authorization URL with PKCE protection."""
        settings = self._required_settings()
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("utf-8")).digest()
        ).decode("ascii").rstrip("=")
        query = urlencode(
            {
                "client_id": settings.telegram_client_id,
                "redirect_uri": str(settings.telegram_redirect_uri),
                "response_type": "code",
                "scope": "openid profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{TELEGRAM_AUTHORIZATION_URL}?{query}"

    async def authenticate_callback(
        self,
        *,
        code: str,
        expected_nonce: str,
        code_verifier: str,
    ) -> str:
        """Exchange a Telegram code and return the verified numeric user ID."""
        settings = self._required_settings()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    TELEGRAM_TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": str(settings.telegram_redirect_uri),
                        "client_id": settings.telegram_client_id,
                        "code_verifier": code_verifier,
                    },
                    auth=(
                        settings.telegram_client_id,
                        settings.telegram_client_secret.get_secret_value(),
                    ),
                )
                response.raise_for_status()
                id_token = response.json().get("id_token")
        except (httpx.HTTPError, ValueError) as error:
            raise TelegramAuthenticationError("Telegram token exchange failed") from error

        if not isinstance(id_token, str):
            raise TelegramAuthenticationError("Telegram response did not include an ID token")

        claims = await self._verify_id_token(id_token, expected_nonce)
        telegram_id = claims.get("id")
        if not isinstance(telegram_id, int | str) or not str(telegram_id).isdecimal():
            raise TelegramAuthenticationError("Telegram ID token did not include a numeric user ID")
        return str(telegram_id)

    async def _verify_id_token(self, id_token: str, expected_nonce: str) -> dict[str, Any]:
        """Verify Telegram's signature, audience, issuer, lifetime, and nonce."""
        settings = self._required_settings()
        try:
            header = jwt.get_unverified_header(id_token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise TelegramAuthenticationError("Unexpected Telegram ID token header")
            key = await self._get_verification_key(header["kid"])
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256"],
                audience=settings.telegram_client_id,
                issuer=TELEGRAM_ISSUER,
                leeway=30,
            )
        except (jwt.PyJWTError, ValueError) as error:
            raise TelegramAuthenticationError("Telegram ID token validation failed") from error

        if not secrets.compare_digest(str(claims.get("nonce", "")), expected_nonce):
            raise TelegramAuthenticationError("Telegram ID token nonce did not match")
        return claims

    async def _get_verification_key(self, key_id: str):
        """Return a current Telegram signing key, refreshing the JWKS cache if needed."""
        now = datetime.now(timezone.utc)
        if now >= self._jwks_expires_at or key_id not in self._jwks:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(TELEGRAM_JWKS_URL)
                    response.raise_for_status()
                    keys = response.json()["keys"]
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                raise TelegramAuthenticationError("Telegram signing keys are unavailable") from error
            self._jwks = {
                key["kid"]: key
                for key in keys
                if isinstance(key, dict) and isinstance(key.get("kid"), str)
            }
            self._jwks_expires_at = now + timedelta(hours=1)

        jwk = self._jwks.get(key_id)
        if jwk is None:
            raise TelegramAuthenticationError("Telegram signing key was not found")
        return jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))

    def _required_settings(self) -> Settings:
        """Return settings after asserting that this service is enabled."""
        if not self._settings.auth_enabled:
            raise TelegramAuthenticationError("Authentication is disabled")
        if (
            self._settings.telegram_client_id is None
            or self._settings.telegram_client_secret is None
            or self._settings.telegram_redirect_uri is None
        ):
            raise TelegramAuthenticationError("Telegram OIDC settings are incomplete")
        return self._settings
