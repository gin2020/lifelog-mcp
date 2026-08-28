"""OAuth authorization-server adapter for FastMCP."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from cryptography.fernet import Fernet
import jwt
from sqlalchemy import select

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.config.settings import Settings, get_settings
from app.db.database import SessionLocal
from app.db.models.oauth import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthRefreshToken,
)
from app.db.models.user import User, UserIdentity
from app.services.telegram_service import TelegramAuthenticationError, TelegramOIDCService


AUTHORIZATION_REQUEST_TTL = timedelta(minutes=10)
AUTHORIZATION_CODE_TTL = timedelta(minutes=5)


class LifelogOAuthProvider(OAuthAuthorizationServerProvider):
    """Persist OAuth clients, login state, codes, and refresh tokens in PostgreSQL."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize the provider and the Telegram OIDC client."""
        self._settings = settings or get_settings()
        self._telegram = TelegramOIDCService(self._settings)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Load a dynamically registered MCP OAuth client."""
        with SessionLocal() as session:
            client = session.scalar(
                select(OAuthClient).where(OAuthClient.client_id == client_id)
            )
            if client is None:
                return None
            metadata = dict(client.client_metadata)
            metadata["client_secret"] = (
                self._decrypt_client_secret(client.client_secret_encrypted)
                if client.client_secret_encrypted
                else None
            )
            return OAuthClientInformationFull.model_validate(metadata)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Persist a client registered through OAuth Dynamic Client Registration."""
        if client_info.client_id is None:
            raise RegistrationError("invalid_client_metadata", "client_id is required")
        if client_info.token_endpoint_auth_method == "private_key_jwt":
            raise RegistrationError(
                "invalid_client_metadata",
                "private_key_jwt is not supported",
            )

        metadata = client_info.model_dump(mode="json")
        client_secret = metadata.pop("client_secret", None)
        encrypted_secret = (
            self._encrypt_client_secret(client_secret)
            if isinstance(client_secret, str)
            else None
        )
        with SessionLocal() as session:
            existing = session.scalar(
                select(OAuthClient).where(OAuthClient.client_id == client_info.client_id)
            )
            if existing is not None:
                raise RegistrationError("invalid_client_metadata", "client_id is already registered")
            session.add(
                OAuthClient(
                    client_id=client_info.client_id,
                    client_metadata=metadata,
                    client_secret_encrypted=encrypted_secret,
                )
            )
            session.commit()

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Store the MCP request and redirect the browser to Telegram Login."""
        if client.client_id is None:
            raise AuthorizeError("invalid_request", "client_id is required")

        request_id = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            session.add(
                OAuthAuthorizationRequest(
                    request_hash=self._hash_secret(request_id),
                    client_id=client.client_id,
                    scopes=params.scopes or ["mcp", "offline_access"],
                    client_state=params.state,
                    redirect_uri=str(params.redirect_uri),
                    redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                    resource=params.resource,
                    code_challenge=params.code_challenge,
                    telegram_nonce=secrets.token_urlsafe(32),
                    telegram_code_verifier=secrets.token_urlsafe(64),
                    expires_at=now + AUTHORIZATION_REQUEST_TTL,
                )
            )
            session.commit()

        return f"{str(self._required_settings().oauth_issuer_url).rstrip('/')}/auth/telegram/start?{urlencode({'request_id': request_id})}"

    async def get_telegram_login_url(self, request_id: str) -> str:
        """Return the Telegram redirect URL for a valid pending MCP authorization."""
        request = self._load_pending_request(request_id)
        return self._telegram.create_login_url(
            state=request_id,
            nonce=request.telegram_nonce,
            code_verifier=request.telegram_code_verifier,
        )

    async def complete_telegram_login(
        self,
        *,
        request_id: str,
        state: str,
        code: str,
    ) -> str:
        """Finish Telegram Login and redirect the MCP client with an authorization code."""
        if not secrets.compare_digest(request_id, state):
            raise TelegramAuthenticationError("Telegram OAuth state did not match")

        pending = self._load_pending_request(request_id)
        telegram_id = await self._telegram.authenticate_callback(
            code=code,
            expected_nonce=pending.telegram_nonce,
            code_verifier=pending.telegram_code_verifier,
        )
        authorization_code = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)

        with SessionLocal() as session:
            request = session.scalar(
                select(OAuthAuthorizationRequest)
                .where(
                    OAuthAuthorizationRequest.request_hash
                    == self._hash_secret(request_id),
                    OAuthAuthorizationRequest.expires_at > now,
                )
                .with_for_update()
            )
            if request is None:
                raise TelegramAuthenticationError("Authorization request has expired")
            user = self._get_or_create_telegram_user(session, telegram_id)
            session.add(
                OAuthAuthorizationCode(
                    code_hash=self._hash_secret(authorization_code),
                    client_id=request.client_id,
                    user_id=user.id,
                    scopes=request.scopes,
                    redirect_uri=request.redirect_uri,
                    redirect_uri_provided_explicitly=request.redirect_uri_provided_explicitly,
                    resource=request.resource,
                    code_challenge=request.code_challenge,
                    expires_at=now + AUTHORIZATION_CODE_TTL,
                )
            )
            session.delete(request)
            session.commit()

        return construct_redirect_uri(
            pending.redirect_uri,
            code=authorization_code,
            state=pending.client_state,
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        """Load an unused authorization code for FastMCP's PKCE validation."""
        if client.client_id is None:
            return None
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            code = session.scalar(
                select(OAuthAuthorizationCode)
                .where(
                    OAuthAuthorizationCode.code_hash == self._hash_secret(authorization_code),
                    OAuthAuthorizationCode.client_id == client.client_id,
                    OAuthAuthorizationCode.used_at.is_(None),
                    OAuthAuthorizationCode.expires_at > now,
                )
            )
            if code is None:
                return None
            user = session.get(User, code.user_id)
            if user is None or not user.is_active:
                return None
            return AuthorizationCode(
                code=authorization_code,
                scopes=code.scopes,
                expires_at=code.expires_at.timestamp(),
                client_id=code.client_id,
                code_challenge=code.code_challenge,
                redirect_uri=code.redirect_uri,
                redirect_uri_provided_explicitly=code.redirect_uri_provided_explicitly,
                resource=code.resource,
                subject=str(user.uuid),
            )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        """Consume an authorization code and issue access and refresh tokens."""
        if client.client_id is None:
            raise TokenError("invalid_client", "client_id is required")
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            code = session.scalar(
                select(OAuthAuthorizationCode)
                .where(
                    OAuthAuthorizationCode.code_hash == self._hash_secret(authorization_code.code),
                    OAuthAuthorizationCode.client_id == client.client_id,
                    OAuthAuthorizationCode.used_at.is_(None),
                    OAuthAuthorizationCode.expires_at > now,
                )
                .with_for_update()
            )
            if code is None:
                raise TokenError("invalid_grant", "authorization code is invalid")
            user = session.get(User, code.user_id)
            if user is None or not user.is_active:
                raise TokenError("invalid_grant", "user is inactive")
            code.used_at = now
            tokens = self._issue_tokens(
                session,
                user=user,
                client_id=code.client_id,
                scopes=code.scopes,
                resource=code.resource,
            )
            session.commit()
            return tokens

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        """Load an active refresh token without exposing its stored hash."""
        if client.client_id is None:
            return None
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            token = session.scalar(
                select(OAuthRefreshToken).where(
                    OAuthRefreshToken.token_hash == self._hash_secret(refresh_token),
                    OAuthRefreshToken.client_id == client.client_id,
                    OAuthRefreshToken.expires_at > now,
                )
            )
            if token is None:
                return None
            user = session.get(User, token.user_id)
            if user is None or not user.is_active:
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=token.client_id,
                scopes=token.scopes,
                expires_at=int(token.expires_at.timestamp()),
                subject=str(user.uuid),
            )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Rotate an active refresh token and issue a fresh token pair."""
        if client.client_id is None:
            raise TokenError("invalid_client", "client_id is required")
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            token = session.scalar(
                select(OAuthRefreshToken)
                .where(
                    OAuthRefreshToken.token_hash == self._hash_secret(refresh_token.token),
                    OAuthRefreshToken.client_id == client.client_id,
                    OAuthRefreshToken.expires_at > now,
                )
                .with_for_update()
            )
            if token is None:
                raise TokenError("invalid_grant", "refresh token is invalid")
            user = session.get(User, token.user_id)
            if user is None or not user.is_active:
                raise TokenError("invalid_grant", "user is inactive")
            session.delete(token)
            tokens = self._issue_tokens(
                session,
                user=user,
                client_id=client.client_id,
                scopes=scopes,
                resource=None,
            )
            session.commit()
            return tokens

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Verify a JWT and map its subject to an active Lifelog user."""
        settings = self._required_settings()
        try:
            claims = jwt.decode(
                token,
                settings.jwt_signing_key.get_secret_value(),
                algorithms=["HS256"],
                audience=str(settings.mcp_resource_url),
                issuer=str(settings.oauth_issuer_url),
            )
            user_uuid = UUID(str(claims["sub"]))
            token_version = int(claims["token_version"])
            client_id = str(claims["client_id"])
            scopes = claims["scope"]
            expires_at = int(claims["exp"])
            if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
                return None
        except (KeyError, TypeError, ValueError, jwt.PyJWTError):
            return None

        with SessionLocal() as session:
            user = session.scalar(
                select(User).where(
                    User.uuid == user_uuid,
                    User.is_active.is_(True),
                    User.token_version == token_version,
                )
            )
            if user is None:
                return None
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            resource=str(settings.mcp_resource_url),
            subject=str(user_uuid),
            claims={"iss": str(settings.oauth_issuer_url)},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke a refresh token or invalidate all JWTs for its user."""
        with SessionLocal() as session:
            if isinstance(token, RefreshToken):
                stored = session.scalar(
                    select(OAuthRefreshToken).where(
                        OAuthRefreshToken.token_hash == self._hash_secret(token.token),
                        OAuthRefreshToken.client_id == token.client_id,
                    )
                )
                if stored is not None:
                    session.delete(stored)
            elif token.subject is not None:
                user = session.scalar(
                    select(User).where(User.uuid == UUID(token.subject)).with_for_update()
                )
                if user is not None:
                    user.token_version += 1
            session.commit()

    def _load_pending_request(self, request_id: str) -> OAuthAuthorizationRequest:
        """Load a live authorization request using its opaque browser token."""
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            request = session.scalar(
                select(OAuthAuthorizationRequest).where(
                    OAuthAuthorizationRequest.request_hash == self._hash_secret(request_id),
                    OAuthAuthorizationRequest.expires_at > now,
                )
            )
            if request is None:
                raise TelegramAuthenticationError("Authorization request has expired")
            session.expunge(request)
            return request

    @staticmethod
    def _get_or_create_telegram_user(session, telegram_id: str) -> User:
        """Resolve Telegram identity or create a new active Lifelog user."""
        identity = session.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.provider == "telegram",
                UserIdentity.provider_subject == telegram_id,
            )
            .with_for_update()
        )
        if identity is not None:
            user = session.get(User, identity.user_id)
            if user is None:
                raise TelegramAuthenticationError("Telegram identity has no user")
            return user

        user = User()
        session.add(user)
        session.flush()
        session.add(
            UserIdentity(
                user_id=user.id,
                provider="telegram",
                provider_subject=telegram_id,
            )
        )
        return user

    def _issue_tokens(
        self,
        session,
        *,
        user: User,
        client_id: str,
        scopes: list[str],
        resource: str | None,
    ) -> OAuthToken:
        """Create a signed JWT and persist its paired opaque refresh token."""
        settings = self._required_settings()
        now = datetime.now(timezone.utc)
        access_expires_at = now + timedelta(seconds=settings.jwt_access_token_ttl_seconds)
        refresh_expires_at = now + timedelta(seconds=settings.oauth_refresh_token_ttl_seconds)
        token_resource = resource or str(settings.mcp_resource_url)
        access_token = jwt.encode(
            {
                "sub": str(user.uuid),
                "client_id": client_id,
                "scope": scopes,
                "token_version": user.token_version,
                "iss": str(settings.oauth_issuer_url),
                "aud": token_resource,
                "iat": int(now.timestamp()),
                "exp": int(access_expires_at.timestamp()),
            },
            settings.jwt_signing_key.get_secret_value(),
            algorithm="HS256",
        )
        raw_refresh_token = secrets.token_urlsafe(48)
        session.add(
            OAuthRefreshToken(
                token_hash=self._hash_secret(raw_refresh_token),
                client_id=client_id,
                user_id=user.id,
                scopes=scopes,
                expires_at=refresh_expires_at,
            )
        )
        return OAuthToken(
            access_token=access_token,
            expires_in=settings.jwt_access_token_ttl_seconds,
            scope=" ".join(scopes),
            refresh_token=raw_refresh_token,
        )

    def _encrypt_client_secret(self, value: str) -> str:
        """Encrypt a DCR client secret before storing it in PostgreSQL."""
        return self._fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt_client_secret(self, value: str) -> str:
        """Decrypt a DCR client secret only for FastMCP client authentication."""
        return self._fernet().decrypt(value.encode("ascii")).decode("utf-8")

    def _fernet(self) -> Fernet:
        """Derive a dedicated symmetric encryption key from the JWT signing secret."""
        signing_key = self._required_settings().jwt_signing_key.get_secret_value()
        key = base64.urlsafe_b64encode(hashlib.sha256(signing_key.encode("utf-8")).digest())
        return Fernet(key)

    @staticmethod
    def _hash_secret(value: str) -> str:
        """Return a non-reversible digest used for stored opaque tokens."""
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _required_settings(self) -> Settings:
        """Return complete settings for the enabled authorization server."""
        if not self._settings.auth_enabled:
            raise RuntimeError("Authentication is disabled")
        if (
            self._settings.oauth_issuer_url is None
            or self._settings.mcp_resource_url is None
            or self._settings.jwt_signing_key is None
        ):
            raise RuntimeError("OAuth settings are incomplete")
        return self._settings
