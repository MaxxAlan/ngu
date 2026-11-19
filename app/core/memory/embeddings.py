"""Local hashing embedder and vector search.

No network and no model download. Hashed unigrams/bigrams are enough for
topic separation and for cosine to agree with lexical overlap. sqlite-vec
is used when the extension loads; otherwise cosine is computed over the
embedding BLOB. If that also fails, callers degrade to BM25.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import struct

log = logging.getLogger("local-agents.memory.embed")

DIM = 256
_TOKEN = re.compile(r"[\w\u0080-\uffff]+", re.UNICODE)


def embed(text: str) -> list[float]:
    """L2-normalized hashed bag-of-words (unigrams + bigrams)."""
    tokens = [tok.lower() for tok in _TOKEN.findall(text or "") if len(tok) >= 2]
    vec = [0.0] * DIM
    if not tokens:
        return vec
    feats = list(tokens)
    feats.extend(f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False))
    for feat in feats:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % DIM
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def pack_f32(vec: list[float]) -> bytes:
    try:
        import sqlite_vec

        return sqlite_vec.serialize_float32([float(v) for v in vec])
    except Exception:  # noqa: BLE001
        return struct.pack(f"<{len(vec)}f", *[float(v) for v in vec])


def unpack_f32(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    count = len(blob) // 4
    if count <= 0:
        return []
    try:
        return list(struct.unpack(f"<{count}f", blob[: count * 4]))
    except struct.error:
        return []


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    norm_l = 0.0
    norm_r = 0.0
    for x, y in zip(left, right, strict=False):
        dot += x * y
        norm_l += x * x
        norm_r += y * y
    if norm_l <= 0.0 or norm_r <= 0.0:
        return 0.0
    return dot / math.sqrt(norm_l * norm_r)


def safe_embed(text: str) -> list[float] | None:
    try:
        return embed(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding failed (%s); storing entry without a vector", exc)
        return None
