"""Novel project support for local-agents.

Mirrors the workflow of NovelWriter (EdwardAThomson/NovelWriter) but keeps the
flat chapter layout the author already uses:

    <story folder>/
      chapter1.txt ... chapterNN.txt      # prose (unchanged layout)
      story.meta.json                    # single metadata file (this module)

Pipeline for one chapter:
    plan -> write -> review (rules + LLM) -> retry -> persist

Metadata file holds: title, genre, pov, character roster (+ relationships), world
(place/time/atmosphere/rules), open plot threads, next-chapter ideas,
per-chapter outline/plan, word counts, review scores and quality trend.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config, llm

log = logging.getLogger("local-agents.novel")

META_NAME = "story.meta.json"
CHAPTER_RE = re.compile(r"(?i)^(?:chapter|chuong|chương)[ _-]?(\d+)$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "target_words": config.NOVEL_TARGET_WORDS,
    "min_score": config.NOVEL_MIN_SCORE,
    "max_attempts": config.NOVEL_MAX_ATTEMPTS,
    "tail_chars": config.NOVEL_TAIL_CHARS,
    "plan_model": config.NOVEL_PLAN_MODEL,
    "review_model": config.NOVEL_REVIEW_MODEL,
    "review_max_tokens": 2048,
    "plan_max_tokens": 1536,
}

WEIGHTS: dict[str, float] = {
    "coherence": 0.25,
    "pacing": 0.20,
    "prose": 0.20,
    "character": 0.20,
    "dialogue": 0.15,
}

# artefacts a small model tends to leave in the prose
ARTIFACT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?im)^\s*[A-Za-z]:\\[^\n]*\.(txt|md)\s*$", "file path line inside the prose"),
    (r"(?i)tiếp nối từ chương", "meta line 'tiep noi tu chuong'"),
    (r"(?im)^\s*\.\.\.\s*$", "ellipsis placeholder line"),
    (r"(?i)^(dưới đây là|đây là (?:nội dung|chương|phần|nội dung của))", "meta intro line"),
    (r"(?im)^\s*(?:as an ai|i cannot|i'm sorry)\b", "model meta commentary"),
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def count_words(text: str) -> int:
    """Word-ish count. Vietnamese is space separated, so this is syllable count."""
    return len([w for w in (text or "").split() if w.strip()])


def _parse_json(text: str) -> dict:
    """Tolerant JSON extraction from a model response."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return {}


async def _json_call(model: str, messages: list[dict], max_tokens: int) -> dict:
    """One completion without tools, parsed as JSON (empty dict on failure)."""
    try:
        resp = await llm.chat(model, messages, tools=None, max_tokens=max_tokens)
    except Exception as exc:  # noqa: BLE001 - review must never kill the run
        log.warning("novel: llm call failed: %s", exc)
        return {}
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return _parse_json(msg.get("content") or "")


def find_project(start: Path) -> Path | None:
    """Nearest folder (searching upwards) that holds chapter files or the meta file."""
    cur = start if start.is_dir() else start.parent
    for _ in range(6):
        if (cur / META_NAME).exists():
            return cur
        try:
            entries = list(cur.glob("*.txt"))
        except OSError:
            entries = []
        if any(CHAPTER_RE.match(p.stem) for p in entries):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


