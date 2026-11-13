from __future__ import annotations

from typing import Any

from . import fs, search, shell
from .common import ToolError

# Tool specs (OpenAI-compatible). Keep small, clear set for local/small models.
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file with line numbers. Use offset/limit for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (relative to workspace or absolute)."},
                    "offset": {"type": "integer", "description": "0-based line offset.", "default": 0},
                    "limit": {"type": "integer", "description": "Max lines (<=2000).", "default": 400},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file. Creates parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": (
                "Append text to a file (creates it if missing). "
                "Use this to continue a long text (chapter, article) that did not fit in one call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact old_text with new_text. Fails if old_text not found or ambiguous (unless replace_all).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List immediate children of a directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tree",
            "description": "Recursively list directory tree (skips .git/node_modules/.venv/__pycache__). Good for exploring large codebases.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "max_depth": {"type": "integer", "default": 3},
                    "max_entries": {"type": "integer", "default": 400},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": "Find files/directories by glob/name pattern (e.g. '*.py', '**/*.ts', 'README.md').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "default": "*"},
                    "path": {"type": "string", "default": "."},
                    "max_results": {"type": "integer", "default": 200},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search file contents by regular expression. Returns matching file:line snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regular expression."},
                    "path": {"type": "string", "default": "."},
                    "glob": {"type": "string", "description": "File glob filter, e.g. '*.py'.", "default": "*"},
                    "max_results": {"type": "integer", "default": 50},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mkdir",
            "description": "Create a directory (including parents).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_path",
            "description": "Move or rename a file/directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "dst": {"type": "string"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_path",
            "description": "Remove a file or directory. Use recursive=true for non-empty directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean", "default": False},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command in the workspace. Use for git/build/tests/scripts. Output is truncated.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "number", "description": "Seconds.", "default": 60},
                },
                "required": ["command"],
            },
        },
    },
]


async def execute(name: str, arguments: dict[str, Any]) -> str:
    args = arguments if isinstance(arguments, dict) else {}
    if name == "read_file":
        return fs.read_file(str(args.get("path", "")), int(args.get("offset", 0)), int(args.get("limit", 400)))
    if name == "write_file":
        return fs.write_file(str(args.get("path", "")), str(args.get("content", "")))
    if name == "append_file":
        return fs.append_file(str(args.get("path", "")), str(args.get("content", "")))
    if name == "edit_file":
        return fs.edit_file(
            str(args.get("path", "")),
            str(args.get("old_text", "")),
            str(args.get("new_text", "")),
            bool(args.get("replace_all", False)),
        )
    if name == "list_dir":
        return fs.list_dir(str(args.get("path", ".")))
    if name == "list_tree":
        return fs.list_tree(str(args.get("path", ".")), int(args.get("max_depth", 3)), int(args.get("max_entries", 400)))
    if name == "glob_files":
        return fs.glob_files(str(args.get("pattern", "*")), str(args.get("path", ".")), int(args.get("max_results", 200)))
    if name == "search_files":
        return search.search_files(
            str(args.get("pattern", "")),
            str(args.get("path", ".")),
            str(args.get("glob", "*")),
            int(args.get("max_results", 50)),
        )
    if name == "mkdir":
        return fs.make_dir(str(args.get("path", "")))
    if name == "move_path":
        return fs.move_path(str(args.get("src", "")), str(args.get("dst", "")))
    if name == "remove_path":
        return fs.remove_path(str(args.get("path", "")), bool(args.get("recursive", False)))
    if name == "run_shell":
        timeout = args.get("timeout", 60)
        try:
            timeout_f = float(timeout)
        except Exception:  # noqa: BLE001
            timeout_f = 60.0
        return await shell.run_shell(str(args.get("command", "")), timeout_f)

    raise ToolError(
        f"unknown tool: {name}. Available tools: " + ", ".join(t["function"]["name"] for t in TOOLS)
    )


def to_openai_tools() -> list[dict]:
    return TOOLS
