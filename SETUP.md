# Hướng dẫn setup local-agents

Tài liệu này hướng dẫn từ **máy trắng** đến **chạy được agent**. Làm đúng thứ tự, không
bỏ qua bước nào.

---

## 0. Yêu cầu

| Hệ điều hành | Cần có |
| --- | --- |
| Windows 10/11 | Python 3.11+ (script tự cài), GPU NVIDIA/AMD **hoặc** CPU |
| Linux / macOS | Docker (cách 2) hoặc Python 3.11+ + llama.cpp build |

Dung lượng: **≥ 25 GB trống** (bộ model đầy đủ ~19 GB + runtime ~1 GB + venv ~300 MB).

---

## 1. Cách 1 — Windows (khuyến nghị)

### 1.1. Clone

```powershell
cd D:\project
git clone https://github.com/MaxxAlan/ngu.git
cd ngu
```

> Nếu bạn đã có sẵn thư mục project (copy từ máy cũ) thì **không cần clone**, chỉ cần
> copy `scripts/setup.ps1`, `scripts/setup.bat` và `scripts/models.json` từ repo vào là
> được.

### 1.2. Chạy setup (Run as Administrator)

```powershell
# Click chuột phải setup.bat -> Run as administrator
setup.bat
```

Script làm 9 việc, in ra từng bước:

| # | Bước | Cụ thể |
| --- | --- | --- |
| 1 | Python toolchain | Cài `uv` (winget / installer chính thức) + python 3.11 nếu thiếu |
| 2 | Virtualenv | Tạo lại `.venv` nếu hỏng / copy từ máy khác |
| 3 | Thư viện Python | `uv sync --frozen` theo `uv.lock` (fastapi, uvicorn, httpx, mcp) |
| 4 | llama.cpp | Tải bản build khớp GPU: `cuda-12.4` / `cuda-13.5` / `vulkan` / `cpu` |
| 5 | Model gguf | Tải bộ model uncensored theo `scripts/models.json` (bỏ qua file đã có) |
| 6 | Lệnh global | Tạo `ngu.cmd` + thêm thư mục project vào **User PATH** |
| 7 | MCP | Đăng ký `local-agents` vào `~/.config/opencode/opencode.json` |
| 8 | Kiểm tra | `ngu models`, `ngu status`, smoke test (load model + 1 câu trả lời) |
| 9 | Báo cáo | `setup-report.txt` + `data/setup.log` |

Kết quả mong đợi:

```
  ================================================================
   SETUP THANH CONG - MO TERMINAL MOI VA CHAY:  ngu
  ================================================================
```

### 1.3. Mở terminal mới rồi thử

```powershell
ngu models          # danh sách model
ngu status          # trạng thái llama.cpp + task
ngu                 # chat interactive (agent làm việc trong thư mục hiện tại)
ngu run "viết fizzbuzz.py rồi chạy thử"   # 1 task
```

> **Quan trọng:** sau khi setup phải **mở terminal mới** (PATH mới chỉ có ở process mới).

### 1.4. Tham số của setup.bat

```powershell
setup.bat                  # đầy đủ (khuyến nghị)
setup.bat -Models core     # chỉ model mặc định + vision (~4.7 GB, nhanh hơn)
setup.bat -Models none     # không tải model (chỉ cài môi trường)
setup.bat -SkipSmoke       # bỏ kiểm tra end-to-end (bỏ qua bước load model)
setup.bat -Force           # tạo lại .venv + tải lại llama.cpp
```

Chạy lại `setup.bat` bất cứ lúc nào — script **idempotent**, không phá cái đang chạy.

---

## 2. Cách 2 — Docker (mọi OS)

```bash
git clone https://github.com/MaxxAlan/ngu.git
cd ngu

# build image (mặc định CPU; đổi sang --build-arg LLAMA_BACKEND=vulkan|cuda-12.8)
docker compose build

# tải model vào volume (1 lần, ~19 GB)
docker compose --profile models run --rm fetch-models

# chạy
docker compose up -d
```

- Web board: <http://127.0.0.1:8765/>
- MCP: <http://127.0.0.1:8765/mcp>

Model **không** nằm trong image (vài GB) — nó ở volume `ngu-models`, tải qua
`scripts/fetch_models.py` (cùng manifest với `setup.bat`).

---

## 3. Cách 3 — Kubernetes

