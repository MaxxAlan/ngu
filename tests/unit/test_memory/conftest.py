"""Isolated SQLite dirs for hierarchical-memory tests."""

from __future__ import annotations

import pytest

from app import config


@pytest.fixture()
def mem_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "tasks.db")
    from app.tools import memory as mem_tools

    monkeypatch.setattr(mem_tools, "_MEM_DB", None)
    monkeypatch.setattr(mem_tools, "_INITIALIZED", False)
    yield data
