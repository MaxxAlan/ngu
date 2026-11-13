"""
Entry point for `python -m app.main` or the `ngu serve` subcommand.
This module starts the web server + MCP server explicitly.

For the interactive CLI (default mode), use `app.cli.main()`.
"""
from __future__ import annotations

import uvicorn

from . import config
from .api import create_app

app = create_app()


def main() -> None:
    """Start web + MCP server (equivalent to `ngu serve`)."""
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
