from __future__ import annotations

import gzip
import re
from pathlib import Path
from typing import Any

from .common import ToolError, allow_path, clip, resolve_path


def parse_seb_file(file_path: Path) -> dict[str, Any]:
    """Parse a Safe Exam Browser .seb configuration file (XML plist, gzip compressed or raw)."""
    raw_data = file_path.read_bytes()
    xml_content = ""

    # Check if gzip compressed
    if raw_data.startswith(b"\x1f\x8b"):
        try:
            decompressed = gzip.decompress(raw_data)
            xml_content = decompressed.decode("utf-8", errors="replace")
        except Exception:
            pass

    if not xml_content:
        # Try raw XML / plist
        try:
            xml_content = raw_data.decode("utf-8", errors="replace")
        except Exception:
            pass

    results: dict[str, Any] = {"file": file_path.name, "size": len(raw_data)}

    if "<plist" in xml_content or "<dict>" in xml_content:
        # Extract common SEB keys
        key_matches = re.findall(
            r"<key>([^<]+)</key>\s*<(string|true|false|integer|array|data)>([^<]*)</\2>",
            xml_content,
        )
        extracted: dict[str, Any] = {}
        for k, v_type, val in key_matches:
            if v_type == "true":
                extracted[k] = True
            elif v_type == "false":
                extracted[k] = False
            elif v_type == "integer":
                extracted[k] = int(val) if val.isdigit() else val
            else:
                extracted[k] = val.strip()

        # High priority SEB security settings
        critical_keys = [
            "startURL", "quitURL", "hashedQuitPassword", "hashedAdminPassword",
            "allowVirtualMachine", "allowPreferencesWindow", "allowSwitchToApplications",
            "allowWlan", "allowFlashFullscreen", "enableAltF4", "enableCtrlEsc",
            "enableStartMenu", "enableRightMouse", "permittedProcesses", "prohibitedProcesses",
            "urlFilterEnable", "urlFilterRules", "examKeySalt", "browserExamKey",
        ]
        results["critical_settings"] = {k: extracted.get(k) for k in critical_keys if k in extracted}
        results["total_settings_found"] = len(extracted)
        results["is_encrypted"] = False
        results["all_settings"] = extracted
    else:
        results["is_encrypted"] = True
        results["note"] = (
            "File appears to be password-encrypted or binary encoded SEB format. "
            "Encrypted SEB configs require the admin/exam password to decrypt."
        )

    return results


def analyze_seb(path: str = "D:\\project\\hepii\\sep") -> str:
    """Analyze Safe Exam Browser (SEB) directory or .seb configuration file.
    Extracts security policies, lock-down settings, exam URLs, process rules, and executable configs.
    """
    allow_path(path)
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"path not found: {p}")

    out: list[str] = [f"### Safe Exam Browser (SEB) Analysis: {p}\n"]

    # 1. Single .seb file analysis
    if p.is_file() and p.suffix.lower() == ".seb":
        info = parse_seb_file(p)
        out.append(f"**Target SEB File:** {p.name} ({info['size']} bytes)")
        if info.get("is_encrypted"):
            out.append(f"- Status: 🔒 {info['note']}")
        else:
            out.append(f"- Status: 🔓 Decoded Plist XML ({info['total_settings_found']} settings)")
            out.append("\n**Key Security & Exam Parameters:**")
            for k, v in info.get("critical_settings", {}).items():
                out.append(f"  • `{k}`: **{v}**")
        return clip("\n".join(out), 8000)

    # 2. Directory Analysis (like D:\project\hepii\sep)
    if p.is_dir():
        entries = list(p.rglob("*"))
        subdirs = [d.name for d in p.iterdir() if d.is_dir()]
        seb_files = [f for f in entries if f.suffix.lower() == ".seb"]
        config_files = [f for f in entries if f.suffix.lower() in {".config", ".xml"}]
        exe_files = [f for f in entries if f.suffix.lower() == ".exe"]
        dll_files = [f for f in entries if f.suffix.lower() == ".dll"]

        out.append(f"**Directory Overview:** {len(entries)} total items")
        out.append(f"- Sub-components: `{', '.join(subdirs)}`")
        out.append(f"- Executables ({len(exe_files)}): `{', '.join(f.name for f in exe_files)}`")
        out.append(f"- DLL Libraries: {len(dll_files)} libraries")
        out.append(f"- Configuration files: {len(config_files)} files")
        out.append(f"- .seb Exam Configs: {len(seb_files)} files\n")

        # Check .config files for interesting settings
        if config_files:
            out.append("#### Important Configuration (.config) Findings:")
            for cfg in config_files[:8]:
                try:
                    text = cfg.read_text(encoding="utf-8", errors="replace")
                    # Find endpoints, appSettings, connectionStrings
                    endpoints = re.findall(r'<endpoint\s+address="([^"]+)"', text)
                    keys = re.findall(r'<add\s+key="([^"]+)"\s+value="([^"]+)"', text)
                    if endpoints or keys or "system.serviceModel" in text:
                        out.append(f"\n**{cfg.name}** (`{cfg.relative_to(p)}`):")
                        if endpoints:
                            out.append(f"  - WCF/Service Endpoints: {endpoints}")
                        if keys:
                            out.append(f"  - AppSettings: {dict(keys[:5])}")
                except Exception:
                    pass

        # Inspect any .seb files inside
        if seb_files:
            out.append("\n#### .seb Files Found:")
            for sf in seb_files[:5]:
                s_info = parse_seb_file(sf)
                out.append(f"- `{sf.name}`: {'🔒 Encrypted' if s_info.get('is_encrypted') else '🔓 Decoded'}")
                if not s_info.get("is_encrypted") and s_info.get("critical_settings"):
                    for k, v in list(s_info["critical_settings"].items())[:4]:
                        out.append(f"    • {k}: {v}")

        # Check for SafeExamBrowser components
        out.append("\n#### Key Architecture Components:")
        components_map = {
            "SafeExamBrowser.Service.exe": "Windows Service running with SYSTEM privileges to enforce desktop lock-down & policy",
            "SEBConfigTool.exe": "GUI Configuration Editor used to create & encrypt .seb files",
            "SafeExamBrowser.ResetUtility.exe": "Emergency recovery tool to restore Windows registry & Explorer shell if crashed",
            "CefSharp.BrowserSubprocess.exe": "Chromium Embedded Framework (CEF) browser rendering process",
            "seb_x64.dll": "Native C++ 64-bit hook library intercepting keyboard shortcuts (Win key, Ctrl+Alt+Del, Alt+Tab)",
        }
        for exe_name, desc in components_map.items():
            found = any(f.name.lower() == exe_name.lower() for f in entries)
            status = "✅ Present" if found else "❌ Not found"
            out.append(f"- **{exe_name}**: {status} — {desc}")

        out.append("\n💡 **Usage Tip:** To extract passwords or bypass rules, inspect the .seb files or look at `SEBConfigTool.exe.config` and the local registry `HKCU\\Software\\ETH Zuerich\\SafeExamBrowser`.")
        return clip("\n".join(out), 8000)

    return f"Path is neither a directory nor a .seb file: {p}"


