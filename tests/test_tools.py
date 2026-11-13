import pytest

from app import tools
from app.tools.common import PermissionDenied


def test_write_and_read(workspace):
    tools.write_file("notes.txt", "hello world")
    out = tools.read_file("notes.txt")
    assert "hello world" in out


def test_append_file(workspace):
    tools.write_file("log.txt", "a\n")
    tools.append_file("log.txt", "b\n")
    assert "b" in tools.read_file("log.txt")


def test_edit_file(workspace):
    tools.write_file("a.py", "print(1)\n")
    out = tools.edit_file("a.py", "print(1)", "print(2)")
    assert "edited" in out
    assert "print(2)" in tools.read_file("a.py")


def test_edit_file_requires_existing_text(workspace):
    tools.write_file("b.py", "x = 1\n")
    with pytest.raises(tools.ToolError):
        tools.edit_file("b.py", "khong ton tai", "y")


def test_list_dir(workspace):
    tools.write_file("one.txt", "1")
    tools.write_file("two.txt", "2")
    out = tools.list_dir(".")
    assert "one.txt" in out and "two.txt" in out


def test_list_tree(workspace):
    (workspace / "sub").mkdir()
    tools.write_file("sub/x.txt", "x")
    out = tools.list_tree(".")
    assert "sub" in out


def test_glob_files(workspace):
    tools.write_file("a.py", "x")
    tools.write_file("b.txt", "y")
    out = tools.glob_files("*.py")
    assert "a.py" in out
    assert "b.txt" not in out


def test_make_and_remove_dir(workspace):
    tools.make_dir("newdir")
    assert (workspace / "newdir").is_dir()
    tools.remove_path("newdir", recursive=True)
    assert not (workspace / "newdir").exists()


def test_move_path(workspace):
    tools.write_file("src.txt", "data")
    tools.move_path("src.txt", "dst.txt")
    assert (workspace / "dst.txt").exists()
    assert not (workspace / "src.txt").exists()


def test_path_outside_workspace_is_denied(workspace, tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(PermissionDenied):
        tools.read_file(str(outside))


def test_tool_registry_has_expected_names():
    names = {t["function"]["name"] for t in tools.TOOLS}
    for expected in (
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "search_files",
        "run_shell",
    ):
        assert expected in names
