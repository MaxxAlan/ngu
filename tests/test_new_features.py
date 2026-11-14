import asyncio
import zipfile

from app.buffer_station import station
from app.router import get_available_roles, route_model
from app.tools import bulk, seb
from app.tools.registry import TOOLS


def test_registry_has_new_tools():
    # Consolidated to a 12-tool surface for weak local models: office readers
    # (read_excel/read_docx/read_pdf) and niche tools (analyze_seb,
    # inspect_project) are no longer model-facing - inspect_data/
    # read_files_tool cover the same ground with less ambiguity.
    tool_names = {t["function"]["name"] for t in TOOLS}
    assert len(tool_names) == 12
    assert "inspect_data" in tool_names
    assert "read_files_tool" in tool_names
    assert "read_excel" not in tool_names
    assert "analyze_seb" not in tool_names


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
    # Analyze the actual sep path on this machine
    res = seb.analyze_seb(r"D:\project\hepii\sep")
    assert "Safe Exam Browser" in res
    assert "Application" in res or "Overview" in res


def test_inspect_project():
    res = seb.inspect_project(r"D:\project\hepii")
    assert "Rust" in res or "Tauri" in res or "Project Architecture" in res
