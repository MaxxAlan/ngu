"""Modular toolset for local-agents.

Public API:
- TOOLS, to_openai_tools(), execute(), set_workspace(), resolve_path, WORKSPACE
- ToolError, PermissionDenied
- fs tools: read_file/write_file/append_file/edit_file/list_dir/
  list_tree/glob_files/search_files/make_dir/remove_path/move_path/run_shell
- memory tools: memory_save/memory_search/memory_list/memory_delete
- office tools: read_excel/read_docx/read_pdf/inspect_data
- bulk: read_files_tool (whole folder/archive -> one combined report)
"""

from . import bulk, fs, memory, office, search, seb, shell
from .bulk import read_files_tool
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
from .memory import memory_delete, memory_list, memory_prune, memory_save, memory_search
from .office import inspect_data, read_docx, read_excel, read_pdf
from .registry import TOOLS, execute, to_openai_tools
from .search import search_files
from .seb import analyze_seb, inspect_project
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
    "memory_save",
    "memory_search",
    "memory_list",
    "memory_delete",
    "memory_prune",
    "read_excel",
    "read_docx",
    "read_pdf",
    "inspect_data",
    "analyze_seb",
    "inspect_project",
    "read_files_tool",
    "fs",
    "search",
    "shell",
    "memory",
    "office",
    "seb",
    "bulk",
]
