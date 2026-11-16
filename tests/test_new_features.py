import asyncio
import zipfile
from pathlib import Path

import pytest

from app.buffer_station import station
from app.router import get_available_roles, route_model
from app.tools import bulk, seb
from app.tools.registry import TOOLS


def test_registry_has_new_tools():
    # Model-facing tool surface: core FS/shell + memory suite + inspect/read_files/web.
    # Office readers and niche SEB tools stay importable but are not model-facing.
    tool_names = {t["function"]["name"] for t in TOOLS}
    required = {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "search_files",
        "run_shell",
        "memory_save",
        "memory_search",
        "memory_get",
        "memory_list",
        "memory_delete",
        "inspect_data",
        "read_files_tool",
    }
    assert required.issubset(tool_names)
    assert "read_excel" not in tool_names
    assert "analyze_seb" not in tool_names
    # Keep surface small for local models (memory suite is intentional).
    assert 12 <= len(tool_names) <= 16


def test_read_files_tool_folder(workspace):
    (workspace / "a.txt").write_text("hello a", encoding="utf-8")
    (workspace / "b.txt").write_text("hello b", encoding="utf-8")
    out = asyncio.run(bulk.read_files_tool(str(workspace), ocr=False))
    assert "a.txt" in out and "hello a" in out
    assert "b.txt" in out and "hello b" in out


def test_read_files_tool_zip(workspace):
    zpath = workspace / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("note.txt", "zipped content")
    out = asyncio.run(bulk.read_files_tool(str(zpath), ocr=False))
    assert "note.txt" in out and "zipped content" in out


def test_buffer_station():
    messages = [
        {"role": "system", "content": "You are ngu"},
        {"role": "user", "content": "test prompt for buffer"},
    ]
    station.store_cached_response("test-model", messages, "test response")
    cached = station.get_cached_response("test-model", messages)
    assert cached == "test response"


def test_router():
    roles = get_available_roles()
    assert roles.worker_scout
    assert roles.master_coder

    # Light task -> scout
    light = route_model("chao ban helo")
    assert light == roles.worker_scout

    # Heavy task -> master
    heavy = route_model("viet toan bo kien truc he thong refactor code phuc tap")
    assert heavy == roles.master_coder


def test_seb_analysis():
    # Local Windows path — skip on CI / machines without that tree.
    path = Path(r"D:\project\hepii\sep")
    if not path.exists():
        pytest.skip(f"SEB path not present: {path}")
    res = seb.analyze_seb(str(path))
    assert "Safe Exam Browser" in res
    assert "Application" in res or "Overview" in res


def test_inspect_project():
    path = Path(r"D:\project\hepii")
    if not path.exists():
        pytest.skip(f"project path not present: {path}")
    res = seb.inspect_project(str(path))
    assert "Rust" in res or "Tauri" in res or "Project Architecture" in res