class Project:
    """Flat novel folder + single metadata file."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.meta_path = root / META_NAME
        self.meta: dict[str, Any] = self._load()

    # -- metadata ------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.meta_path.exists():
            try:
                data = json.loads(self.meta_path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("novel: cannot read %s (%s), starting fresh", self.meta_path, exc)
                data = {}
        data.setdefault("version", 1)
        data.setdefault("title", self.root.name)
        data.setdefault("genre", "")
        data.setdefault("pov", [])
        data.setdefault("characters", {})
        data.setdefault("world", {})
        data.setdefault("threads", [])
        data.setdefault("ideas", {})
        data.setdefault("chapters", {})
        data.setdefault("next", {})
        settings = dict(DEFAULT_SETTINGS)
        settings.update(data.get("settings") or {})
        data["settings"] = settings
        return data

    def save(self) -> None:
        self.meta["updated_at"] = _now()
        try:
            self.meta_path.write_text(
                json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            log.warning("novel: cannot write %s: %s", self.meta_path, exc)

    @property
    def settings(self) -> dict[str, Any]:
        return self.meta["settings"]

    # -- chapters ------------------------------------------------------
    def chapter_files(self) -> dict[int, Path]:
        out: dict[int, Path] = {}
        try:
            entries = list(self.root.glob("*.txt"))
        except OSError:
            return out
        for p in entries:
            m = CHAPTER_RE.match(p.stem)
            if m:
                out[int(m.group(1))] = p
        return out

    def chapter_path(self, number: int) -> Path:
        files = self.chapter_files()
        if number in files:
            return files[number]
        # follow the naming scheme of the existing chapters
        template = "chapter{}.txt"
        for p in files.values():
            m = re.match(r"(?i)^(chapter|chuong|chương)([ _-]?)(\d+)$", p.stem)
            if m:
                template = f"{m.group(1)}{m.group(2)}{{}}.txt"
                break
        return self.root / template.format(number)

    def latest_number(self) -> int:
        files = self.chapter_files()
        return max(files) if files else 0

    def next_number(self) -> int:
        """Next chapter number: derived from the files on disk (meta is only a hint)."""
        latest = self.latest_number()
        nxt = (self.meta.get("next") or {}).get("chapter")
        if isinstance(nxt, int) and nxt > latest:
            return nxt
        return latest + 1

    def chapter_meta(self, number: int) -> dict[str, Any]:
        return (self.meta.get("chapters") or {}).get(str(number)) or {}

    def set_chapter_meta(self, number: int, data: dict[str, Any]) -> None:
        chapters = self.meta.setdefault("chapters", {})
        entry = chapters.setdefault(str(number), {})
        entry.update(data)
        entry["updated_at"] = _now()

    def chapter_text(self, number: int) -> str:
        path = self.chapter_path(number)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stats(self, number: int) -> dict[str, Any]:
        text = self.chapter_text(number)
        return {
            "words": count_words(text),
            "chars": len(text),
            "file": self.chapter_path(number).name,
        }

    def target_words(self, number: int) -> int:
        """Target length: average of the previous chapters (fallback to settings)."""
        files = self.chapter_files()
        prev = [n for n in files if n < number]
        if prev:
            prev.sort(reverse=True)
            samples = []
            for n in prev[:5]:
                words = self.stats(n)["words"]
                if words > 200:
                    samples.append(words)
            if samples:
                return int(sum(samples) / len(samples))
        return int(self.settings.get("target_words", 1800))

    # -- context -------------------------------------------------------
    def roster_block(self) -> str:
        chars = self.meta.get("characters") or {}
        if not chars:
            return ""
        lines = ["Key characters (use these exact names):"]
        for name, info in chars.items():
            bits = [name]
            if isinstance(info, dict):
                for key in ("title", "role", "goals", "flaws", "relationships"):
                    val = info.get(key)
                    if val:
                        bits.append(f"{key}: {val}")
            lines.append("- " + " | ".join(str(b) for b in bits))
        return "\n".join(lines)

    def world_block(self) -> str:
        world = self.meta.get("world") or {}
        if not world:
            return ""
        lines = ["Setting:"]
        for key in ("place", "time", "atmosphere", "rules"):
            val = world.get(key)
            if val:
                lines.append(f"- {key}: {val}")
        return "\n".join(lines) if len(lines) > 1 else ""

    def threads_block(self) -> str:
        threads = [t for t in (self.meta.get("threads") or []) if str(t).strip()]
        if not threads:
            return ""
        return "Open plot threads (unresolved, weave at least one in):\n" + "\n".join(
            f"- {t}" for t in threads
        )

    def previous_tail(self, number: int, limit: int | None = None) -> str:
        prev = [n for n in self.chapter_files() if n < number]
        if not prev:
            return ""
        last = max(prev)
        limit = int(limit or self.settings.get("tail_chars", 1800))
        text = self.chapter_text(last)
        return text[-limit:]

    def story_language(self, number: int) -> str:
        """Language of the story, detected from the previous chapters."""
        prevs = [n for n in self.chapter_files() if n < number]
        if prevs:
            return detect_language(self.chapter_text(max(prevs)))
        if self.chapter_files():
            return detect_language(self.chapter_text(min(self.chapter_files())))
        return "Vietnamese"

    def context_block(self, number: int) -> str:
        """Everything the writer needs: roster, plan, tail of previous chapter."""
        parts: list[str] = []
        roster = self.roster_block()
        if roster:
            parts.append(roster)
        world = self.world_block()
        if world:
            parts.append(world)
        threads = self.threads_block()
        if threads:
            parts.append(threads)
        plan = self.chapter_meta(number).get("plan")
        if plan:
            parts.append("Chapter plan:\n" + plan_block(plan))
        tail = self.previous_tail(number)
        if tail:
            parts.append(
                "End of the previous chapter (continue from here, do not repeat it):\n---\n"
                + tail
                + "\n---"
            )
        return "\n\n".join(parts)


def detect_language(text: str) -> str:
    """Guess the writing language from a sample: Vietnamese, English or unknown."""
    sample = (text or "")[:4000]
    if not sample.strip():
        return "Vietnamese"
    letters = [c for c in sample if c.isalpha()]
    if not letters:
        return "Vietnamese"
    viet = sum(
        1 for c in letters if c in "ăâđêôơưĂÂĐÊÔƠƯạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
    )
    ratio = viet / len(letters)
    if ratio > 0.015:
        return "Vietnamese"
    ascii_ratio = sum(1 for c in letters if ord(c) < 128) / len(letters)
    if ascii_ratio > 0.95:
        return "English"
    return "Vietnamese"


MAX_SCENE_WORDS = 400
PLACEHOLDER_NAMES = {
    "name", "character", "characters", "n/a", "na", "none", "unknown",
    "main character", "protagonist", "unnamed", "tên", "nhân vật",
}


def normalize_plan(plan: dict[str, Any], total_words: int, number: int = 0) -> dict[str, Any]:
    """Make the plan executable: scenes of at most MAX_SCENE_WORDS words, enough of them.

    Small models often return one giant scene (or none). A scene must fit in a
    single tool call, so split/extend the script here instead of trusting it.
    """
    total_words = int(total_words or 0)
    scenes = [s for s in (plan.get("scenes") or []) if isinstance(s, dict)]
    scenes = [s for s in scenes if (s.get("goal") or s.get("beats") or s.get("title"))]

    if not scenes:
        beats = [str(b) for b in (plan.get("beats") or []) if str(b).strip()]
        if not beats:
            goal = plan.get("goal") or "Continue the story"
            beats = [f"{goal} (part {i + 1})" for i in range(max(3, total_words // MAX_SCENE_WORDS))]
        scenes = [
            {
                "n": i + 1,
                "title": f"Scene {i + 1}",
                "pov": plan.get("pov", ""),
                "goal": beat,
                "conflict": "",
                "turn": "",
                "beats": [beat],
            }
            for i, beat in enumerate(beats)
        ]

    out: list[dict[str, Any]] = []
    for i, scene in enumerate(scenes):
        s = dict(scene)
        try:
            words = int(s.get("words") or 0)
        except (TypeError, ValueError):
            words = 0
        if words <= 0 or words > MAX_SCENE_WORDS:
            words = MAX_SCENE_WORDS
        s["n"] = i + 1
        s["words"] = words
        out.append(s)

    # make sure the script covers the whole chapter
    covered = sum(s["words"] for s in out)
    if total_words and covered < total_words:
        missing = total_words - covered
        extra = max(1, (missing + MAX_SCENE_WORDS - 1) // MAX_SCENE_WORDS)
        for _ in range(extra):
            out.append(
                {
                    "n": len(out) + 1,
                    "title": f"Scene {len(out) + 1}",
                    "pov": plan.get("pov", ""),
                    "goal": "Continue the chapter: the next beat of the story",
                    "conflict": "",
                    "turn": "",
                    "beats": [],
                    "words": MAX_SCENE_WORDS,
                }
            )
    plan["scenes"] = out
    plan["total_words"] = total_words or covered
    # the model sometimes numbers the chapter wrong ("Chuong 11" for chapter 69)
    title = str(plan.get("title") or "")
    if title and number:
        fixed = re.sub(
            r"(?i)\b(chương|chuong|chapter|chapitre)\s*\d+",
            lambda m: f"{m.group(1)} {number}",
            title,
        )
        plan["title"] = fixed
    return plan


def plan_block(plan: dict[str, Any]) -> str:
    if not isinstance(plan, dict):
        return str(plan)
    order = ["title", "pov", "goal", "conflict", "location", "time", "beats"]
    lines = []
    for key in order:
        val = plan.get(key)
        if not val:
            continue
        if isinstance(val, list):
            val = "; ".join(str(v) for v in val)
        lines.append(f"{key}: {val}")
    extra = [k for k in plan if k not in order]
    for key in extra:
        val = plan[key]
        if isinstance(val, list):
            val = "; ".join(str(v) for v in val)
        lines.append(f"{key}: {val}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------
PLAN_PROMPT = """You are a story planner for a novel. Build a detailed scene-by-scene script
for the next chapter.

