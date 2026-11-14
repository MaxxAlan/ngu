#!/usr/bin/env python3
"""Download GGUF models from HuggingFace Hub to local models/ directory.

Usage:
    python scripts/download_model.py <repo> <pattern> [--alias NAME]

Examples:
    python scripts/download_model.py BlossomsAI/Qwen2.5-Coder-7B-Instruct-Uncensored-GGUF q4_k_m
    python scripts/download_model.py Qwen/Qwen2.5-Coder-3B-Instruct-GGUF q4_k_m --alias coder-3b
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

# ---- paths ---------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
MODELS_DIR = PROJECT_DIR / "models"
CONFIG_FILE = PROJECT_DIR / ".config"

HF_API = "https://huggingface.co/api/models/{repo}"
HF_RESOLVE = "https://huggingface.co/{repo}/resolve/main/{filename}"
CHUNK = 1 << 20  # 1 MB


# ---- progress bar --------------------------------------------------------
def _progress(downloaded: int, total: int) -> None:
    if total <= 0:
        sys.stdout.write(f"\r  downloaded {downloaded / 1024**2:.1f} MB")
    else:
        pct = downloaded * 100 // total
        bar = "#" * (pct // 5) + "." * (20 - pct // 5)
        mb_done = downloaded / 1024**2
        mb_total = total / 1024**2
        sys.stdout.write(f"\r  [{bar}] {pct:3d}%  {mb_done:.1f}/{mb_total:.1f} MB")
    sys.stdout.flush()


try:
    from tqdm import tqdm as _tqdm  # type: ignore

    def _download(url: str, dest: Path) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "local-agents/1.0"})
        with urllib.request.urlopen(req) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            with open(dest, "wb") as f, _tqdm(
                total=total, unit="B", unit_scale=True, desc=dest.name
            ) as bar:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    bar.update(len(chunk))

except ImportError:  # no tqdm – use simple percent printer

    def _download(url: str, dest: Path) -> None:  # type: ignore[misc]
        req = urllib.request.Request(url, headers={"User-Agent": "local-agents/1.0"})
        with urllib.request.urlopen(req) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    _progress(downloaded, total)
        print()  # newline after progress


# ---- HuggingFace helpers -------------------------------------------------

def _list_files(repo: str) -> list[dict]:
    """Return siblings from HF model metadata."""
    import json

    url = HF_API.format(repo=repo)
    req = urllib.request.Request(url, headers={"User-Agent": "local-agents/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data.get("siblings", [])
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] could not fetch model metadata from HF API: {exc}")
        return []


def _pick_file(siblings: list[dict], pattern: str) -> str | None:
    """Pick the best filename matching pattern (case-insensitive substring)."""
    pat = pattern.lower()
    # prefer exact match, then substring
    names = [s["rfilename"] for s in siblings if s["rfilename"].lower().endswith(".gguf")]
    # exact substring match
    matches = [n for n in names if pat in n.lower()]
    if not matches:
        return None
    # prefer the one with the fewest parts (smallest / most targeted quant)
    matches.sort(key=lambda n: len(n))
    return matches[0]


# ---- .config helpers -----------------------------------------------------

def _read_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not CONFIG_FILE.exists():
        return cfg
    for raw in CONFIG_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def _write_config(cfg: dict[str, str]) -> None:
    lines = []
    if CONFIG_FILE.exists():
        lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()

    new_lines: list[str] = []
    written: set[str] = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in cfg:
                new_lines.append(f"{k}={cfg[k]}")
                written.add(k)
                continue
        new_lines.append(line)
    for k, v in cfg.items():
        if k not in written:
            new_lines.append(f"{k}={v}")
    CONFIG_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def _register_model(alias: str, filename: str) -> None:
    """Write LA_MODEL_<ALIAS>=<filename stem> to .config."""
    stem = Path(filename).stem
    key = f"LA_MODEL_{alias.upper().replace('-', '_')}"
    cfg = _read_config()
    cfg[key] = stem
    _write_config(cfg)
    print(f"  registered: {key}={stem}")


# ---- main ----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download a GGUF model from HuggingFace Hub.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("repo", help='HuggingFace repo, e.g. "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF"')
    parser.add_argument("pattern", help='Filename pattern to match, e.g. "q4_k_m"')
    parser.add_argument("--alias", default=None, help="Alias to register in .config (auto-derived if omitted)")
    parser.add_argument("--models-dir", default=None, help=f"Where to save (default: {MODELS_DIR})")
    parser.add_argument("--force", action="store_true", help="Re-download even if already present")
    args = parser.parse_args(argv)

    models_dir = Path(args.models_dir) if args.models_dir else MODELS_DIR
    models_dir.mkdir(parents=True, exist_ok=True)

    repo = args.repo.strip().strip("/")
    pattern = args.pattern.strip()
    print(f"[download] repo={repo}  pattern={pattern}")

    siblings = _list_files(repo)
    if not siblings:
        # Fallback: try common naming convention
        guess = f"{repo.split('/')[-1].replace('.', '-').lower()}-{pattern}.gguf"
        filename = guess
        print(f"[warn] could not list files; guessing filename: {filename}")
    else:
        filename = _pick_file(siblings, pattern)
        if not filename:
            gguf_names = [s["rfilename"] for s in siblings if s["rfilename"].endswith(".gguf")]
            print(f"[error] no GGUF file matching '{pattern}' in {repo}")
            if gguf_names:
                print("  available GGUF files:")
                for n in gguf_names:
                    print(f"    {n}")
            return 1
        print(f"  matched file: {filename}")

    dest = models_dir / filename
    if dest.exists() and not args.force:
        size_mb = dest.stat().st_size / 1024**2
        print(f"  already downloaded: {dest}  ({size_mb:.1f} MB)  (use --force to re-download)")
    else:
        url = HF_RESOLVE.format(repo=repo, filename=urllib.request.quote(filename))
        print(f"  downloading from: {url}")
        print(f"  saving to: {dest}")
        tmp = dest.with_suffix(".tmp")
        try:
            _download(url, tmp)
            tmp.rename(dest)
        except Exception as exc:  # noqa: BLE001
            if tmp.exists():
                tmp.unlink()
            print(f"[error] download failed: {exc}")
            return 1
        size_mb = dest.stat().st_size / 1024**2
        print(f"  saved: {dest}  ({size_mb:.1f} MB)")

    # Derive alias from filename stem if not provided
    alias = args.alias
    if not alias:
        stem = Path(filename).stem
        # simplify: strip common suffix patterns
        alias = re.sub(r"[-_.](q\d[^-]*)$", "", stem, flags=re.IGNORECASE).lower()
        alias = re.sub(r"[^a-z0-9]+", "-", alias).strip("-")
        alias = alias[:40]

    _register_model(alias, filename)
    print(f"[done] model available as alias '{alias}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
