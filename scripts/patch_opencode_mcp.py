"""Dang ky MCP server 'local-agents' vao opencode global config (JSON).

Dung khi may khong co lenh `opencode`. Giu nguyen cac key dang co.
Usage: python patch_opencode_mcp.py <url>
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

SERVER_NAME = "local-agents"


def config_path() -> Path:
    home = Path.home()
    candidates = [
        home / ".config" / "opencode" / "opencode.json",
        home / ".config" / "opencode" / "opencode.jsonc",
        home / "AppData" / "Roaming" / "opencode" / "opencode.json",
    ]
    env = os.environ.get("OPENCODE_CONFIG")
    if env:
        candidates.insert(0, Path(env))
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def strip_jsonc(text: str) -> str:
    """Bo comment // va /* */ giong du khi file la jsonc."""
    out = []
    in_str = False
    esc = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else "http://127.0.0.1:8765/mcp"
    path = config_path()
    data: dict = {}

    if path.exists():
        raw = path.read_text(encoding="utf-8-sig")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:
                data = json.loads(strip_jsonc(raw))
                print("note: doc file jsonc (da bo comment)")
            except json.JSONDecodeError as exc:
                print(f"error: khong parse duoc {path}: {exc}")
                print("error: hay chay tay: opencode mcp add local-agents --url " + url + " --global")
                return 1

    if not isinstance(data, dict):
        data = {}

    mcp = data.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
        data["mcp"] = mcp
    servers = mcp.get("servers")
    if not isinstance(servers, dict):
        servers = {}
        mcp["servers"] = servers

    servers[SERVER_NAME] = {
        "type": "remote",
        "url": url,
        "oauth": False,
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = path.with_name(path.name + f".bak-{stamp}")
            shutil.copy2(path, backup)
            print(f"backup: {backup}")
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"error: khong ghi duoc {path}: {exc}")
        return 1

    print(f"da dang ky MCP '{SERVER_NAME}' -> {url} trong {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