CONTEXT:
{context}

{instruction}

The chapter must be about {words} words.
Write the plan (titles, goals, beats) in {language}.
Return ONLY a JSON object:
{{
  "title": "chapter title",
  "pov": "point of view character name",
  "goal": "what the POV character wants in the whole chapter",
  "conflict": "what stands in the way",
  "location": "where the chapter happens",
  "time": "when the chapter happens",
  "total_words": {words},
  "scenes": [
    {{
      "n": 1,
      "title": "short scene title",
      "pov": "POV character of this scene",
      "goal": "what happens in this scene (one sentence)",
      "conflict": "the obstacle inside this scene",
      "turn": "how the scene ends, what changes",
      "beats": ["beat 1", "beat 2", "beat 3"],
      "words": 350
    }}
  ]
}}

Rules:
- 5 to 10 scenes (more only for a very long chapter), each scene 250-500 words.
- Every scene needs a clear goal, a conflict and a turn.
- The beats must be concrete enough that a writer can execute them without guessing.
- The last scene must land the chapter's turning point.
"""


async def plan_chapter(
    project: Project,
    number: int,
    instruction: str,
    model: str,
    target_words: int = 0,
) -> dict[str, Any]:
    ctx = project.context_block(number) or "(no previous chapters yet)"
    words = int(target_words or project.target_words(number))
    lang = project.story_language(number)
    prompt = PLAN_PROMPT.format(
        context=ctx,
        instruction=instruction or "Write a plan that continues the story.",
        words=words,
        language=lang,
    )
    plan_model = project.settings.get("plan_model") or model
    data = await _json_call(
        plan_model,
        [{"role": "user", "content": prompt}],
        int(project.settings.get("plan_max_tokens", 1536)),
    )
    if not data:
        data = {
            "title": f"Chapter {number}",
            "pov": (project.meta.get("pov") or [""])[0] if project.meta.get("pov") else "",
            "goal": instruction or "Continue the story",
            "conflict": "",
            "beats": [],
        }
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        # no scene list -> derive scenes from the beats
        beats = [str(b) for b in (data.get("beats") or []) if str(b).strip()]
        if not beats:
            beats = [data.get("goal") or "Continue the story"]
        per = max(200, int(words / max(1, len(beats))))
        data["scenes"] = [
            {
                "n": i + 1,
                "title": f"Scene {i + 1}",
                "pov": data.get("pov", ""),
                "goal": beat,
                "conflict": "",
                "turn": "",
                "beats": [beat],
                "words": per,
            }
            for i, beat in enumerate(beats)
        ]
    data.setdefault("total_words", words)
    data = normalize_plan(data, words, number)
    data["number"] = number
    project.set_chapter_meta(number, {"plan": data})
    project.save()
    log.info("novel: plan for chapter %s saved", number)
    return data


# --------------------------------------------------------------------------
# review
# --------------------------------------------------------------------------
REVIEW_PROMPT = """You are a strict fiction editor. Review the chapter below.

