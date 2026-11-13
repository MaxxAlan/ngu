# models/

Thu muc nay chua model `.gguf` (bi `.gitignore` bo qua - file rat lon).

## Cai model

**Windows (khuyen nghi):** chay `setup.bat` (Run as Administrator) o thu muc goc project.
Script tu tai dung bo model theo `scripts/models.json`:

```powershell
setup.bat                 # bo day du
setup.bat -Models core    # chi model mac dinh + vision
```

**Docker / Kubernetes:**

```bash
docker compose --profile models run --rm fetch-models
python scripts/fetch_models.py --set all
```

## Danh sach model

| file | alias | nguon |
| --- | --- | --- |
| `Qwen2.5-Coder-3B-Instruct-abliterated-Q4_K_M.gguf` | `qwen2.5-coder-3b` | bartowski |
| `Qwen2.5-Coder-1.5B-Instruct-abliterated-Q4_K_M.gguf` | `qwen2.5-coder-1.5b` | bartowski |
| `Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf` + `mmproj-...gguf` | `qwen2.5-vl-3b` | ggml-org |
| `Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q5_K_M.gguf` | `gemma-4-e4b` | HauhauCS |
| `Nemotron3-Nano-4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf` | `nemotron3-nano-4b-...` | HauhauCS |
| `Qwen2.5-7B-Instruct-Q4_K_M.gguf` | `qwen2.5-7b` | bartowski |

Manifest chinh thuc: [`scripts/models.json`](../scripts/models.json).

## Kiem tra

```powershell
ngu models
```
