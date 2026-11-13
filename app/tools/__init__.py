"""Modular toolset for local-agents (inspired by opencode-style separation).

Public API kept compatible with legacy `app.tools` module:
- TOOLS, to_openai_tools(), execute(), set_workspace(), resolve_path, WORKSPACE
- ToolError, PermissionDenied
- cac ham tien ich: read_file/write_file/append_file/edit_file/list_dir/
  list_tree/glob_files/search_files/make_dir/remove_path/move_path/run_shell
"""

from . import fs, search, shell
from .common import WORKSPACE, PermissionDenied, ToolError, resolve_path, set_workspace
from .fs import (
    append_file,
    edit_file,
    glob_files,
    list_dir,
    list_tree,
    make_dir,
    move_path,
    read_file,
    remove_path,
    write_file,
)
from .registry import TOOLS, execute, to_openai_tools
from .search import search_files
from .shell import run_shell

__all__ = [
    "TOOLS",
    "ToolError",
    "PermissionDenied",
    "WORKSPACE",
    "execute",
    "to_openai_tools",
    "set_workspace",
    "resolve_path",
    "read_file",
    "write_file",
    "append_file",
    "edit_file",
    "list_dir",
    "list_tree",
    "glob_files",
    "search_files",
    "make_dir",
    "remove_path",
    "move_path",
    "run_shell",
    "fs",
    "search",
    "shell",
]
