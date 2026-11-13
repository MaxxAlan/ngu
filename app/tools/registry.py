from __future__ import annotations

from typing import Any

from . import bulk, fs, memory, office, search, shell, web
from .common import ToolError

# Tool specs (OpenAI-compatible). Keep small, clear set for local/small models.
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file with line numbers. Use offset/limit for large files. ALWAYS read a file before editing it.",
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
            "description": "Create or overwrite a file. Creates parent directories. When asked to write/create code or text, USE THIS tool to actually save the file.",
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
            "description": "Replace exact old_text with new_text in a file. ALWAYS read_file first to get exact content. Fails if old_text not found or ambiguous (unless replace_all).",
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
            "description": (
                "List a directory. By default lists immediate children only. "
                "Set recursive=true for a full tree (skips .git/node_modules/.venv/__pycache__). "
                "Set pattern to a glob (e.g. '*.py', '**/*.ts') to find files/dirs by name instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "recursive": {"type": "boolean", "default": False},
                    "pattern": {"type": "string", "description": "Glob pattern; when set, searches by name instead of listing children."},
                    "max_depth": {"type": "integer", "description": "Only used with recursive=true.", "default": 3},
                    "max_results": {"type": "integer", "description": "Max entries returned.", "default": 400},
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
            "name": "run_shell",
            "description": (
                "Run a shell command in the workspace. Use for git/build/tests/scripts, "
                "and also for mkdir/move/rename/delete (e.g. 'mkdir foo', 'mv a b', 'rm -r foo'). "
                "Output is truncated."
            ),
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
    # ---- memory tools -------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": (
                "Save a note, fact, snippet, or reference to persistent memory so it can be found later "
                "without re-reading files. Use this to cache important findings mid-task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Short unique identifier, e.g. 'api_design' or 'bug_root_cause'."},
                    "content": {"type": "string", "description": "The text/snippet to store."},
                    "tags": {"type": "string", "description": "Space-separated tags for search, e.g. 'python config error'.", "default": ""},
                },
                "required": ["key", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Search persistent memory by keyword/phrase. Use before reading large files to check if the info was already cached.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords to search for."},
                    "limit": {"type": "integer", "description": "Max results.", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    # ---- data tools ----------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "inspect_data",
            "description": "Inspect structured data files (CSV, JSON, SQLite, Parquet, XML). Returns schema, record count, and sample data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to data file."},
                },
                "required": ["path"],
            },
        },
    },
    # ---- web search tools ---------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using DuckDuckGo. No API key needed. Returns title, URL, and snippet for each result. Use for current information, documentation, or any factual lookup.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string."},
                    "max_results": {"type": "integer", "description": "Maximum number of results (1-20).", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_files_tool",
            "description": (
                "Read EVERY file inside a folder or archive (.zip/.tar/.tar.gz) in one call: "
                "office docs (xlsx/docx/pdf), images (OCR via vision model), text/code, all combined "
                "into one report. Use this instead of list_dir+read_file one by one when the user "
                "points at a whole folder or a .zip/.tar and asks what's inside / to read it all."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder or archive path."},
                    "max_files": {"type": "integer", "description": "Max files to read.", "default": 40},
                    "ocr": {"type": "boolean", "description": "Run OCR on images.", "default": True},
                    "ocr_limit": {"type": "integer", "description": "Max images to OCR (cost control).", "default": 5},
                },
                "required": ["path"],
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
        path = str(args.get("path", "."))
        pattern = args.get("pattern")
        if pattern:
            return fs.glob_files(str(pattern), path, int(args.get("max_results", 400)))
        if bool(args.get("recursive", False)):
            return fs.list_tree(path, int(args.get("max_depth", 3)), int(args.get("max_results", 400)))
        return fs.list_dir(path)
    if name == "search_files":
        return search.search_files(
            str(args.get("pattern", "")),
            str(args.get("path", ".")),
            str(args.get("glob", "*")),
            int(args.get("max_results", 50)),
        )
    if name == "run_shell":
        timeout = args.get("timeout", 60)
        try:
            timeout_f = float(timeout)
        except Exception:  # noqa: BLE001
            timeout_f = 60.0
        return await shell.run_shell(str(args.get("command", "")), timeout_f)
    # memory tools
    if name == "memory_save":
        return memory.memory_save(str(args.get("key", "")), str(args.get("content", "")), str(args.get("tags", "")))
    if name == "memory_search":
        return memory.memory_search(str(args.get("query", "")), int(args.get("limit", 5)))
    # data tools
    if name == "inspect_data":
        return office.inspect_data(str(args.get("path", "")))
    if name == "read_files_tool":
        return await bulk.read_files_tool(
            str(args.get("path", "")),
            int(args.get("max_files", 40)),
            bool(args.get("ocr", True)),
            int(args.get("ocr_limit", 5)),
        )
    if name == "web_search":
        return web.web_search(str(args.get("query", "")), int(args.get("max_results", 5)))

    raise ToolError(
        f"unknown tool: {name}. Available tools: " + ", ".join(t["function"]["name"] for t in TOOLS)
    )


def to_openai_tools() -> list[dict]:
    return TOOLS
