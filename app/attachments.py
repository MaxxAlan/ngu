from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from . import config

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
TEXT_EXT = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
    ".html", ".css", ".csv", ".xml", ".ini", ".cfg", ".log", ".sh", ".ps1",
    ".bat", ".cmd", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".hpp",
}


def upload_dir(task_id: str) -> Path:
    d = config.UPLOAD_DIR / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(task_id: str, filename: str, data: bytes) -> Path:
    safe = Path(filename).name
    path = upload_dir(task_id) / safe
    path.write_bytes(data)
    return path


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in TEXT_EXT:
        return "text"
    return "binary"


def describe(path: Path, max_chars: int = 6000) -> dict:
    kind = classify(path)
    info = {"path": str(path), "name": path.name, "kind": kind, "size": path.stat().st_size}
    if kind == "text":
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        info["preview"] = text[:max_chars]
        if len(text) > max_chars:
            info["preview"] += f"\n... [truncated, {len(text)} chars total]"
    return info


def build_content(prompt: str, paths: list[Path]) -> list[dict] | str:
    """Build OpenAI-style multimodal content. Images become image_url parts."""
    images = [p for p in paths if classify(p) == "image"]
    texts = [p for p in paths if classify(p) == "text"]
    others = [p for p in paths if classify(p) == "binary"]

    if not images:
        parts_text = prompt
        for p in texts:
            info = describe(p)
            parts_text += f"\n\n--- file: {info['name']} ---\n{info.get('preview', '')}"
        for p in others:
            parts_text += f"\n\n--- file attached: {p.name} ({p.stat().st_size} bytes, binary) ---"
        return parts_text

    content: list[dict] = [{"type": "text", "text": prompt}]
    for p in texts:
        info = describe(p)
        content.append({"type": "text", "text": f"--- file: {info['name']} ---\n{info.get('preview', '')}"})
    for p in images:
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
    return content
