"""Compatibility shim — GGUF model catalog.

Generation backends live in the ``backends`` subpackage and are constructed
only by ``app.router``. This package stays the catalog so ``import app.models``
keeps working.
"""
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
