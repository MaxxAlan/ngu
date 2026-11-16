"""Compatibility shim — GGUF model catalog."""
from app.infrastructure.models.catalog import *  # noqa: F403
from app.infrastructure.models.catalog import (  # noqa: F401
    ALIASES,
    VISION_MARKERS,
    ModelInfo,
    _alias_for,
    _family,
    _find_mmproj,
    _param,
    _tokens,
    default_model,
    discover_models,
    get_model,
    is_vision,
)
