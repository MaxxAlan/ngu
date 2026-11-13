"""Tai model gguf theo manifest scripts/models.json.

Dung cho Docker / Kubernetes (initContainer) va de xem lai danh sach model.
Tai duoc tiep tuc (resume) neu file .part con lai.

Usage:
    python scripts/fetch_models.py --set core
    python scripts/fetch_models.py --set all --dest models
    python scripts/fetch_models.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).resolve().parent / "models.json"
HF = "https://huggingface.co/{repo}/resolve/main/{name}"
MIN_SIZE = 50 * 1024 * 1024  # 50 MB


def load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        print(f"error: khong tim thay {MANIFEST}")
        return []
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [d for d in data if isinstance(d, dict) and d.get("file") and d.get("repo")]


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def download(url: str, dest: Path, retries: int = 4) -> bool:
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        offset = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": "local-agents-setup"})
        if offset:
            req.add_header("Range", f"bytes={offset}-")
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                total = res.headers.get("Content-Length")
                total = (int(total) + offset) if total else 0
                mode = "ab" if offset else "wb"
                if offset and res.status == 200:
                    # server ignore Range -> tai lai tu dau
                    offset = 0
                    mode = "wb"
                got = offset
                last = 0.0
                with open(part, mode) as fh:
                    while True:
                        chunk = res.read(1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        got += len(chunk)
                        now = time.time()
                        if now - last > 2:
                            last = now
                            if total:
                                pct = got * 100.0 / total
                                sys.stdout.write(
                                    f"\r    {pct:5.1f}%  {human(got)} / {human(total)}   "
                                )
                                sys.stdout.flush()
            if total:
                sys.stdout.write("\n")
            if part.stat().st_size < MIN_SIZE:
                print(f"    file qua nho ({human(part.stat().st_size)})")
                return False
            part.replace(dest)
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            print(f"\n    attempt {attempt}/{retries} that bai: {exc}")
            if attempt < retries:
                time.sleep(min(30, 5 * attempt))
    return False


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="tai model gguf")
    ap.add_argument("--set", dest="group", default="core", choices=["core", "all"], help="bo model can tai")
    ap.add_argument("--dest", default=str(ROOT / "models"), help="thu muc dich")
    ap.add_argument("--list", action="store_true", help="chi hien danh sach")
    args = ap.parse_args(argv[1:])

    items = load_manifest()
    if not items:
        return 1
    if args.group == "core":
        items = [i for i in items if i.get("group") == "core"]

    print(f"manifest: {MANIFEST}")
    for it in items:
        url = HF.format(repo=it["repo"], name=urllib.parse.quote(it["file"]))
        print(f"  [{it.get('group', 'all')}] {it['file']}")
        print(f"      {url}")
        print(f"      {it.get('note', '')}")
    if args.list:
        return 0

    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    failed = 0
    for it in items:
        dest = dest_dir / it["file"]
        if dest.exists() and dest.stat().st_size > MIN_SIZE:
            print(f"  [OK] {it['file']} da co san ({human(dest.stat().st_size)})")
            continue
        url = HF.format(repo=it["repo"], name=urllib.parse.quote(it["file"]))
        print(f"  tai {it['file']} ...")
        if download(url, dest):
            print(f"  [OK] xong {it['file']} ({human(dest.stat().st_size)})")
        else:
            print(f"  [FAIL] {it['file']}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
