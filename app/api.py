"""Compatibility shim — HTTP API lives in interfaces.http."""
from app.interfaces.http.api import *  # noqa: F403
from app.interfaces.http.api import create_app  # noqa: F401
