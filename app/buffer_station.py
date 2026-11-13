from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("local-agents.buffer")


class AIBufferStation:
    """Resource-saving Buffer Station for local LLM execution.
    Designed specifically for resource-constrained hardware (e.g., GTX 1650 4GB VRAM):
    1. Response & Prompt Cache: avoids redundant inference on duplicate queries.
    2. Context Compressor: trims historical tool outputs to keep context small and fast.
    3. Tool Call Memoization: caches read-only tool results.
    4. Hardware Throttler / Model Warmth Tracker.
    """

    def __init__(self, cache_file: Path | None = None) -> None:
        self.cache_dir = config.DATA_DIR / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = cache_file or (self.cache_dir / "station_cache.json")
        self._mem_cache: dict[str, dict[str, Any]] = {}
        self._tool_cache: dict[str, tuple[float, str]] = {}
        self._current_warm_model: str | None = None
        self._last_active_time: float = 0.0
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._mem_cache = data
            except Exception as exc:
                log.warning("could not load buffer station cache: %s", exc)

    def _save_cache(self) -> None:
        try:
            # Keep cache size under 500 entries
            if len(self._mem_cache) > 500:
                # prune oldest
                sorted_keys = sorted(
                    self._mem_cache.keys(),
                    key=lambda k: self._mem_cache[k].get("time", 0),
                )
                for k in sorted_keys[:100]:
                    del self._mem_cache[k]
            self.cache_file.write_text(
                json.dumps(self._mem_cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("could not save buffer station cache: %s", exc)

    def hash_key(self, model: str, messages: list[dict[str, Any]]) -> str:
        """Create a deterministic hash for a given conversation state."""
        raw = json.dumps({"model": model, "messages": messages}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_cached_response(self, model: str, messages: list[dict[str, Any]]) -> str | None:
        """Return cached assistant answer if identical query was previously processed."""
        # Only cache if last message is a user message without volatile attachments
        if not messages or messages[-1].get("role") != "user":
            return None
        key = self.hash_key(model, messages)
        entry = self._mem_cache.get(key)
        if entry:
            # TTL: 24 hours
            if time.time() - entry.get("time", 0) < 86400:
                log.info("BufferStation HIT: returning cached response (saved GPU inference)")
                return entry.get("response")
        return None

    def store_cached_response(self, model: str, messages: list[dict[str, Any]], response: str) -> None:
        """Store an answer into the cache."""
        if not response or len(response.strip()) < 10:
            return
        key = self.hash_key(model, messages)
        self._mem_cache[key] = {
            "time": time.time(),
            "model": model,
            "response": response,
        }
        self._save_cache()

    def compress_messages_for_vram(
        self,
        messages: list[dict[str, Any]],
        max_total_chars: int = 14000,
    ) -> list[dict[str, Any]]:
        """Compress older messages and historical tool outputs.
        Prevents context bloating which causes VRAM exhaustion and severe token slowdown.
        """
        if not messages:
            return messages

        compressed: list[dict[str, Any]] = []
        head = messages[:1]  # System prompt
        body = messages[1:]

        # Process from newest to oldest
        reversed_body = list(reversed(body))
        budget = max_total_chars
        kept_body: list[dict[str, Any]] = []

        for idx, msg in enumerate(reversed_body):
            role = msg.get("role")
            content = msg.get("content") or ""

            # Keep latest 3 messages intact
            if idx < 3:
                kept_body.append(msg)
                budget -= len(str(content))
                continue

            # For older tool outputs, aggressively summarize/truncate
            if role == "tool":
                str_content = str(content)
                if len(str_content) > 300:
                    truncated = str_content[:250] + f"\n... [buffer station: compressed older tool output {len(str_content)} chars]"
                    new_msg = dict(msg)
                    new_msg["content"] = truncated
                    kept_body.append(new_msg)
                    budget -= len(truncated)
                    continue

            # Older assistant messages with large code
            if role == "assistant" and len(str(content)) > 1500:
                str_content = str(content)
                truncated = str_content[:600] + f"\n... [previous answer truncated to save context]"
                new_msg = dict(msg)
                new_msg["content"] = truncated
                kept_body.append(new_msg)
                budget -= len(truncated)
                continue

            if budget > 0:
                kept_body.append(msg)
                budget -= len(str(content))
            else:
                # Out of budget for older turns, skip very old turns
                break

        return head + list(reversed(kept_body))

    def get_tool_cache(self, tool_name: str, args: dict[str, Any]) -> str | None:
        """Cache read-only tool results (e.g. read_file, inspect_data, analyze_seb)."""
        read_only_tools = {"read_file", "list_dir", "list_tree", "read_excel", "read_docx", "read_pdf", "inspect_data", "analyze_seb"}
        if tool_name not in read_only_tools:
            return None
        raw_key = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        if raw_key in self._tool_cache:
            ts, val = self._tool_cache[raw_key]
            if time.time() - ts < 120:  # 2 minute cache
                return val
        return None

    def set_tool_cache(self, tool_name: str, args: dict[str, Any], result: str) -> None:
        read_only_tools = {"read_file", "list_dir", "list_tree", "read_excel", "read_docx", "read_pdf", "inspect_data", "analyze_seb"}
        if tool_name not in read_only_tools:
            return
        raw_key = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        self._tool_cache[raw_key] = (time.time(), result)


# Global Buffer Station instance
station = AIBufferStation()
