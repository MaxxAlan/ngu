import os
from pathlib import Path
import pytest
from app.tools import office, seb
from app.tools.registry import TOOLS, execute
from app.buffer_station import station
from app.router import route_model, get_available_roles


def test_registry_has_new_tools():
    tool_names = {t["function"]["name"] for t in TOOLS}
    assert "read_excel" in tool_names
    assert "read_docx" in tool_names
    assert "read_pdf" in tool_names
    assert "inspect_data" in tool_names
    assert "analyze_seb" in tool_names
    assert "inspect_project" in tool_names


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