```bash
kubectl apply -k k8s/
kubectl -n ngu get pods -w
kubectl -n ngu port-forward svc/ngu 8765:8765
```

Chi tiết (PVC, initContainer tải model, GPU, ingress): [`k8s/README.md`](k8s/README.md).

---

## 4. Cách 4 — Dev (đọc code, sửa code)

```bash
uv sync --group dev     # + pytest, ruff
uv run pytest           # 32 test
uv run ruff check .     # lint
uv run app.cli status   # chạy app bằng source
```

---

## 5. Lệnh thường dùng

```powershell
ngu                     # chat interactive
ngu run "<task>"        # chạy 1 task tới khi xong
ngu chat file.md a.png  # chat kèm file/ảnh đính kèm
ngu models              # danh sách model
ngu status              # runtime + số task
ngu tasks               # task gần đây
ngu show <id>           # xem 1 task
ngu cancel <id>         # huỷ task
ngu rm <id>             # xoá task
ngu serve               # web board + MCP server
ngu install             # tạo/cập nhật lệnh global + PATH
ngu novel               # tiến độ dự án truyện
```

Trong chat: `/model`, `/models`, `/attach <path>`, `/files`, `/clear-files`, `/reset`,
`/status`, `/help`, `/exit`, `/write <prompt>`.

---

## 6. Bảo mật & quyền

| Setting | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `LA_AGENT_ALLOW_ABS` | `0` | Cho phép tool đọc/ghi ngoài thư mục làm việc |
| `LA_ASK_PERMISSION` | `1` | Hỏi quyền khi thao tác ngoài thư mục làm việc |
| `LA_HOST` / `LA_PORT` | `127.0.0.1` / `8765` | Board + MCP chỉ nghe localhost |

Mặc định **an toàn**: agent bị chặn khi chạm ngoài workspace. Bật `/auto` trong chat (tương
đương `LA_AGENT_ALLOW_ABS=1`) chỉ khi bạn tin tưởng tác vụ.

Không expose board ra ngoài máy nếu chưa đặt reverse proxy + auth.

---

## 7. Xử lý sự cố

| Triệu chứng | Nguyên nhân | Cách sửa |
| --- | --- | --- |
| `[ERROR] Chua cai moi truong Python` | `.venv` không tồn tại (copy từ máy khác) | Chạy `setup.bat` |
| `ngu` không chạy lệnh | Terminal cũ chưa có PATH mới | Mở terminal mới |
| `llama-server not found` | Runtime chưa tải / sai `LA_LLAMA_DIR` | `setup.bat`, hoặc sửa `.config` |
| `CUDA PTX error` | Build llama.cpp mới hơn driver | `setup.bat` tự chọn đúng bản; hoặc dùng bản `vulkan`/`cpu` |
| Model chạy rất chậm | GPU không đủ VRAM → fallback CPU | Dùng model nhỏ hơn (`qwen2.5-coder-1.5b`) |
| `CUDA out of memory` | Model + ctx quá lớn cho card | Giảm `LA_LLAMA_CTX` (vd 8192), giảm `LA_LLAMA_NGL` |
| Board không mở | Server chưa chạy | `ngu serve` rồi mở <http://127.0.0.1:8765/> |
| MCP không kết nối | Board chưa chạy hoặc sai port | `ngu serve`; kiểm tra `opencode mcp list` |
| Tải model bị gián đoạn | Mạng chập chờn | Chạy lại `setup.bat` — tải tiếp từ file `.part` |
| Sai model trong `ngu models` | Trùng alias | Xoá model cũ trong `models/` |

Log để debug:
- `data/setup.log` — script setup
- `data/llama-<model>.log` — llama.cpp server
- `data/tasks.db` — SQLite task store

---

## 8. Cấu trúc thư mục

```
setup.bat            script 1-click (Run as Administrator)
scripts/setup.ps1    9 bước cài/sửa môi trường
scripts/models.json  manifest model (dùng chung setup + docker + k8s)
scripts/fetch_models.py  tải model theo manifest
scripts/smoke_test.py    kiểm tra end-to-end
app/                 mã nguồn agent
static/              web board
tests/               pytest
k8s/                 manifest kubernetes
Dockerfile           image chạy app + llama.cpp
.config              cấu hình (đọc/ghi bằng tay)
```

Cấu hình nằm ở `.config` (biến môi trường ghi đè được giá trị trong file).
Xem toàn bộ: [`README.md`](README.md).
