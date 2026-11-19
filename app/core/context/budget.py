"""Tokenizer-aware token counting.

OpenAI-compatible names go through tiktoken. llama.cpp / GGUF names try a
local tokenizer (tokenizer.json or llama-tokenize). Anything else logs a loud
warning and uses a character heuristic — never a silent fallback.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("local-agents.budget")

_OPENAI_ENCODINGS = {
    "cl100k": "cl100k_base",
    "cl100k_base": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-3.5": "cl100k_base",
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-4o": "o200k_base",
    "gpt-4o-mini": "o200k_base",
    "o200k": "o200k_base",
    "o200k_base": "o200k_base",
    "openai": "cl100k_base",
    "openai_compat": "cl100k_base",
    "text-embedding-3-small": "cl100k_base",
    "text-embedding-3-large": "cl100k_base",
    "text-embedding-ada-002": "cl100k_base",
    "p50k": "p50k_base",
    "p50k_base": "p50k_base",
    "p50k_edit": "p50k_edit",
    "r50k": "r50k_base",
    "r50k_base": "r50k_base",
    "gpt2": "r50k_base",
}

_warned: set[str] = set()
_llama_miss: set[str] = set()


def count_chars(text: str) -> int:
    """Character length used only when LA_CONTEXT_BUDGET_STRATEGY=char."""
    return len(text or "")


def count_tokens(text: str, tokenizer_name: str = "cl100k_base") -> int:
    """Return a token count for ``text``.

    Falls back to a character heuristic only when no real tokenizer can be
    loaded. That path logs a warning the first time each name is seen.
    """
    if not text:
        return 0
    name = (tokenizer_name or "cl100k_base").strip() or "cl100k_base"
    encoder = _tiktoken_encoder(name)
    if encoder is not None:
        return len(encoder.encode(text))
    llama_count = _count_with_llama(text, name)
    if llama_count is not None:
        return llama_count
    _warn_heuristic(name)
    return _heuristic_tokens(text)


def truncate_to_budget(
    text: str,
    max_units: int,
    *,
    strategy: str,
    tokenizer_name: str,
) -> str:
    """Cut ``text`` so it fits ``max_units`` tokens or characters."""
    if not text or max_units < 1:
        return ""
    if strategy == "char":
        if count_chars(text) <= max_units:
            return text
        return text[:max_units]
    encoder = _tiktoken_encoder(tokenizer_name)
    if encoder is not None:
        ids = encoder.encode(text)
        if len(ids) <= max_units:
            return text
        return encoder.decode(ids[:max_units])
    if count_tokens(text, tokenizer_name) <= max_units:
        return text
    lo = 0
    hi = len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        chunk = text[:mid]
        if count_tokens(chunk, tokenizer_name) <= max_units:
            best = chunk
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _tiktoken_encoder(tokenizer_name: str):
    encoding = _OPENAI_ENCODINGS.get(tokenizer_name.lower())
    try:
        import tiktoken
    except Exception:  # noqa: BLE001
        return None
    try:
        if encoding:
            return tiktoken.get_encoding(encoding)
        try:
            return tiktoken.get_encoding(tokenizer_name)
        except Exception:  # noqa: BLE001
            return tiktoken.encoding_for_model(tokenizer_name)
    except Exception:  # noqa: BLE001
        return None


def _count_with_llama(text: str, tokenizer_name: str) -> int | None:
    """Best-effort llama.cpp / HuggingFace tokenizer. None if unavailable."""
    low = tokenizer_name.lower()
    path = Path(tokenizer_name)
    looks_llama = path.is_file() or any(
        token in low for token in ("llama", "gguf", "qwen", "gemma", "mistral", "nemotron")
    )
    if not looks_llama:
        return None
    if tokenizer_name in _llama_miss and not path.is_file():
        return None

    candidates: list[Path] = []
    if path.is_file():
        candidates.append(path)
    try:
        from app import config

        models = Path(config.MODELS_DIR)
        llama_dir = Path(config.LLAMA_DIR)
    except Exception:  # noqa: BLE001
        models = Path()
        llama_dir = Path()

    if models.is_dir():
        # Prefer a tokenizer that sits next to the named model, else any local one.
        named = tokenizer_name.replace("\\", "/").split("/")[-1]
        for pattern in (f"**/{named}", "**/tokenizer.json", "**/*.gguf"):
            candidates.extend(list(models.glob(pattern))[:4])

    for candidate in candidates:
        if candidate.suffix == ".json" and candidate.is_file():
            try:
                from tokenizers import Tokenizer

                tok = Tokenizer.from_file(str(candidate))
                return len(tok.encode(text).ids)
            except Exception:  # noqa: BLE001
                continue

    exe_name = "llama-tokenize.exe" if os.name == "nt" else "llama-tokenize"
    exe = llama_dir / exe_name
    gguf = next((c for c in candidates if c.suffix == ".gguf" and c.is_file()), None)
    if exe.is_file() and gguf is not None:
        try:
            proc = subprocess.run(
                [str(exe), "-m", str(gguf), "-n", "-1", "--", text[:8000]],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            if proc.returncode == 0 and lines:
                # llama-tokenize prints one token per line when -n is unset / high.
                return len(lines)
        except Exception:  # noqa: BLE001
            pass

    _llama_miss.add(tokenizer_name)
    return None


def _warn_heuristic(tokenizer_name: str) -> None:
    if tokenizer_name in _warned:
        return
    _warned.add(tokenizer_name)
    log.warning(
        "LOUD FALLBACK: tokenizer %r is unavailable (tiktoken and llama.cpp both missed). "
        "count_tokens is using a character heuristic (~4 chars/token, 1 token per CJK "
        "character). Context budgets are approximate until a real tokenizer loads.",
        tokenizer_name,
    )


def _heuristic_tokens(text: str) -> int:
    cjk = 0
    other = 0
    for ch in text:
        code = ord(ch)
        if (
            0x4E00 <= code <= 0x9FFF
            or 0x3400 <= code <= 0x4DBF
            or 0x3040 <= code <= 0x30FF
            or 0xAC00 <= code <= 0xD7AF
        ):
            cjk += 1
        else:
            other += 1
    return max(1, cjk + (other + 3) // 4)
