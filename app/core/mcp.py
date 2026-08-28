"""Общий экземпляр FastMCP для всех инструментов приложения."""

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings

from app.config.logging import configure_logging
from app.config.settings import get_settings
from app.core.oauth_provider import LifelogOAuthProvider


configure_logging()

settings = get_settings()
oauth_provider = LifelogOAuthProvider(settings) if settings.auth_enabled else None

auth_options = {}
if settings.auth_enabled:
    auth_options = {
        "auth": AuthSettings(
            issuer_url=settings.oauth_issuer_url,
            resource_server_url=settings.mcp_resource_url,
            required_scopes=["mcp"],
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["mcp", "offline_access"],
                default_scopes=["mcp", "offline_access"],
            ),
            revocation_options=RevocationOptions(enabled=True),
        ),
        "auth_server_provider": oauth_provider,
    }

mcp = FastMCP(
    "Lifelog",
    stateless_http=True,
    json_response=True,
    host="127.0.0.1",
    port=8001,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["mcp.jesarion.com"],
    ),
    **auth_options,
)