def inspect_project(path: str = ".", focus: str | None = None) -> str:
    """Deeply inspect a software project or repository.
    Analyzes project architecture, dependencies, tech stack (Rust, Node, Python, .NET, Tauri, C++),
    entry points, and configuration files in a single call.
    """
    allow_path(path)
    p = resolve_path(path)
    if not p.exists() or not p.is_dir():
        raise ToolError(f"project directory not found: {p}")

    out: list[str] = [f"### Project Architecture Inspection: {p.name} ({p})\n"]

    entries = list(p.iterdir())
    names = {e.name.lower() for e in entries}

    # Detect Tech Stacks
    stacks: list[str] = []
    if "cargo.toml" in names:
        stacks.append("Rust (Cargo)")
    if "package.json" in names:
        stacks.append("Node.js / Web")
    if "tauri.conf.json" in names or any(p.glob("**/tauri.conf.json")):
        stacks.append("Tauri (Desktop App)")
    if "pyproject.toml" in names or "requirements.txt" in names:
        stacks.append("Python")
    if any(f.suffix.lower() == ".sln" or f.suffix.lower() == ".csproj" for f in entries):
        stacks.append(".NET / C#")
    if "cmakelists.txt" in names or "makefile" in names:
        stacks.append("C / C++")
    if "go.mod" in names:
        stacks.append("Go")

    out.append(f"**Detected Tech Stack:** {', '.join(stacks) if stacks else 'Generic Project / Data'}")

    # Inspect Readme
    readme = next((e for e in entries if e.name.lower().startswith("readme")), None)
    if readme:
        try:
            r_text = readme.read_text(encoding="utf-8", errors="replace")[:1200]
            out.append(f"\n#### README Preview:\n{r_text.strip()}\n")
        except Exception:
            pass

    # Inspect Key Configs
    configs_to_check = [
        "Cargo.toml", "package.json", "pyproject.toml", "tauri.conf.json",
        "app-flow.txt", "docker-compose.yml", ".env.example",
    ]
    out.append("#### Key Configuration Files:")
    for c_name in configs_to_check:
        c_path = p / c_name
        if c_path.exists():
            try:
                c_text = c_path.read_text(encoding="utf-8", errors="replace")
                out.append(f"**{c_name}** preview:\n```\n{c_text[:800].strip()}\n```")
            except Exception:
                pass

    # Directory Structure Tree (Depth 2)
    out.append("\n#### Top-level Structure:")
    for e in sorted(entries, key=lambda x: (x.is_file(), x.name.lower()))[:30]:
        kind = "[DIR]" if e.is_dir() else "[FILE]"
        size = f" ({e.stat().st_size:,} bytes)" if e.is_file() else ""
        out.append(f"  {kind:6} {e.name}{size}")

    return clip("\n".join(out), 8000)
