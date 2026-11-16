"""Compatibility shim — MCP server lives in interfaces.mcp."""
from app.interfaces.mcp.server import *  # noqa: F403
from app.interfaces.mcp.server import build_mcp  # noqa: F401