GENRE: {genre}
{pov_line}

CHAPTER:
{text}

Check: continuity with the plan, character consistency, pacing, dialogue, prose, show-don't-tell.
Return ONLY a JSON object:
{{
  "coherence_score": 0.0-1.0,
  "pacing_score": 0.0-1.0,
  "prose_score": 0.0-1.0,
  "character_score": 0.0-1.0,
  "dialogue_score": 0.0-1.0,
  "issues": ["specific problem, with a short quote"],
  "suggestions": ["how to fix it"],
  "strengths": ["what works"]
}}"""


def deterministic_review(text: str, project: Project, number: int) -> dict[str, Any]:
    """Rule based checks - fast, objective, no model needed."""
    issues: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    words = count_words(text)
    chars = len(text)
    sentences = [s.strip() for s in re.split(r"[.!?…]+", text) if s.strip()]
    metrics["words"] = words
    metrics["chars"] = chars
    metrics["sentences"] = len(sentences)
    metrics["avg_sentence_words"] = round(words / len(sentences), 1) if sentences else 0

    target = project.target_words(number)
    metrics["target_words"] = target
    if words < int(target * 0.8):
        issues.append(
            f"chapter too short: {words} words, target at least {int(target * 0.8)}"
        )
    elif words > int(target * 1.35):
        warnings.append(f"chapter longer than target: {words} words (target {target})")

    for pattern, label in ARTIFACT_PATTERNS:
        if re.search(pattern, text, re.M):
            issues.append(f"artefact: {label}")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 120]
    seen: set[str] = set()
    dupes = 0
    for p in paragraphs:
        key = " ".join(p.lower().split()[:18])
        if key in seen:
            dupes += 1
        seen.add(key)
    if dupes:
        issues.append(f"{dupes} repeated paragraph(s) inside the chapter")

    # repeated sentences - small models loop on the same phrase
    sentence_counts: dict[str, int] = {}
    for s in sentences:
        key = s.strip().lower()
        if len(key) >= 25:
            sentence_counts[key] = sentence_counts.get(key, 0) + 1
    repeated_sentences = [k for k, c in sentence_counts.items() if c >= 3]
    if repeated_sentences:
        issues.append(
            f"{len(repeated_sentences)} sentence(s) repeated 3+ times (model loop) - "
            f'e.g. "{repeated_sentences[0][:60]}..."'
        )

    # duplicated content vs previous chapters
    prev_text = ""
    prevs = [n for n in project.chapter_files() if n < number]
    if prevs:
        prev_text = project.chapter_text(max(prevs)).lower()
    if prev_text:
        for p in paragraphs:
            head = " ".join(p.lower().split()[:18])
            if len(head) > 60 and head in prev_text:
                issues.append("paragraph repeated from a previous chapter")
                break

    # POV presence (match the full name or any part of it: "Amos Rosselt" -> "Amos")
    plan = project.chapter_meta(number).get("plan") or {}
    pov = (plan.get("pov") or "").strip()
    metrics["pov"] = pov
    if pov:
        parts = [p for p in re.split(r"\s+", pov) if len(p) >= 3]
        low = text.lower()
        if not any(part.lower() in low for part in parts):
            warnings.append(f"POV character '{pov}' does not appear in the chapter")

    # scene script fully executed?
    planned = len(plan.get("scenes") or [])
    done = project.chapter_meta(number).get("scenes_done")
    if planned and isinstance(done, int) and done < planned:
        issues.append(f"only {done} of {planned} planned scenes were written")

    quotes = text.count('"')
    dialogue_ratio = round(quotes / 2 / len(sentences), 3) if sentences else 0.0
    metrics["dialogue_ratio"] = dialogue_ratio

    return {
        "issues": issues,
        "warnings": warnings,
        "metrics": metrics,
        "target_words": target,
    }


def proxy_scores(checks: dict[str, Any]) -> dict[str, float]:
    """Scores derived from the deterministic metrics (used when LLM review fails)."""
    m = checks.get("metrics") or {}
    avg = m.get("avg_sentence_words") or 0
    if avg and avg < 11:
        pacing = 0.80
    elif avg and avg > 22:
        pacing = 0.60
    else:
        pacing = 0.75
    ratio = m.get("dialogue_ratio") or 0.0
    if ratio >= 0.20:
        dialogue = 0.80
    elif ratio >= 0.10:
        dialogue = 0.70
    else:
        dialogue = 0.50
    issues = len(checks.get("issues") or [])
    prose = max(0.2, 1.0 - 0.15 * issues)
    character = 0.80 if (not m.get("pov") or m.get("pov")) else 0.5
    return {
        "coherence": 0.70,
        "pacing": pacing,
        "prose": prose,
        "character": character,
        "dialogue": dialogue,
    }


async def review_chapter(
    project: Project, number: int, model: str, use_llm_review: bool = True
) -> dict[str, Any]:
    """Full review: deterministic checks + LLM editor scores."""
    text = project.chapter_text(number)
    checks = deterministic_review(text, project, number)

    scores: dict[str, float] = {}
    llm_report: dict[str, Any] = {}
    if use_llm_review and text.strip():
        review_model = project.settings.get("review_model") or model
        plan = project.chapter_meta(number).get("plan") or {}
        pov = (plan.get("pov") or "").strip()
        prompt = REVIEW_PROMPT.format(
            genre=project.meta.get("genre") or "general fiction",
            pov_line=f"POV character: {pov}\n" if pov else "",
            text=text[:12000],
        )
        llm_report = await _json_call(
            review_model,
            [{"role": "user", "content": prompt}],
            int(project.settings.get("review_max_tokens", 2048)),
        )
        for key in WEIGHTS:
            val = llm_report.get(f"{key}_score")
            if isinstance(val, (int, float)):
                scores[key] = max(0.0, min(1.0, float(val)))

    if len(scores) < len(WEIGHTS):
        proxy = proxy_scores(checks)
        for key, val in proxy.items():
            scores.setdefault(key, val)

    overall = round(sum(scores.get(k, 0.0) * w for k, w in WEIGHTS.items()), 3)
    llm_issues = [str(i) for i in (llm_report.get("issues") or []) if str(i).strip()]
    report = {
        "number": number,
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "overall": overall,
        "issues": checks["issues"] + llm_issues,
        "warnings": checks["warnings"],
        "suggestions": [str(s) for s in (llm_report.get("suggestions") or [])],
        "strengths": [str(s) for s in (llm_report.get("strengths") or [])],
        "metrics": checks["metrics"],
        "reviewed_at": _now(),
        "llm_review": bool(llm_report),
    }
    return report


def trend(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not reports:
        return {"count": 0}
    vals = [r.get("overall", 0.0) for r in reports]
    avg = round(sum(vals) / len(vals), 3)
    first, last = vals[0], vals[-1]
    return {
        "count": len(vals),
        "avg": avg,
        "last": round(last, 3),
        "first": round(first, 3),
        "delta": round(last - first, 3),
        "direction": "up" if last > first + 0.02 else ("down" if last < first - 0.02 else "flat"),
    }


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------
WRITE_SYSTEM = """You are a professional novelist writing one chapter of a novel.

