"""Общий экземпляр FastMCP для всех инструментов приложения."""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


mcp = FastMCP(
    "Lifelog",
    stateless_http=True,
    json_response=True,
    host="127.0.0.1",
    port=8001,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["mcp.jesarion.com"],
    ),
)
