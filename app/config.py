from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / ".config"


def _load_config_file(path: Path) -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not path.exists():
        return cfg
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return cfg
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # strip optional surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        cfg[key] = value
    return cfg


_FILE_CFG = _load_config_file(CONFIG_FILE)


def _get(key: str, default: str = "") -> str:
    # Environment variables override the .config file.
    if key in os.environ:
        return os.environ[key]
    if key in _FILE_CFG:
        return _FILE_CFG[key]
    return default


def _path_setting(key: str, default: Path) -> Path:
    raw = _get(key, "").strip()
    if not raw:
        return default
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = BASE_DIR / p
    return p


def _int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_get(key, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _bool(key: str, default: bool) -> bool:
    raw = _get(key, "1" if default else "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ---- paths --------------------------------------------------------------
DATA_DIR = _path_setting("LA_DATA_DIR", BASE_DIR / "data")
MODELS_DIR = _path_setting("LA_MODELS_DIR", BASE_DIR / "models")
DB_PATH = DATA_DIR / "tasks.db"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = DATA_DIR / "uploads"

# ---- server (web + mcp) -------------------------------------------------
HOST = _get("LA_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = _int("LA_PORT", 8765)

# ---- llama.cpp runtime --------------------------------------------------
_DEFAULT_LLAMA_DIR = BASE_DIR / "runtime" / "llama.cpp"
LLAMA_DIR = _path_setting("LA_LLAMA_DIR", _DEFAULT_LLAMA_DIR)
# Windows build: llama-server.exe | Linux/macOS build: llama-server
_LLAMA_EXE_NAME = "llama-server.exe" if os.name == "nt" else "llama-server"
LLAMA_SERVER = LLAMA_DIR / _LLAMA_EXE_NAME
LLAMA_HOST = _get("LA_LLAMA_HOST", "127.0.0.1").strip() or "127.0.0.1"
LLAMA_PORT_BASE = _int("LA_LLAMA_PORT_BASE", 8080)
LLAMA_DEVICE = _get("LA_LLAMA_DEVICE", "").strip()
LLAMA_CTX = _int("LA_LLAMA_CTX", 4096)
LLAMA_FALLBACK_CTX = _int("LA_LLAMA_FALLBACK_CTX", 8192)
LLAMA_NGL = _int("LA_LLAMA_NGL", 999)
# KV cache quant (q8_0 saves a lot of VRAM; empty = server default)
LLAMA_CACHE_TYPE = _get("LA_LLAMA_CACHE_TYPE", "q8_0").strip()
LLAMA_LOAD_TIMEOUT = _float("LA_LLAMA_LOAD_TIMEOUT", 180)
SWAP_UNLOAD = _bool("LA_SWAP_UNLOAD", True)

# ---- agent --------------------------------------------------------------
DEFAULT_MODEL = _get("LA_DEFAULT_MODEL", "qwen2.5-coder-3b").strip() or "qwen2.5-coder-3b"
DEFAULT_VISION_MODEL = _get("LA_DEFAULT_VISION_MODEL", "qwen2.5-vl-3b").strip() or "qwen2.5-vl-3b"
DEFAULT_AGENT = _get("LA_DEFAULT_AGENT", "local-agent").strip() or "local-agent"
MAX_CONCURRENCY = max(1, _int("LA_MAX_CONCURRENCY", 1))
REQUEST_TIMEOUT = _float("LA_REQUEST_TIMEOUT", 900)
TEMPERATURE = _float("LA_TEMPERATURE", 0.2)
AGENT_MAX_STEPS = max(1, _int("LA_AGENT_MAX_STEPS", 12))
AGENT_SHELL_TIMEOUT = _float("LA_AGENT_SHELL_TIMEOUT", 60)
AGENT_MAX_OUTPUT = _int("LA_AGENT_MAX_OUTPUT", 4000)
AGENT_ALLOW_ABS = _bool("LA_AGENT_ALLOW_ABS", False)

# ---- novel project ------------------------------------------------------
NOVEL_TARGET_WORDS = _int("LA_NOVEL_TARGET_WORDS", 1800)
NOVEL_MIN_SCORE = _float("LA_NOVEL_MIN_SCORE", 0.70)
NOVEL_MAX_ATTEMPTS = _int("LA_NOVEL_MAX_ATTEMPTS", 2)
NOVEL_TAIL_CHARS = _int("LA_NOVEL_TAIL_CHARS", 1800)
NOVEL_PLAN_MODEL = _get("LA_NOVEL_PLAN_MODEL", "").strip()
NOVEL_REVIEW_MODEL = _get("LA_NOVEL_REVIEW_MODEL", "").strip()


# ---- per model tool-call budget -----------------------------------------
# Tool call budget: each call may use `call_tokens` output tokens, and one
# chapter may use at most `max_calls` calls.
DEFAULT_CALL_TOKENS = _int("LA_CALL_TOKENS", 8192)
DEFAULT_MAX_CALLS = _int("LA_MAX_CALLS", 12)
_MODEL_PROFILES_RAW = _get("LA_MODEL_PROFILES", "")


def model_profile(model: str) -> dict[str, int]:
    """Tool-call budget for a model alias: {"call_tokens": int, "max_calls": int}.

    Configure with LA_MODEL_PROFILES, e.g.
        LA_MODEL_PROFILES=qwen2.5-coder-3b:8192:12;gemma-4-e4b:16384:24;default:8192:12
    """
    fallback = {"call_tokens": DEFAULT_CALL_TOKENS, "max_calls": DEFAULT_MAX_CALLS}
    default = fallback
    for part in _MODEL_PROFILES_RAW.replace(",", ";").split(";"):
        bits = [b.strip() for b in part.split(":")]
        if len(bits) < 2 or not bits[0]:
            continue
        alias = bits[0]
        tokens = bits[1] if len(bits) > 1 else ""
        calls = bits[2] if len(bits) > 2 else ""
        entry = {
            "call_tokens": int(tokens) if tokens.isdigit() else DEFAULT_CALL_TOKENS,
            "max_calls": int(calls) if calls.isdigit() else DEFAULT_MAX_CALLS,
        }
        if alias == "default":
            default = entry
        if alias == (model or "").strip():
            return entry
    return default


# ---- global / workspace -------------------------------------------------
# Tên lệnh gọi global (vd: ngu). Dùng với `la.bat install` để tạo lệnh và thêm vào PATH.
COMMAND_NAME = _get("LA_COMMAND_NAME", "ngu").strip() or "ngu"
# Hỏi quyền khi tool thao tác ngoài thư mục làm việc (1=hỏi, 0=từ chối trừ khi /auto)
ASK_PERMISSION = _bool("LA_ASK_PERMISSION", True)
# Khi gọi qua lệnh global, LA_WORKDIR = thư mục người dùng đang đứng. Mặc định = project.
WORKSPACE_DIR = _path_setting("LA_WORKDIR", BASE_DIR)

SYSTEM_PROMPT = _get(
    "LA_SYSTEM_PROMPT",
    "You are a local coding agent executing one assigned task. "
    "Be concrete and terse. Use the available tools to inspect files and run commands. "
    "Output only the final result: code, patch, or short report. "
    "If the task cannot be done, state exactly what is missing.",
)

AGENT_SYSTEM_PROMPT = (
    "You are a local coding agent running on llama.cpp. "
    "Always reply in the same language as the user (use Vietnamese if the user writes Vietnamese).\n"
    "Tools (exact names only): read_file, write_file, append_file, edit_file, list_dir, list_tree, glob_files, search_files, mkdir, move_path, remove_path, run_shell.\n"
    "Use tools to inspect and work on project files in the workspace. "
    "Only exception: if a file/image is attached inline in the chat message, do not open it with a tool.\n"
    "Tool call protocol (text mode): reply with EXACTLY ONE fenced JSON block and nothing else:\n"
    "```json\n"
    '{"name":"list_tree","arguments":{"path":".","max_depth":2}}\n'
    "```\n"
    "Then wait for the tool result.\n"
    "Rules:\n"
    "- Act, don't plan. Make one real tool call per step (never a list of planned calls).\n"
    "- Understand the user's request: if asked to analyze/find/explain project code or files, start by exploring (list_tree/list_dir/glob_files/read_file/search_files). If asked a simple question, chat, translation, or creative writing (stories, etc.), answer directly in plain text without tools.\n"
    "- Paths relative to workspace unless absolute. For large codebases prefer list_tree/glob_files/search_files before deep reads. List each requested directory separately (e.g. list_dir engines then list_dir infrastructure), never join them into one path.\n"
    "- If a path is missing, do not guess; inspect actual structure. Use small read ranges (offset/limit) when useful.\n"
    "- Never repeat an identical tool call. If blocked or info is sufficient, stop calling tools and give the final answer in plain text in the user's language.\n"
    "- If the user asks you to write or create code or text, produce it directly in the final answer. "
    "Do not explore the workspace first unless the task is about existing files."
)

# allow overriding the whole prompt via env/config
AGENT_SYSTEM_PROMPT = _get("LA_AGENT_SYSTEM_PROMPT", AGENT_SYSTEM_PROMPT)

# ---- creative writing mode ------------------------------------------------
CREATIVE_SYSTEM_PROMPT = _get(
    "LA_CREATIVE_SYSTEM_PROMPT",
    "You are a creative fiction writer. Write vivid prose: scenes, sensory detail, natural dialogue, "
    "emotional arc. Follow the requested genre, tone, length and language.\n"
    "File workflow (only when the user points at a folder or a file): "
    "1) list_dir the folder, 2) read_file the latest chapter to match style, names and plot, "
    "3) write_file the new text as the next numbered file in the same folder, "
    "4) if the chapter is still too short, continue with append_file on the same path, "
    "5) answer with one short line naming the file you saved. "
    "Never print the full chapter in the answer when a file is involved - save it with write_file.\n"
    "If no file is involved, write the story text directly as the answer. "
    "Answer in the same language as the user. No meta commentary.",
)

CREATIVE_FINAL_MAX_TOKENS = _int("LA_CREATIVE_FINAL_MAX_TOKENS", 16384)
FINAL_MAX_TOKENS = _int("LA_FINAL_MAX_TOKENS", 4096)
# Minimum chapter length (chars) when there is no previous chapter to copy.
CREATIVE_DEFAULT_MIN_CHARS = _int("LA_CREATIVE_MIN_CHARS", 4000)

# Always advertise tools to the model (default: only when the request needs them).
TOOLS_ALWAYS = _bool("LA_TOOLS_ALWAYS", False)

# Requests that clearly need file/shell tools.
ACTION_KEYWORDS = (
    # english
    "write", "create", "make", "fix", "bug", "refactor", "implement", "update",
    "build", "compile", "test", "debug", "install", "delete", "remove", "rename",
    "code", "script", "function", "api", "file", "folder", "directory", "repo",
    "project", "optimize", "format", "migrate", "generate", "edit", "modify",
    "deploy", "config", "package", "import", "read", "open", "run",
    # vietnamese (unaccented)
    "viet", "tao", "sua", "chay", "tim", "xem", "doc", "cap nhat", "them",
    "xoa", "doi", "ghi", "lap trinh", "chuong trinh", "ham", "sua loi", "loi code",
    "cai dat", "chuyen", "phan tich", "giup",
)

# "save to file" hints -> creative mode may use write_file
SAVE_KEYWORDS = ("luu", "save", "ghi ra", "export", "file")

_FILE_HINT_RE = re.compile(r"[\w./\\-]+\.[A-Za-z]{2,5}\b")
# absolute windows path, e.g. C:\Users\me\Desktop\.commic\HuyetThu
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]+")
# chapter numbering: chapter69.txt / chapter_69 / Chuong 69
_CHAPTER_RE = re.compile(r"(?i)chapter[ _-]?(\d+)")
_CHAPTER_FILE_RE = re.compile(r"(?i)^(chapter[ _-]?)(\d+)$")


def _norm(s: str) -> str:
    n = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in n if not unicodedata.combining(c))


def _kw_regex(words: tuple[str, ...]) -> re.Pattern[str]:
    pats = []
    for w in words:
        parts = [re.escape(p) for p in _norm(w).split()]
        pats.append(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)")
    return re.compile("|".join(pats))


# "chuong" = chapter, but "chuong trinh" = program -> exclude the latter.
_CREATIVE_RE = re.compile(
    "|".join(
        [
            r"viet\s+truyen",
            r"(?<!\w)truyen(?!\w)",
            r"(?<!\w)cau\s+truyen(?!\w)",
            r"(?<!\w)sang\s+tac(?!\w)",
            r"(?<!\w)ke\s+chuyen(?!\w)",
            r"(?<!\w)bedtime(?!\w)",
            r"(?<!\w)short[\s-]?story(?!\w)",
            r"(?<!\w)fiction(?!\w)",
            r"(?<!\w)novel(?!\w)",
            r"(?<!\w)chapter(?!\w)",
            r"(?<!\w)chuong(?!\w)(?!\s+trinh)",
            r"(?<!\w)prose(?!\w)",
        ]
    )
)
_ACTION_RE = _kw_regex(ACTION_KEYWORDS)
_SAVE_RE = _kw_regex(SAVE_KEYWORDS)


def is_creative(prompt: str) -> bool:
    return bool(_CREATIVE_RE.search(_norm(prompt)))


def system_prompt_for(prompt: str) -> str:
    return CREATIVE_SYSTEM_PROMPT if is_creative(prompt) else AGENT_SYSTEM_PROMPT


def needs_tools(prompt: str, creative: bool = False) -> bool:
    """Should the model get file/shell tools for this request?

    Small local models love calling tools even for chit-chat, which burns steps and
    loops. Only advertise tools when the request actually implies working on files.
    """
    if TOOLS_ALWAYS:
        return True
    raw = prompt or ""
    p = _norm(raw)
    if creative:
        # writers answer directly; tools only when a path/file is involved
        return (
            bool(_FILE_HINT_RE.search(raw))
            or bool(_PATH_RE.search(raw))
            or bool(_SAVE_RE.search(p))
        )
    if _ACTION_RE.search(p):
        return True
    return bool(_FILE_HINT_RE.search(raw)) or bool(_PATH_RE.search(raw))


def creative_target(prompt: str) -> Path | None:
    """Target file for "continue chapter N of <folder>" requests (None otherwise)."""
    raw = prompt or ""
    m = _PATH_RE.search(raw)
    if not m:
        return None
    folder = Path(m.group(0).strip().rstrip("\\/"))
    if not folder.is_dir():
        return None
    ch = _CHAPTER_RE.search(raw)
    if not ch:
        return None
    want = int(ch.group(1))

    numbered: list[tuple[int, Path]] = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    for p in entries:
        if not p.is_file():
            continue
        mm = _CHAPTER_FILE_RE.match(p.stem)
        if mm:
            numbered.append((int(mm.group(2)), p))
    numbered.sort()

    exists = {num for num, _ in numbered}
    nxt = want + 1 if want in exists else want
    template = "chapter"
    if numbered:
        mm = _CHAPTER_FILE_RE.match(numbered[-1][1].stem)
        if mm:
            template = mm.group(1)
    return folder / f"{template}{nxt}.txt"


def creative_target_info(prompt: str) -> tuple[Path | None, int]:
    """(target file, minimum chars) for a "continue chapter N" request."""
    target = creative_target(prompt)
    if target is None:
        return None, 0
    folder = target.parent
    try:
        entries = list(folder.iterdir())
    except OSError:
        entries = []
    numbered: list[tuple[int, Path]] = []
    for p in entries:
        if p.is_file():
            mm = _CHAPTER_FILE_RE.match(p.stem)
            if mm:
                numbered.append((int(mm.group(2)), p))
    numbered.sort()

    tgt_num = 0
    mm = _CHAPTER_FILE_RE.match(target.stem)
    if mm:
        tgt_num = int(mm.group(2))

    prev_len = 0
    for num, p in numbered:
        if tgt_num and num >= tgt_num:
            continue
        try:
            prev_len = max(prev_len, len(p.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            pass
    return target, (prev_len or CREATIVE_DEFAULT_MIN_CHARS)


def creative_file_hint(prompt: str) -> str:
    """Instruction line telling the model exactly which file to write and how long."""
    target, min_chars = creative_target_info(prompt)
    if not target:
        return ""
    words = max(200, int(min_chars / 6))
    return (
        f"Target file for the new chapter: {target}\n"
        f"Target length: at least {min_chars} characters (about {words} words) - "
        "the same length as the previous chapter.\n"
        "Put ONLY the chapter text into that file: no path line, no commentary, no ellipses.\n"
        "If one call is not enough, write the first part with write_file and continue "
        "with append_file on the same path until the chapter is long enough."
    )


def _strip_echoed_path(text: str, target: Path) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().rstrip(":").lower() == str(target).lower():
        lines = lines[1:]
    lines = [line for line in lines if line.strip() != "..."]
    return "\n".join(lines).strip()


def clean_story_file(target: Path) -> bool:
    """Clean a chapter file the model wrote (echoed path line, ellipsis markers)."""
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return False
    cleaned = _strip_echoed_path(text, target)
    if not cleaned or cleaned == text.strip():
        return False
    try:
        target.write_text(cleaned + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def save_creative_output(prompt: str, answer: str) -> str | None:
    """Fallback: persist the generated chapter when the model only printed it.

    Returns the written path, or None when nothing should be written.
    """
    target = creative_target(prompt)
    if target is None:
        return None
    text = (answer or "").strip()
    if len(text) < 400:
        return None
    if target.exists():
        return None  # the model already used write_file
    text = _strip_echoed_path(text, target)
    if len(text) < 400:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
    except OSError:
        return None
    return str(target)


def finalize_creative_output(prompt: str, answer: str) -> str | None:
    """Make sure the chapter ends up on disk. Returns the file path, or None."""
    target = creative_target(prompt)
    if target is None:
        return None
    if not target.exists():
        if not save_creative_output(prompt, answer):
            return None
    clean_story_file(target)
    return str(target)