{context}

Rules:
- Write prose only: no commentary, no file paths, no "here is the chapter".
- Length target: at least {min_chars} characters (about {words} words). Keep writing until you reach it.
- IMPORTANT: one tool call may only carry about 2000-2500 characters of prose.
  Write the first part with write_file, then keep going with append_file on the same path.
  Never try to put the whole chapter into one call.
- Use the exact character names from the context and keep the POV consistent.
- Match the language, tone and style of the previous chapter.
"""

SCENE_PROMPT = """You are a professional novelist. Write ONE scene of a chapter.

CHAPTER SCRIPT:
{plan}

CONTEXT (characters, what happened before):
{context}

SCENE TO WRITE:
{scene}

Rules:
- Write the scene prose in {language} and nothing else. No commentary, no scene title,
  no notes, no "here is the scene".
- About {words} words.
- Do not repeat anything that is already written.
- End the scene on the planned turn.
"""

CONTINUE_PROMPT = """You are a professional novelist. Continue the scene exactly where it stopped.

Rules:
- Write only the continuation prose in {language}, nothing else.
- Never repeat a sentence that was already written.
- Continue until the planned turn of the scene is reached.
"""


SCENE_SYSTEM = """You are a professional novelist writing ONE SCENE of a chapter.

CHAPTER SCRIPT:
{plan}

