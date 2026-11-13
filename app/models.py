from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

# Friendly aliases: filename substring (lowercased) -> model name.
ALIASES: list[tuple[str, str]] = [
    ("qwen2.5-coder-1.5b", "qwen2.5-coder-1.5b"),
    ("qwen2.5-coder-3b", "qwen2.5-coder-3b"),
    ("qwen2.5-coder-14b", "qwen2.5-coder-14b"),
    ("qwen2.5-7b-instruct", "qwen2.5-7b"),
    ("qwen2.5-vl-3b", "qwen2.5-vl-3b"),
    ("gemma-4-e4b", "gemma-4-e4b"),
]


@dataclass
class ModelInfo:
    name: str
    path: Path
    mmproj: Path | None = None
    kind: str = "text"
    size: int = 0
    tags: list[str] = field(default_factory=list)

    @property
    def parameter_size(self) -> str:
        m = re.search(r"(\d+(?:\.\d+)?)[bB]", self.path.stem)
        return f"{m.group(1)}B" if m else ""

    @property
    def quantization(self) -> str:
        m = re.search(r"(Q\d+_K_[MS]|Q\d+_0|IQ\d+_[A-Z]+|Q8_0)", self.path.stem, re.I)
        return m.group(1).upper() if m else ""


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[-_.\s]+", text.lower()) if t}


def _alias_for(stem: str) -> str:
    low = stem.lower()
    for needle, name in ALIASES:
        if needle in low:
            return name
    clean = re.sub(r"[^a-z0-9_.-]+", "-", low).strip("-")
    return clean or "local-model"


def _param(text: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)\s*b", text.lower())
    return m.group(1) if m else ""


def _family(text: str) -> str:
    m = re.match(r"([a-z]+\d*)", text.lower().replace("mmproj-", ""))
    return m.group(1) if m else ""


VISION_MARKERS = {"vl", "vision", "multimodal", "llava", "clip", "siglip", "mmproj"}


def _find_mmproj(model_path: Path, mmproj_files: list[Path]) -> Path | None:
    if not mmproj_files:
        return None
    model_tokens = _tokens(model_path.stem)
    family = _family(model_path.stem)
    size = _param(model_path.stem)
    best: tuple[int, Path] | None = None
    for m in mmproj_files:
        m_tokens = _tokens(m.stem)
        # same model family, same parameter size when both declare one
        if family and _family(m.stem) and _family(m.stem) != family:
            continue
        m_size = _param(m.stem)
        if size and m_size and size != m_size:
            continue
        # a VL/vision projector only matches a VL/vision model
        if (m_tokens & VISION_MARKERS) and not (model_tokens & VISION_MARKERS):
            continue
        score = len(model_tokens & m_tokens)
        if best is None or score > best[0]:
            best = (score, m)
    if best and best[0] >= 2:
        return best[1]
    return None


def discover_models() -> list[ModelInfo]:
    if not config.MODELS_DIR.exists():
        return []
    files = list(config.MODELS_DIR.glob("*.gguf"))
    # a vision projector can be named "mmproj-*.gguf" but also
    # "Qwen2.5-VL-3B-Instruct.mmproj-fp16.gguf" -> match "mmproj" anywhere
    mmproj_files = [f for f in files if "mmproj" in f.stem.lower()]
    models: list[ModelInfo] = []
    for f in files:
        if f.name.lower().startswith("mmproj-"):
            continue
        mmproj = _find_mmproj(f, mmproj_files)
        models.append(
            ModelInfo(
                name=_alias_for(f.stem),
                path=f,
                mmproj=mmproj,
                kind="vision" if mmproj else "text",
                size=f.stat().st_size,
                tags=["vision"] if mmproj else ["text"],
            )
        )
    # de-duplicate by name, keep first
    seen: set[str] = set()
    out: list[ModelInfo] = []
    for m in sorted(models, key=lambda x: x.name):
        if m.name in seen:
            continue
        seen.add(m.name)
        out.append(m)
    return out


def get_model(name: str) -> ModelInfo | None:
    if not name:
        return None
    for m in discover_models():
        if m.name == name:
            return m
    return None


def default_model() -> ModelInfo | None:
    return get_model(config.DEFAULT_MODEL) or (discover_models()[0] if discover_models() else None)


def is_vision(name: str) -> bool:
    model = get_model(name)
    return bool(model and model.kind == "vision")
