"""read_files_tool: read every file inside a folder or archive (.zip/.tar) in one call.

Core idea borrowed from archive2md (recursive extract + classify + dispatch to the
right reader), but built from scratch against what this repo already has: no new
dependency, no second GPU server. Office files go through `office.py`, images go
through the vision model already wired in `router.py`.
"""

from __future__ import annotations

import base64
import mimetypes
import tarfile
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from ..attachments import IMAGE_EXT, OFFICE_EXT, TEXT_EXT
from . import office
from .common import ToolError, allow_path, clip, resolve_path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", ".ruff_cache"}
ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz"}

MAX_FILES = 40
MAX_FILE_CHARS = 1500
MAX_READ_BYTES = 2_000_000


def _iter_leaf_files(root: Path, max_files: int) -> list[Path]:
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
            continue
        out.append(p)
        if len(out) >= max_files:
            break
    return out


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    for member in zf.infolist():
        if member.filename.startswith("/") or ".." in member.filename.replace("\\", "/").split("/"):
            raise ToolError(f"unsafe path in archive: {member.filename}")
    zf.extractall(dest)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    for member in tf.getmembers():
        if member.name.startswith("/") or ".." in member.name.replace("\\", "/").split("/"):
            raise ToolError(f"unsafe path in archive: {member.name}")
    tf.extractall(dest)  # noqa: S202 - members validated above


def _extract_archive(path: Path, dest: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            _safe_extract_zip(zf, dest)
        return
    if suffix in {".tar", ".gz", ".tgz"}:
        with tarfile.open(path) as tf:
            _safe_extract_tar(tf, dest)
        return
    raise ToolError(
        f"archive format not supported: {suffix} "
        "(only .zip/.tar/.tar.gz; .rar/.7z need an extra library, add if needed)"
    )


def _read_text_snippet(p: Path) -> str:
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            raw = fh.read(MAX_READ_BYTES)
    except OSError as exc:
        return f"[cannot read: {exc}]"
    text = raw.decode("utf-8", errors="replace")[:MAX_FILE_CHARS]
    if size > MAX_FILE_CHARS:
        text += f"\n... [{size} bytes total, truncated]"
    return text


async def _describe_image(p: Path, vision_model: str) -> str:
    from .. import llm

    data = base64.b64encode(p.read_bytes()).decode("ascii")
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image and transcribe any visible text (OCR), concisely."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
            ],
        }
    ]
    try:
        resp = await llm.chat(vision_model, messages, max_tokens=500)
        choice = (resp.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or "[vision model returned nothing]"
    except Exception as exc:  # noqa: BLE001
        return f"[OCR failed: {exc}]"


async def read_files_tool(
    path: str,
    max_files: int = MAX_FILES,
    ocr: bool = True,
    ocr_limit: int = 5,
) -> str:
    """Read every file in a folder or archive (.zip/.tar) and return one combined report.

    Dispatches each file: office docs (xlsx/docx/pdf) -> office.py readers,
    images -> the vision model (OCR, capped at ocr_limit), everything else -> text preview.
    """
    allow_path(path)
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"not found: {p}")

    with TemporaryDirectory(prefix="ngu_bulk_") as tmp_str:
        tmp = Path(tmp_str)
        if p.is_file() and p.suffix.lower() in ARCHIVE_EXT:
            _extract_archive(p, tmp)
            allow_path(tmp)
            root, label, files = tmp, f"archive {p.name}", _iter_leaf_files(tmp, max_files)
        elif p.is_file():
            root, label, files = p.parent, p.name, [p]
        else:
            root, label, files = p, p.name, _iter_leaf_files(p, max_files)

        if not files:
            return f"{label}: no readable files found."

        vision_model = None
        if ocr and any(f.suffix.lower() in IMAGE_EXT for f in files):
            from ..router import get_available_roles

            vision_model = get_available_roles().vision_specialist

        out = [f"### Bulk read: {label} ({len(files)} file(s))\n"]
        ocr_used = 0
        for f in files:
            rel = f.relative_to(root)
            ext = f.suffix.lower()
            out.append(f"--- {rel} ---")
            try:
                if ext in OFFICE_EXT:
                    out.append(office.inspect_data(str(f)))
                elif ext in IMAGE_EXT:
                    if vision_model and ocr_used < ocr_limit:
                        out.append(await _describe_image(f, vision_model))
                        ocr_used += 1
                    else:
                        out.append("[image, OCR skipped (disabled or ocr_limit reached)]")
                elif ext in TEXT_EXT or ext == "":
                    out.append(_read_text_snippet(f))
                else:
                    out.append(f"[binary, {f.stat().st_size} bytes, skipped]")
            except Exception as exc:  # noqa: BLE001
                out.append(f"[error reading {rel}: {exc}]")
            out.append("")
        return clip("\n".join(out), 12000)