CONTEXT:
{context}

SCENE TO WRITE:
{scene}

Rules:
- Write ONLY this scene, nothing else.
- Write the scene in {language}.
- Target length: about {words} words.
- One tool call may carry at most ~{chunk} characters of prose. If the scene is longer,
  write the first part with {tool} and continue with append_file on the same path.
- No commentary, no file paths, no scene labels, no ellipsis placeholders.
- Use the exact names from the context, keep the POV and match the tone of the previous text.
- End the scene on the planned turn.
"""


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _append_text(path: Path, text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        return False
    return True


def _trim_partial(text: str) -> str:
    """Drop a dangling half sentence (the generation may have been cut)."""
    text = (text or "").rstrip()
    if not text:
        return text
    if text[-1] in ".!?\"\u201d\u2019)":
        return text
    for i in range(len(text) - 1, max(0, len(text) - 400), -1):
        if text[i] in ".!?\"\u201d\u2019)":
            return text[: i + 1]
    return text


async def write_scene_prose(
    project: Project,
    number: int,
    model: str,
    scene: dict[str, Any],
    plan: dict[str, Any],
    context: str,
    call_tokens: int,
    on_event=None,
    stream: bool = True,
) -> dict[str, Any]:
    """Write one scene as plain prose; the code appends it to the chapter file.

    The model never puts prose into a tool call, so a long generation can never
    break the JSON of a tool call (the usual llama.cpp 500 failure).
    """
    path = project.chapter_path(number)
    words = int(scene.get("words") or 350)
    lang = project.story_language(number)
    budget = int(max(512, min(int(call_tokens), words * 3)))
    beats = scene.get("beats") or []
    scene_desc = "\n".join(
        [
            f"scene {scene.get('n', '?')}: {scene.get('title', '')}",
            f"goal: {scene.get('goal', '')}",
            f"conflict: {scene.get('conflict', '')}",
            f"turn: {scene.get('turn', '')}",
            "beats: " + "; ".join(str(b) for b in beats),
        ]
    )
    written = 0
    rounds = 0
    for rnd in range(4):
        rounds = rnd + 1
        if rnd == 0:
            system = SCENE_PROMPT.format(
                plan=plan_block(plan),
                context=context or "(no previous chapters yet)",
                scene=scene_desc,
                words=words,
                language=lang,
            )
            user = "Write the scene now."
        else:
            system = CONTINUE_PROMPT.format(language=lang)
            user = "Continue the scene from exactly where it stopped."
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            resp = await llm.chat(model, messages, tools=None, max_tokens=budget)
        except Exception as exc:  # noqa: BLE001 - a dead step must not kill the run
            log.warning("novel: scene generation failed: %s", exc)
            break
        choice = (resp.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content") or "").strip()
        if not text:
            break
        text = _trim_partial(text)
        if not text:
            break
        _append_text(path, text)
        written += count_words(text)
        if on_event is not None:
            await on_event({"type": "token", "step": 0, "delta": text + "\n\n"})
        if written >= words:
            break
    return {"words": written, "rounds": rounds}


LORE_PROMPT = """Extract the story bible of this novel excerpt: characters, setting, open plot threads.

EXCERPT:
{text}

Return ONLY a JSON object:
{{
  "characters": {{
    "Name": {{"title": "...", "role": "...", "goals": "...", "flaws": "...", "relationships": "..."}}
  }},
  "world": {{"place": "...", "time": "...", "atmosphere": "...", "rules": "..."}},
  "threads": ["unresolved plot thread 1", "unresolved plot thread 2"]
}}"""


async def extract_lore(project: Project, model: str, chapters: int = 5) -> dict[str, Any]:
    """Refresh the story bible (characters, world, open threads) from the most recent chapters."""
    numbers = sorted(project.chapter_files())[-chapters:]
    if not numbers:
        return {}
    parts = []
    for n in numbers:
        parts.append(f"--- chapter {n} ---\n{project.chapter_text(n)}")
    text = "\n\n".join(parts)[:14000]
    data = await _json_call(model, [{"role": "user", "content": LORE_PROMPT.format(text=text)}], 2048)
    chars = data.get("characters")
    if not isinstance(chars, dict):
        # the model sometimes returns the roster at the top level
        chars = {k: v for k, v in data.items() if isinstance(v, dict)}
    clean: dict[str, Any] = {}
    for name, info in (chars or {}).items():
        label = str(name).strip()
        if not label or label.lower() in PLACEHOLDER_NAMES:
            continue
        if not isinstance(info, dict):
            info = {"role": str(info)}
        clean[label] = {k: v for k, v in info.items() if isinstance(v, (str, int, float, list))}

    world = data.get("world")
    if isinstance(world, dict):
        project.meta["world"] = {k: v for k, v in world.items() if isinstance(v, (str, int, float))}

    threads = [str(t).strip() for t in (data.get("threads") or []) if str(t).strip()]
    if threads:
        project.meta["threads"] = threads

    if len(clean) >= 1:
        project.meta["characters"] = clean
        project.save()
        return clean
    if world or threads:
        project.save()
    log.warning("novel: roster extraction returned nothing usable")
    return {}


IDEAS_PROMPT = """You are a story planner brainstorming what could happen next in this novel.

