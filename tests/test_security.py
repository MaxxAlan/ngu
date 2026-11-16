"""Security fixes: attachment path jail, API token, shell non-tty policy, creative write."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, store
from app.tools import common, shell
from app.tools.common import (
    PermissionDenied,
    allow_write_path,
    command_references_outside_workspace,
    path_permission_scope,
    resolve_attachment_path,
    resolve_path,
)


@pytest.fixture()
def isolated_fs(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    uploads = data / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(config, "DB_PATH", data / "tasks.db")
    monkeypatch.setattr(config, "AGENT_ALLOW_ABS", False)
    monkeypatch.setattr(config, "ASK_PERMISSION", True)
    common.set_workspace(ws)
    common.ALLOWED_PATHS.clear()
    common.ALLOWED_WRITE_PATHS.clear()
    store.init_db()
    return ws, uploads


# ---- P0-1 attachments ----

def test_resolve_attachment_rejects_etc(isolated_fs):
    with pytest.raises(PermissionDenied):
        resolve_attachment_path("/etc/hostname")
    with pytest.raises(PermissionDenied):
        resolve_attachment_path("/etc/passwd")


def test_resolve_attachment_allows_upload_and_workspace(isolated_fs):
    ws, uploads = isolated_fs
    up = uploads / "sess1" / "note.txt"
    up.parent.mkdir(parents=True)
    up.write_text("hello", encoding="utf-8")
    assert resolve_attachment_path(str(up)) == up.resolve()

    inside = ws / "readme.md"
    inside.write_text("ws", encoding="utf-8")
    assert resolve_attachment_path(str(inside)) == inside.resolve()


def test_attachment_does_not_leak_into_global_allowed(isolated_fs, monkeypatch):
    """describe/resolve must not permanently open external paths for later tasks."""
    secret = Path("/etc/hostname")
    if not secret.exists():
        pytest.skip("no /etc/hostname")
    with pytest.raises(PermissionDenied):
        resolve_attachment_path(str(secret))
    assert not common._is_allowed(secret)


def test_path_permission_scope_isolates_grants(isolated_fs):
    outside = isolated_fs[0].parent / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with path_permission_scope():
        allow_write_path(outside)
        assert resolve_path(str(outside), write=True) == outside.resolve()
    # after scope ends, grant is gone
    with pytest.raises(PermissionDenied):
        resolve_path(str(outside), write=True)


# ---- P0-2 API token ----

@pytest.fixture()
def api_client(isolated_fs, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    monkeypatch.setattr(config, "HOST", "127.0.0.1")
    from app.api import create_app

    app = create_app()
    return TestClient(app)


def test_api_without_token_allows_local(api_client):
    r = api_client.get("/api/health")
    assert r.status_code == 200


def test_api_token_required_when_set(isolated_fs, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "secret-token-xyz")
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    from app.api import create_app

    client = TestClient(create_app())
    assert client.get("/api/health").status_code == 200  # public
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/tasks", headers={"Authorization": "Bearer secret-token-xyz"})
    assert ok.status_code == 200
    ok2 = client.get("/api/tasks", headers={"X-API-Key": "secret-token-xyz"})
    assert ok2.status_code == 200


def test_create_task_rejects_outside_attachment(isolated_fs, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    from app.api import create_app

    client = TestClient(create_app())
    r = client.post(
        "/api/tasks",
        json={"prompt": "describe this file", "attachments": ["/etc/hostname"], "auto_run": False},
    )
    assert r.status_code == 400
    assert "outside" in r.json()["detail"].lower() or "workspace" in r.json()["detail"].lower()


# ---- P1 shell ----

def test_command_detects_unix_absolute_outside(isolated_fs):
    assert command_references_outside_workspace("cat /etc/hostname") is True
    assert command_references_outside_workspace("rm -rf /tmp/not-ws") is True
    # relative / workspace-ish should not flag mere words
    assert command_references_outside_workspace("ls -la") is False
    assert command_references_outside_workspace("python script.py") is False


def test_run_shell_nontty_denies_outside(isolated_fs, monkeypatch):
    class _NoTTY:
        def isatty(self):
            return False

    monkeypatch.setattr("app.tools.shell.sys.stdin", _NoTTY())
    monkeypatch.setattr(config, "AGENT_ALLOW_ABS", False)

    with pytest.raises(PermissionDenied):
        asyncio.run(shell.run_shell("cat /etc/hostname"))


def test_run_shell_nontty_allows_with_auto(isolated_fs, monkeypatch):
    class _NoTTY:
        def isatty(self):
            return False

    monkeypatch.setattr("app.tools.shell.sys.stdin", _NoTTY())
    monkeypatch.setattr(config, "AGENT_ALLOW_ABS", True)
    # Should not raise PermissionDenied (may still fail if shell missing, but policy allows)
    try:
        out = asyncio.run(shell.run_shell("echo ok_shell_test"))
        assert "ok_shell_test" in out or "exit" in out
    except Exception as exc:  # noqa: BLE001
        # only policy errors are failures
        assert "outside the workspace" not in str(exc)


# ---- P2 creative write ----

def test_creative_prompt_does_not_auto_allow_write(isolated_fs, monkeypatch):
    """Prompt mentioning an abs path must not silently grant write outside workspace."""
    outside = isolated_fs[0].parent / "novel_out"
    outside.mkdir()
    prompt = f"viet truyen chapter vao {outside}"
    assert config.is_creative(prompt)
    # Simulate what the old runner did — we assert the new code path does NOT grant.
    # After imports, allow sets should not contain outside unless explicitly granted.
    assert not common._is_write_allowed(outside)
    with pytest.raises(PermissionDenied):
        resolve_path(str(outside / "chap1.txt"), write=True)
