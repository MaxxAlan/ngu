from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from . import config
from .models import discover_models, get_model, is_vision

log = logging.getLogger("local-agents.router")

# Heuristics for heavy coding and complex reasoning tasks
HEAVY_KEYWORDS = {
    "refactor", "architect", "architecture", "algorithm", "benchmark",
    "reverse", "decompile", "optimize", "concurrency", "asyncio",
    "pipeline", "framework", "xay dung", "kien truc", "thuat toan",
    "nang cap", "toi uu", "chuyen doi", "phan tich sau", "viet toan bo",
    "debug sau", "kho qua", "logic phuc tap", "he thong", "seb", "sep",
}

LIGHT_KEYWORDS = {
    "chao", "hello", "hi", "helo", "test", "la ai", "ban ten gi", "dich",
    "translate", "format", "json dep", "tom tat", "giai thich ngan",
    "ping", "check", "may gio", "thoi tiet",
}


@dataclass
class ModelRole:
    worker_scout: str      # "Con ga" lam tay sai, trinh sat & viec nhe
    master_coder: str      # "Thang ngon" xu ly logic nang, code kho
    vision_specialist: str # Model co mat doc anh


def get_available_roles() -> ModelRole:
    """Identify the best models available on local machine for each role."""
    models = {m.name: m for m in discover_models()}

    # Scout / Light worker: prefer 1.5b then 3b
    scout = "qwen2.5-coder-3b"
    if "qwen2.5-coder-1.5b" in models:
        scout = "qwen2.5-coder-1.5b"
    elif "qwen2.5-coder-3b" in models:
        scout = "qwen2.5-coder-3b"
    elif models:
        scout = list(models.keys())[0]

    # Master coder / Heavy lifter: prefer 7b-coder uncensored, 7b, or gemma
    master = scout
    candidates = [
        "qwen2.5-coder-7b",
        "qwen2.5-7b",
        "nemotron3-nano-4b-uncensored-hauhaucs-aggressive-q4_k_p",
        "gemma-4-e4b",
        "qwen2.5-coder-3b",
    ]
    for c in candidates:
        if c in models:
            master = c
            break

    # Vision specialist
    vision = config.DEFAULT_VISION_MODEL
    if vision not in models:
        for name, m in models.items():
            if m.kind == "vision":
                vision = name
                break

    return ModelRole(worker_scout=scout, master_coder=master, vision_specialist=vision)


def route_model(prompt: str, has_images: bool = False, current_model: str | None = None) -> str:
    """Automatically route the request to the optimal model based on workload."""
    roles = get_available_roles()

    if has_images:
        log.info("AutoRouter -> Vision specialist: %s", roles.vision_specialist)
        return roles.vision_specialist

    low = (prompt or "").lower()
    words = set(re.findall(r"\w+", low))

    # Explicit light task
    if any(k in low for k in LIGHT_KEYWORDS) and len(low) < 120 and not any(k in low for k in HEAVY_KEYWORDS):
        log.info("AutoRouter -> Light Worker ('con ga' viec nhe): %s", roles.worker_scout)
        return roles.worker_scout

    # Heavy task detection
    is_heavy = (
        any(k in low for k in HEAVY_KEYWORDS)
        or len(low) > 300
        or "```" in prompt
        or "D:\\project" in prompt
        or "hepii" in low
        or "seb" in low
        or "giai thich code" in low
    )

    if is_heavy:
        log.info("AutoRouter -> Heavy Master ('thang ngon' viec nang): %s", roles.master_coder)
        return roles.master_coder

    # If already set and valid, keep it; else default to master coder
    if current_model and current_model != "auto" and get_model(current_model):
        return current_model

    return roles.master_coder
