"""Smoke tests for layered architecture and compatibility shims."""
from __future__ import annotations


def test_domain_ports_and_statuses():
    from app import store
    from app.domain import TASK_STATUSES, TaskRepositoryPort

    assert "queued" in TASK_STATUSES
    assert isinstance(store, TaskRepositoryPort) or hasattr(store, "create_task")


def test_compatibility_shims():
    from app import api, llama_runtime, llm, models, store
    from app.api import create_app
    from app.mcp_server import build_mcp

    assert callable(create_app)
    assert callable(build_mcp)
    assert hasattr(store, "init_db")
    assert hasattr(llm, "list_models")
    assert hasattr(llama_runtime, "runtime")
    assert hasattr(models, "discover_models")
    assert hasattr(api, "create_app")


def test_task_service_normalizes_attachments(tmp_path, monkeypatch):
    from app import config
    from app.application.task_service import TaskService
    from app.tools.common import PermissionDenied, set_workspace

    ws = tmp_path / "ws"
    ws.mkdir()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(config, "UPLOAD_DIR", uploads)
    set_workspace(ws)

    class FakeRepo:
        def create_task(self, **kwargs):
            return {"id": "t1", **kwargs}

    svc = TaskService(repo=FakeRepo())  # type: ignore[arg-type]
    inside = ws / "a.txt"
    inside.write_text("x", encoding="utf-8")
    assert svc.normalize_attachments([str(inside)])[0].endswith("a.txt")
    try:
        svc.normalize_attachments(["/etc/hostname"])
        raised = False
    except PermissionDenied:
        raised = True
    assert raised
