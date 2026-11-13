"""Kiem tra end-to-end: start llama-server cho model mac dinh va chay 1 completion.

Dung boi setup.bat (scripts/setup.ps1). Exit code 0 = OK.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import (
    config,  # noqa: E402
    llm,  # noqa: E402
)
from app.llama_runtime import runtime  # noqa: E402
from app.models import default_model, discover_models  # noqa: E402


async def main() -> int:
    models = discover_models()
    if not models:
        print(f"NO_MODELS: khong co file .gguf nao trong {config.MODELS_DIR}")
        return 2

    model = default_model()
    print(f"model       : {model.name}")
    print(f"file        : {model.path}")
    print(f"kind        : {model.kind}")
    print(f"server      : {config.LLAMA_SERVER}")
    print(f"device      : {config.LLAMA_DEVICE or 'auto'}")
    print(f"model count : {len(models)}")

    started = time.time()
    try:
        base_url = await runtime.ensure(model)
    except Exception as exc:  # noqa: BLE001
        print(f"SERVER_FAIL: {exc}")
        return 3
    print(f"server up   : {base_url} ({time.time() - started:.1f}s)")

    try:
        result = await llm.chat(
            model.name,
            [{"role": "user", "content": "Reply with exactly one word: OK"}],
            max_tokens=16,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"CHAT_FAIL: {exc}")
        await runtime.stop_all()
        return 4

    text = ""
    try:
        text = str(result["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001
        text = str(result)[:400]
    print(f"reply       : {text.strip()}")

    await runtime.stop_all()
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("SMOKE_INTERRUPTED")
        sys.exit(5)
