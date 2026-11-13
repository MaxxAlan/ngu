# runtime/

Chua **llama.cpp build** (`llama-server` + `.dll`/`.so`) - bi `.gitignore` bo qua.

## Windows

`setup.bat` (Run as Administrator) tu:

1. Phat hien GPU (NVIDIA / AMD / khong co GPU)
2. Chon ban phu hop: `cuda-12.4` / `cuda-13.5` / `vulkan` / `cpu`
3. Tai tu GitHub releases cua `ggml-org/llama.cpp`, giai nen vao `runtime/llama.cpp/`

## Docker / Kubernetes

Da co san trong image (xem `Dockerfile`, bien `LLAMA_BACKEND`).

## Tu cai dat

```powershell
# mac dinh: runtime/llama.cpp
ngu status
```

Tuy chinh duong dan trong `.config`:

```
LA_LLAMA_DIR=runtime/llama.cpp
```

## Loi thuong gap

- **CUDA PTX error**: ban build moi hon driver cua may. Dung `cuda-12.4` (driver >= 550)
  hoac ban `vulkan`/`cpu`.
- **Wrong GPU**: `llama-server --list-devices` roi dat `LA_LLAMA_DEVICE=CUDA0` trong `.config`.
- **First response cham**: CUDA kernel duoc JIT lan dau, cac lan sau se nhanh.
