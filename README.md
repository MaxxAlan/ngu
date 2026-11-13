# local-agents

[![CI](https://github.com/MaxxAlan/ngu/actions/workflows/ci.yml/badge.svg)](https://github.com/MaxxAlan/ngu/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/MaxxAlan/ngu)](https://github.com/MaxxAlan/ngu/releases)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Local coding agent that runs **gguf models directly on llama.cpp**. CLI-first, with an
optional web board and an MCP server sharing the same task store.

> **Cài đặt từ đầu:** xem [`SETUP.md`](SETUP.md) — hướng dẫn đầy đủ (Windows / Docker / Kubernetes / dev).

- CLI: `la run "..."`, `la chat` (interactive REPL, tool loop, drag-and-drop files/images)
- Board: http://127.0.0.1:8765/
- MCP (streamable HTTP): http://127.0.0.1:8765/mcp
- Storage: `data/tasks.db` (SQLite, WAL)

The agent loop uses real tools: `read_file`, `write_file`, `edit_file`, `list_dir`,
`search_files`, `run_shell`. It runs until the task is done or the step limit is hit.

## Requirements

- Windows, Python 3.11+ (hoac Linux/macOS khi dung Docker)
- A llama.cpp build with `llama-server.exe` in `runtime/llama.cpp` (setup.bat tu tai)
- gguf models in `models/` (setup.bat tu tai)

Default runtime path is `<project>/runtime/llama.cpp`. Override with `LA_LLAMA_DIR`.
All paths default relative to the project folder, so copying the folder to another
location keeps it runnable (if Python env and models/runtime are included). Any
llama.cpp build works (CUDA, Vulkan, CPU); the runtime picks a GPU device
automatically unless `LA_LLAMA_DEVICE` is set.

## Setup (Windows)

```powershell
# Run as Administrator
setup.bat
```

`setup.bat` la script **cai/sua toan bo moi truong** trong 9 buoc:

1. Python toolchain (cai `uv` bang winget/installer neu thieu, tai python 3.11)
2. Virtual environment `.venv` (tu tao lai neu copy sang may khac - `.venv` co
   duong dan absolute nen **khong** chay duoc o may moi)
3. Thu vien Python (`uv sync --frozen` theo `uv.lock`)
4. `llama.cpp` runtime (tu tai ban phu hop GPU: `cuda-12.4` / `cuda-13.5` /
   `vulkan` / `cpu`)
5. Model gguf theo manifest `scripts/models.json` (bo **uncensored** cua ban:
   bartowski / ggml-org / HauhauCS)
6. Lenh global `ngu` + them thu muc project vao **User PATH**
7. Dang ky MCP `local-agents` trong `opencode.json` (global config)
8. Kiem tra: `ngu models`, `ngu status`, va **smoke test end-to-end**
   (load model + 1 completion)
9. Bao cao `setup-report.txt` + log `data/setup.log`

Script **idempotent** - chay lai nhieu lan cung duoc, va khong pha vong cai
dang chay neu moi truong con tot.

```powershell
setup.bat                  # day du
setup.bat -Models core     # chi model mac dinh + vision (nhanh hon)
setup.bat -Models none     # khong tai model
setup.bat -SkipSmoke       # bo qua bai kiem tra end-to-end
setup.bat -Force           # tao lai .venv + tai lai llama.cpp
```

### Docker

```bash
docker build -t ghcr.io/maxxalan/ngu:latest .
docker compose --profile models run --rm fetch-models   # tai model vao volume
docker compose up -d
# web board: http://127.0.0.1:8765/   MCP: http://127.0.0.1:8765/mcp
```

Build arg `LLAMA_BACKEND`: `cpu` (mac dinh) | `vulkan` | `cuda-12.8`.

> CI tu push image len `ghcr.io/maxxalan/ngu` moi lan push len `main` (tag `latest` + `sha`).
> Image da gan label `org.opencontainers.image.source` nen package duoc link voi repo nay
> va ke thua quyen truy cap cua repo.

### Kubernetes

```bash
cp k8s/20-secret.example.yaml k8s/secret.yaml   # HF_TOKEN (optional)
kubectl apply -k k8s/
kubectl -n ngu port-forward svc/ngu 8765:8765
```

Chi tiet: [`k8s/README.md`](k8s/README.md).

## Development

```bash
uv sync --group dev        # cai them pytest + ruff
uv run pytest              # 31 test
uv run ruff check .        # lint
```

CI/CD: `.github/workflows/` - `ci.yml` (lint + test tren ubuntu/windows,
validate `setup.ps1`, docker build, push image len GHCR), `release.yml`
(tag `v*` -> Docker image + GitHub Release), `update-llama.yml` (tu bump
`LLAMA_TAG` trong `Dockerfile`).

## CLI

```powershell
ngu                                      # interactive chat (default)
ngu run "write fizzbuzz.py and test it"
ngu run "summarize this image" photo.png
ngu chat notes.md diagram.png            # start chat with attachments
ngu models                               # list gguf models
ngu status                               # llama.cpp runtime + task counts
ngu tasks                                # recent tasks
ngu show <id>                            # prompt, steps, result
ngu cancel <id>
ngu rm <id>
ngu serve                                # web board + MCP server
ngu install                              # tạo/cập nhật lệnh global (LA_COMMAND_NAME trong .config)
```

`ngu.cmd` là launcher duy nhất: gọi từ bất kỳ thư mục nào — agent làm việc trong thư mục
hiện tại (`LA_WORKDIR`). `ngu install` tạo/cập nhật `<LA_COMMAND_NAME>.cmd` và thêm thư
mục project vào User PATH. Thao tác ngoài thư mục làm việc sẽ hỏi quyền `[y/n/a]` nếu
`LA_ASK_PERMISSION=1` và chưa `/auto`.

Flags: `-m/--model <alias>`, `--max-steps N`, `--no-stream`.

`la.bat` forwards its arguments to `python -m app.cli`, so you can drag files or images
onto it in Explorer, or paste paths into the chat prompt. Existing paths are detected
automatically and attached to the message; images switch to the vision model.

Chat commands: `/model [name]`, `/models`, `/attach <path>`, `/files`, `/clear-files`,
`/reset`, `/status`, `/help`, `/exit`, `/write <prompt>`.
`/write <prompt>` bat creative writing mode (viet truyen) cho turn do.
`Ctrl+C` interrupts the current run without
leaving the session.

## Writing mode

- `/write <prompt>` (chat) hoặc prompt chứa từ khoá truyện (`viết truyện`, `short story`,
  `novel`, `chapter`, `kể chuyện`...) → creative mode: system prompt riêng cho viết lách,
  budget final answer = `LA_CREATIVE_FINAL_MAX_TOKENS`.
- Mode viết truyện không đưa tool cho model (trừ khi bạn yêu cầu lưu ra file) → model viết
  thẳng ra, không loop tool.
- Câu hỏi thường (chào hỏi, hỏi đáp) cũng chạy không-tool → trả lời thẳng, 1 step.
- Tool loop tự dừng khi model lặp lại cùng một tool call: bị chặn và bắt model trả lời
  bằng plain text.

## Novel project (`/write`)

Chạy trong thư mục truyện (chứa `chapterNN.txt`). Mọi state nằm trong **một** file
`story.meta.json` cạnh các chương; layout file truyện không đổi.

```powershell
ngu                 # chat trong thư mục truyện
/write              # pipeline: plan -> write -> review -> retry -> persist
/write "viết tiếp chương 69"   # kèm chỉ dẫn
/write --plan       # chỉ lập kế hoạch chương tới
/write --review     # chỉ review chương vừa viết
/write --quick      # bật chế độ viết nhanh cho turn kế tiếp
/review [n]         # review chương n (mặc định: chương mới nhất)
/lore               # trích danh sách nhân vật từ các chương -> meta
/novel              # tiến độ: chương, từ, quality trend
/novel combine      # gộp toàn bộ chương thành manuscript
ngu novel           # cùng /novel, từ CLI
```

Pipeline mỗi chương:

1. **Plan (kịch bản chi tiết)** – model trả JSON: title, pov, goal, conflict, location,
   time + **danh sách scene** (mỗi scene có `goal`, `conflict`, `turn`, `beats`, `words`).
   5-10 scene/chương, 250-500 từ mỗi scene. Lưu vào meta.
2. **Write scene-by-scene** – mỗi scene = 1 tool call (`write_file` cho scene đầu,
   `append_file` cho các scene sau). Budget mỗi call lấy theo model
   (`LA_MODEL_PROFILES`: `call_tokens` 8k/16k, `max_calls` 5-10 cho chương ngắn,
   20-30 cho chương dài). Không bao giờ nhét cả chương vào 1 call.
3. **Review** – 2 tầng:
   - rule bằng code: độ dài, artefact (dòng path, "Tiếp nối từ Chương", dòng "..."),
     đoạn lặp, POV có mặt, số scene đã viết/plan, tỉ lệ thoại, độ dài câu;
   - LLM editor trả JSON 5 điểm (coherence/pacing/prose/character/dialogue) → điểm tổng
     có trọng số 0.25/0.20/0.20/0.20/0.15.
4. **Retry** – điểm < `LA_NOVEL_MIN_SCORE` (hoặc có rule fail) → viết lại kèm feedback,
   tối đa `LA_NOVEL_MAX_ATTEMPTS` lần.
5. **Persist** – lưu plan, số từ, điểm, issues, số scene đã viết, trend vào `story.meta.json`.

Mẫu `story.meta.json`:

```json
{
  "title": "...", "genre": "...", "pov": ["Amos"],
  "characters": {"Amos": {"title": "...", "role": "...", "goals": "...", "flaws": "..."}},
  "chapters": {"69": {"file": "chapter69.txt", "words": 1850, "plan": {}, "quality": {}, "attempts": 1}},
  "next": {"chapter": 70},
  "settings": {"target_words": 1800, "min_score": 0.7, "max_attempts": 2}
}
```

Model local chậm: 4B ~5-8 tok/s → 1 chương 10k ký tự mất **15-30 phút** (plan + viết +
review). Dùng `LA_NOVEL_PLAN_MODEL` / `LA_NOVEL_REVIEW_MODEL` để đặt model nhanh cho
plan và review, model chất lượng cho phần viết.

## Web board

```powershell
ngu serve        # starts web board + MCP
# then open http://127.0.0.1:8765/
```

Drag files/images onto the drop zone (or paste a screenshot into the prompt). The board
streams agent steps, tool calls and tool output over SSE. It can also run tasks created
by the CLI and MCP.

## Models

Aliases are derived from gguf filenames:

| alias | file |
| --- | --- |
| `qwen2.5-coder-1.5b` | `Qwen2.5-Coder-1.5B-Instruct-abliterated-Q4_K_M.gguf` |
| `qwen2.5-coder-3b` | `Qwen2.5-Coder-3B-Instruct-abliterated-Q4_K_M.gguf` (default) |
| `qwen2.5-coder-14b` | `Qwen2.5-Coder-14B-q4_k_m.gguf` |
| `qwen2.5-vl-3b` | `Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf` + `mmproj-*.gguf` (vision) |
| `gemma-4-e4b` | `Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q5_K_M.gguf` |

A model becomes `vision` when a matching `mmproj-*.gguf` exists. Tasks with images
automatically use `LA_DEFAULT_VISION_MODEL`.

## Configuration

Edit settings in **`.config`** (project root). `la.bat` only initializes/launches the
program. Environment variables override values in `.config`. Paths can be relative to
the project folder.

| key | default | purpose |
| --- | --- | --- |
| `LA_LLAMA_DIR` | `./runtime/llama.cpp` | llama.cpp build directory (`llama-server.exe`) |
| `LA_LLAMA_DEVICE` | auto | force a device id, e.g. `CUDA0` |
| `LA_LLAMA_CTX` | `4096` | context size per server |
| `LA_LLAMA_NGL` | `999` | GPU layers |
| `LA_MODELS_DIR` | `./models` | gguf search path |
| `LA_DATA_DIR` | `./data` | sqlite + uploads |
| `LA_DEFAULT_MODEL` | `qwen2.5-coder-3b` | text model |
| `LA_DEFAULT_VISION_MODEL` | `qwen2.5-vl-3b` | image model |
| `LA_AGENT_MAX_STEPS` | `12` | tool loop limit |
| `LA_AGENT_ALLOW_ABS` | `0` | allow tools outside the workspace (same as `/auto`) |
| `LA_FINAL_MAX_TOKENS` | `4096` | token budget cho final answer |
| `LA_CREATIVE_FINAL_MAX_TOKENS` | `16384` | token budget khi viết truyện |
| `LA_TOOLS_ALWAYS` | `0` | luôn đưa tool cho model (mặc định: chỉ khi yêu cầu cần file/shell) |
| `LA_AGENT_SYSTEM_PROMPT` | built-in | override system prompt của coding agent |
| `LA_CREATIVE_SYSTEM_PROMPT` | built-in | override system prompt khi viết truyện |
| `LA_NOVEL_TARGET_WORDS` | `1800` | độ dài mục tiêu 1 chương (từ) |
| `LA_NOVEL_MIN_SCORE` | `0.7` | ngưỡng score để accept chương sau review |
| `LA_NOVEL_MAX_ATTEMPTS` | `2` | số lần viết lại khi review fail |
| `LA_NOVEL_TAIL_CHARS` | `1800` | số ký tự cuối chương trước làm ngữ cảnh |
| `LA_NOVEL_PLAN_MODEL` / `LA_NOVEL_REVIEW_MODEL` | empty | model riêng cho plan / review |
| `LA_MODEL_PROFILES` | see `.config` | budget tool call theo model: `alias:call_tokens:max_calls` |
| `LA_CALL_TOKENS` | `8192` | token budget mặc định cho 1 tool call |
| `LA_MAX_CALLS` | `12` | số tool call tối đa cho 1 chương |
| `LA_MAX_CONCURRENCY` | `1` | parallel tasks in the server |
| `LA_COMMAND_NAME` | `ngu` | tên lệnh global (dùng với `ngu install`) |
| `LA_ASK_PERMISSION` | `1` | hỏi quyền khi thao tác ngoài thư mục làm việc (không `/auto`) |
| `LA_HOST` / `LA_PORT` | `127.0.0.1` / `8765` | web + MCP bind |

## HTTP API

`GET /api/health`, `GET /api/models`, `POST /api/upload`, `GET/POST /api/tasks`,
`GET /api/tasks/{id}`, `POST /api/tasks/{id}/run`, `POST /api/tasks/{id}/cancel`,
`DELETE /api/tasks/{id}`, `GET /api/events` (SSE: `task`, `token`, `agent_step`,
`tool_result`).

## MCP tools

| tool | purpose |
| --- | --- |
| `assign_task(prompt, title, agent, model, attachments, auto_run)` | create task, queue it |
| `wait_for_task(task_id, timeout_seconds)` | block until done/failed/cancelled |
| `get_task(task_id)` | read one task with result and steps |
| `list_tasks(status, limit)` | newest first |
| `run_task(task_id)` | queue a draft/failed task |
| `cancel_task(task_id)` | cancel queued/running |
| `list_models()` | gguf models + running state |
| `server_status()` | counts, active ids, llama.cpp health |

Already registered in `~/.config/opencode/opencode.json` as a remote MCP server named
`local-agents` (`setup.bat` does this automatically; manual:

```powershell
opencode mcp add local-agents --url http://127.0.0.1:8765/mcp --global
```

). Start the server (`ngu serve`) before opencode connects.

## Layout

```
setup.bat           one-click setup / repair (Run as Administrator)
scripts/setup.ps1   9-step installer (python, venv, deps, runtime, models, PATH, MCP, verify)
scripts/models.json manifest model gguf (dung chung setup.bat + docker + k8s)
scripts/fetch_models.py  tai model theo manifest (docker/k8s)
scripts/smoke_test.py    kiem tra end-to-end (load model + 1 completion)
scripts/patch_opencode_mcp.py  dang ky MCP vao opencode.json (fallback)
app/cli.py           CLI entry point (run/chat/models/tasks/status/serve/install)
app/agent.py         tool-use loop + tool-call parser
app/tools/           file/shell/search tools + workspace sandbox
app/llm.py           OpenAI-compatible client for llama-server
app/llama_runtime.py spawns one llama-server per model on demand
app/models.py        gguf discovery, aliases, mmproj matching
app/attachments.py   uploads, text/image classification, multimodal content
app/runner.py        server-side task queue + SSE
app/api.py           FastAPI app, upload, events, MCP mount
app/novel.py         novel project pipeline (plan -> write -> review -> persist)
static/              web board
tests/               pytest (config, models, tool parsing, fs tools)
k8s/                 kubernetes manifests (deployment, pvc, configmap, ingress)
Dockerfile           image chay app + llama.cpp runtime (cpu/vulkan/cuda)
```

## Troubleshooting

- **`[ERROR] Chua cai moi truong Python` khi goi `ngu`**: `.venv` chua ton tai hoac
  copy tu may khac (venv chua duong dan absolute). Chay `setup.bat` (Run as Administrator).
- **Copy sang may khong chay**: chay `setup.bat` o may moi - no tu tao lai `.venv`,
  tai `llama.cpp` theo GPU va tai model.
- **llama-server not found**: check `runtime/llama.cpp/llama-server.exe`, or set
  `LA_LLAMA_DIR` in `.config`.
- **CUDA PTX error**: the build is newer than the driver; use a matching CUDA build
  (e.g. `cuda-12.4` on driver 550.x) or the Vulkan build. `setup.bat` tu chon ban
  theo version driver.
- **First response is slow**: CUDA kernels are JIT-compiled once; later runs are fast.
- **Wrong GPU**: run `llama-server --list-devices` and set `LA_LLAMA_DEVICE` in `.config`.
- **Model ignores tools**: small models need the JSON tool protocol in
  `AGENT_SYSTEM_PROMPT`; use a 3B+ coder model.
- **Outside workspace / permission denied**: bật `/auto` trong chat, set
  `LA_AGENT_ALLOW_ABS=1` trong `.config`, hoặc trả lời `y/a` khi được hỏi quyền.
- **Xem log**: `data/setup.log` (setup), `data/llama-<model>.log` (llama.cpp server).

