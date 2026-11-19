"""Hybrid retrieval: BM25 + cosine + 1-hop graph, fused with RRF.

``retrieve`` never raises because sqlite-vec or the embedder failed. Those
rankers drop out with a warning and BM25 still returns.
"""

from __future__ import annotations

import logging

from app import config
from app.core.context.budget import count_tokens
from app.core.memory import sqlite_store as store
from app.core.memory.types import MemoryEntry, now_iso

log = logging.getLogger("local-agents.memory.retrieve")

RRF_K = 60
_CANDIDATES = 40


def retrieve(
    query: str,
    task_id: str | None = None,
    subtask_id: str | None = None,
    budget_tokens: int = 512,
    *,
    tokenizer_name: str | None = None,
    tags: str = "",
    limit: int = 20,
) -> list[MemoryEntry]:
    """Return scored memory entries that fit in ``budget_tokens``.

    Each entry carries ``score`` (RRF, higher is better) and ``score_parts``
    plus the original ``source_json`` provenance so the agent can cite it.
    """
    query = (query or "").strip()
    if not query or budget_tokens <= 0:
        return []
    tokenizer = tokenizer_name or getattr(config, "CONTEXT_TOKENIZER", "cl100k_base")
    if task_id:
        _sync_episodic(task_id)

    now = now_iso()
    tag_filter = (tags or "").strip().lower()
    with store.connect() as con:
        bm25_ids = store.bm25_search(con, store.fts_query(query), _CANDIDATES)
        try:
            vector_ids = store.vector_search(con, query, _CANDIDATES)
        except Exception as exc:  # noqa: BLE001
            log.warning("vector ranker crashed (%s); continuing BM25-only", exc)
            vector_ids = []
        loaded = store.load_many(con, list(dict.fromkeys([*bm25_ids, *vector_ids])))

        def visible(entry_id: str) -> bool:
            entry = loaded.get(entry_id)
            if entry is None:
                entry = store.read_entry(con, entry_id)
                if entry is not None:
                    loaded[entry_id] = entry
            if entry is None:
                return False
            if not store.is_visible(entry, task_id=task_id, subtask_id=subtask_id, now=now):
                return False
            if tag_filter and tag_filter not in (entry.tags or "").lower():
                return False
            return True

        bm25_ids = [entry_id for entry_id in bm25_ids if visible(entry_id)]
        vector_ids = [entry_id for entry_id in vector_ids if visible(entry_id)]
        scores, parts = _rrf([bm25_ids, vector_ids])
        for rank, entry_id in enumerate(bm25_ids, start=1):
            parts.setdefault(entry_id, {})["bm25_rank"] = rank
        for rank, entry_id in enumerate(vector_ids, start=1):
            parts.setdefault(entry_id, {})["vector_rank"] = rank

        seed = [entry_id for entry_id, _score in sorted(scores.items(), key=lambda item: -item[1])]
        edges = store.neighbor_edges(con, seed[:10])
        for neighbor_id, parent_id, relation, weight in edges:
            if not visible(neighbor_id):
                continue
            bonus = scores.get(parent_id, 0.0) * 0.5 * max(weight, 0.0)
            if bonus <= 0:
                continue
            previous = scores.get(neighbor_id, 0.0)
            part = parts.setdefault(neighbor_id, {})
            part["graph"] = True
            part["via"] = parent_id
            part["relation"] = relation
            if bonus > previous:
                scores[neighbor_id] = bonus
                part["rrf"] = previous

        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        chosen: list[MemoryEntry] = []
        remaining = int(budget_tokens)
        for entry_id, score in ordered:
            if len(chosen) >= limit:
                break
            entry = loaded.get(entry_id)
            if entry is None:
                continue
            cost = _entry_tokens(entry, tokenizer)
            if cost > remaining:
                continue
            entry.score = score
            entry.score_parts = dict(parts.get(entry_id, {}))
            entry.score_parts["rrf"] = score
            chosen.append(entry)
            remaining -= cost
        return chosen


def _rrf(ranked_lists: list[list[str]]) -> tuple[dict[str, float], dict[str, dict]]:
    scores: dict[str, float] = {}
    parts: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, entry_id in enumerate(ranked, start=1):
            scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (RRF_K + rank)
            parts.setdefault(entry_id, {})
    return scores, parts


def _entry_tokens(entry: MemoryEntry, tokenizer_name: str) -> int:
    origin = ""
    if isinstance(entry.source_json, dict):
        origin = str(entry.source_json.get("origin") or "")
    citation = f"{entry.type}:{entry.id} source:{origin}\n{entry.content}"
    return count_tokens(citation, tokenizer_name)


def _sync_episodic(task_id: str) -> None:
    try:
        from app.core.memory.episodic import EpisodicMemory

        EpisodicMemory().sync_from_kernel(task_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("episodic sync skipped (%s)", exc)