{context}

Propose 3 to 5 distinct directions for the NEXT chapter(s): twists, reveals, escalations,
or quiet character beats. Each must use the existing characters/threads/world above,
not invent an unrelated plot.

Return ONLY a JSON object:
{{
  "ideas": [
    {{"title": "short hook", "pitch": "1-2 sentences: what happens and why it matters", "uses_thread": "which open thread this resolves or escalates (or '')"}}
  ]
}}"""


async def suggest_ideas(project: Project, model: str, n: int = 4) -> list[dict[str, Any]]:
    """Adaptive 'what happens next' brainstorm, grounded in the existing roster/world/threads."""
    number = project.next_number()
    context = project.context_block(number) or "(no story context yet - this is the opening)"
    prompt = IDEAS_PROMPT.format(context=context)
    data = await _json_call(model, [{"role": "user", "content": prompt}], 1024)
    ideas = [i for i in (data.get("ideas") or []) if isinstance(i, dict) and i.get("pitch")]
    clean = [
        {
            "title": str(i.get("title") or "").strip() or "Untitled",
            "pitch": str(i.get("pitch") or "").strip(),
            "uses_thread": str(i.get("uses_thread") or "").strip(),
        }
        for i in ideas
    ][:n]
    if clean:
        project.meta["ideas"] = {"for_chapter": number, "generated_at": _now(), "items": clean}
        project.save()
    return clean


async def write_chapter(
    project: Project,
    number: int,
    model: str,
    instruction: str,
    context: str,
    target_words: int,
    on_event=None,
    stream: bool = True,
    max_steps: int = 24,
) -> dict[str, Any]:
    """Write (or rewrite) one chapter file."""
    from . import agent

    path = project.chapter_path(number)
    min_chars = int(target_words * 6)
    system = WRITE_SYSTEM.format(
        context=context or "(no previous chapters yet)",
        min_chars=min_chars,
        words=target_words,
        path=path,
    )
    user = instruction.strip() or f"Write chapter {number} of the novel."
    user = f"{user}\n\nWrite the chapter to this file: {path}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    result = await agent.run_agent(
        model,
        messages,
        on_event=on_event,
        max_steps=max_steps,
        stream=stream,
        creative=True,
        continue_path=str(path),
        continue_min_chars=min_chars,
    )
    config.clean_story_file(path)
    return result


async def run_write(
    project: Project,
    instruction: str = "",
    model: str = "",
    on_event=None,
    stream: bool = True,
    do_plan: bool = True,
    use_llm_review: bool = True,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """Full pipeline: plan -> write -> review -> (retry) -> persist."""
    number = project.next_number()
    target_words = project.target_words(number)
    log.info("novel: run_write chapter %s (project %s)", number, project.root)

    # no roster yet -> build it from the existing chapters so names stay consistent
    if not project.meta.get("characters") and len(project.chapter_files()) >= 2:
        log.info("novel: extracting character roster")
        await extract_lore(project, model)

    plan = project.chapter_meta(number).get("plan")
    if do_plan and not plan:
        plan = await plan_chapter(project, number, instruction, model, target_words)

    attempts = max_attempts if max_attempts is not None else int(project.settings.get("max_attempts", 2))
    min_score = float(project.settings.get("min_score", 0.7))
    profile = config.model_profile(model)
    call_tokens = int(profile["call_tokens"])
    max_calls = int(profile["max_calls"])
    report: dict[str, Any] = {}
    draft_backup = ""
    attempt = 0
    calls_used = 0

    while True:
        attempt += 1
        context = project.context_block(number)
        if draft_backup:
            context += (
                "\n\nCurrent draft (rewrite it, fix the problems, keep the good parts):\n---\n"
                + draft_backup[:8000]
                + "\n---"
            )
        path = project.chapter_path(number)
        scenes = list((project.chapter_meta(number).get("plan") or {}).get("scenes") or [])
        if scenes:
            # scene by scene: one tool call per scene, sized by the script
            for idx, scene in enumerate(scenes):
                if calls_used >= max_calls:
                    break
                before = _file_size(path)
                scene_context = context
                if before:
                    try:
                        tail = path.read_text(encoding="utf-8", errors="replace")[-1500:]
                    except OSError:
                        tail = ""
                    if tail.strip():
                        scene_context += (
                            "\n\nTEXT ALREADY WRITTEN (continue seamlessly, do not repeat):\n---\n"
                            + tail
                            + "\n---"
                        )
                res = await write_scene_prose(
                    project,
                    number,
                    model,
                    scene,
                    project.chapter_meta(number).get("plan") or {},
                    scene_context,
                    call_tokens,
                    on_event=on_event,
                    stream=stream,
                )
                calls_used += 1
                if _file_size(path) <= before and res.get("words"):
                    log.warning("novel: scene %s produced no text", idx + 1)
                project.set_chapter_meta(number, {"scenes_done": idx + 1, "calls_used": calls_used})
                project.save()
        else:
            await write_chapter(
                project,
                number,
                model,
                instruction,
                context,
                target_words,
                on_event=on_event,
                stream=stream,
            )
            calls_used += 1
        report = await review_chapter(project, number, model, use_llm_review=use_llm_review)
        hard = [
            i
            for i in report.get("issues", [])
            if "artefact" in i or "repeated" in i or "too short" in i or "path" in i
        ]
        if (report.get("overall", 0.0) >= min_score and not hard) or attempt >= attempts:
            break

        draft_backup = project.chapter_text(number)
        lines = [f"- {i}" for i in report.get("issues", [])]
        lines += [f"- {s}" for s in report.get("suggestions", [])[:5]]
        if any("repeated" in i for i in report.get("issues", [])):
            lines.append(
                "- do not reuse the same sentence or the same sentence opening more than once; "
                "vary how every paragraph starts"
            )
        instruction = (
            "The editor rejected this chapter. Fix these problems:\n"
            + "\n".join(lines)
            + "\n\nRewrite the WHOLE chapter (same story, same POV, same length) and save it again."
        )
        try:
            project.chapter_path(number).unlink(missing_ok=True)
        except OSError:
            pass

    if not project.chapter_path(number).exists() and draft_backup:
        config.clean_story_file(project.chapter_path(number))
        try:
            project.chapter_path(number).write_text(draft_backup, encoding="utf-8")
        except OSError:
            pass

    stats = project.stats(number)
    plan = project.chapter_meta(number).get("plan") or {}
    project.set_chapter_meta(
        number,
        {
            "file": stats["file"],
            "title": plan.get("title", ""),
            "pov": plan.get("pov", ""),
            "words": stats["words"],
            "chars": stats["chars"],
            "quality": report,
            "attempts": attempt,
            "status": "done",
        },
    )
    project.meta["next"] = {"chapter": number + 1}
    project.save()

    return {
        "number": number,
        "file": str(project.chapter_path(number)),
        "plan": plan,
        "report": report,
        "attempts": attempt,
        "calls": calls_used,
        "words": stats["words"],
        "target_words": target_words,
    }


# --------------------------------------------------------------------------
# status / manuscript
# --------------------------------------------------------------------------
def status(project: Project) -> dict[str, Any]:
    files = project.chapter_files()
    numbers = sorted(files)
    words = 0
    chars = 0
    for n in numbers:
        s = project.stats(n)
        words += s["words"]
        chars += s["chars"]
    reports = []
    for n in numbers:
        q = project.chapter_meta(n).get("quality")
        if q:
            reports.append(q)
    return {
        "title": project.meta.get("title", ""),
        "genre": project.meta.get("genre", ""),
        "root": str(project.root),
        "chapters": len(numbers),
        "words": words,
        "chars": chars,
        "avg_words": int(words / len(numbers)) if numbers else 0,
        "last_chapter": numbers[-1] if numbers else 0,
        "next_chapter": project.next_number(),
        "target_words": project.target_words(project.next_number()),
        "characters": len(project.meta.get("characters") or {}),
        "quality": trend(reports),
        "settings": project.settings,
    }


def build_manuscript(project: Project, out_name: str | None = None) -> Path:
    """Combine all chapters into one manuscript file (like NovelWriter's combine)."""
    numbers = sorted(project.chapter_files())
    title = project.meta.get("title") or project.root.name
    out = project.root / (out_name or f"{title}.md")
    parts = [f"# {title}\n"]
    for n in numbers:
        plan = project.chapter_meta(n).get("plan") or {}
        chapter_title = plan.get("title") or ""
        header = f"## Chapter {n}" + (f" - {chapter_title}" if chapter_title else "")
        parts.append(header + "\n\n" + project.chapter_text(n).strip() + "\n")
    out.write_text("\n".join(parts), encoding="utf-8")
    return out
