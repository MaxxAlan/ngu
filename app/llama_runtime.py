from __future__ import annotations

import asyncio
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from urllib.request import urlopen

from . import config
from .models import ModelInfo, discover_models

log = logging.getLogger("local-agents.llama")


class RuntimeUnavailable(RuntimeError):
    pass


@dataclass
class Server:
    model: ModelInfo
    port: int
    proc: asyncio.subprocess.Process
    base_url: str
    started: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((config.LLAMA_HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LlamaRuntime:
    """Spawn one llama-server process per model, on demand."""

    def __init__(self) -> None:
        self._servers: dict[str, Server] = {}
        self._device: str | None = None
        self._device_resolved = False
        self._lock = asyncio.Lock()

    # ---- device detection ------------------------------------------------
    async def device(self) -> str | None:
        if self._device_resolved:
            return self._device
        if config.LLAMA_DEVICE:
            self._device = config.LLAMA_DEVICE
            self._device_resolved = True
            return self._device
        self._device = await self._detect_device()
        self._device_resolved = True
        return self._device

    async def _detect_device(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                str(config.LLAMA_SERVER),
                "--list-devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        except Exception as exc:  # noqa: BLE001
            log.warning("device detection failed: %s", exc)
            return None
        text = out.decode("utf-8", "replace")
        devices: list[tuple[int, str, str]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("available"):
                continue
            if ":" not in line:
                continue
            dev_id, rest = line.split(":", 1)
            name = rest.strip().split("(")[0].strip()
            low = name.lower()
            score = 0
            if "nvidia" in low or "geforce" in low or "rtx" in low or "gtx" in low:
                score = 3
            elif "radeon" in low and "graphics" not in low:
                score = 2
            elif "rtx" in low:
                score = 2
            devices.append((score, dev_id, name))
        if not devices:
            return None
        devices.sort(key=lambda d: d[0], reverse=True)
        log.info("llama device: %s", devices[0][2])
        return devices[0][1]

    # ---- server lifecycle ------------------------------------------------
    async def ensure(self, model: ModelInfo) -> str:
        async with self._lock:
            srv = self._servers.get(model.name)
            if srv and srv.alive:
                return srv.base_url
            if srv and not srv.alive:
                del self._servers[model.name]
            if config.SWAP_UNLOAD:
                # Free VRAM: keep only the requested model running.
                for name in [n for n, s in self._servers.items() if n != model.name and s.alive]:
                    await self.stop(name)
            return await self._start(model)

    async def _start(self, model: ModelInfo) -> str:
        if not config.LLAMA_SERVER.exists():
            raise RuntimeUnavailable(
                f"llama-server not found at {config.LLAMA_SERVER}. "
                "Set LA_LLAMA_DIR to the llama.cpp build directory."
            )
        device = await self.device()
        last_error = ""
        for attempt, (ngl, ctx) in enumerate(self._launch_profiles()):
            port = _free_port()
            args = [
                str(config.LLAMA_SERVER),
                "-m", str(model.path),
                "--host", config.LLAMA_HOST,
                "--port", str(port),
                "-c", str(ctx),
                "-ngl", str(ngl),
                "--jinja",
            ]
            if model.mmproj:
                args += ["--mmproj", str(model.mmproj)]
            if device:
                args += ["--device", device]
            cache_type = (config.LLAMA_CACHE_TYPE or "").strip()
            if cache_type:
                args += ["--cache-type-k", cache_type, "--cache-type-v", cache_type]

            log.info(
                "starting llama-server for %s (ngl=%s ctx=%s) on port %d",
                model.name, ngl, ctx, port,
            )
            log_file = self._log_path(model.name)
            try:
                handle = open(log_file, "ab")
            except OSError:
                handle = asyncio.subprocess.DEVNULL
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=handle,
                )
            finally:
                if hasattr(handle, "close"):
                    handle.close()
            base_url = f"http://{config.LLAMA_HOST}:{port}"
            srv = Server(model=model, port=port, proc=proc, base_url=base_url)
            self._servers[model.name] = srv

            if await self._wait_ready(srv):
                if attempt:
                    log.warning(
                        "llama-server for %s only started with ngl=%s ctx=%s (VRAM too small for the full config)",
                        model.name, ngl, ctx,
                    )
                return base_url

            await self.stop(model.name)
            last_error = f"ngl={ngl} ctx={ctx} (log: {log_file})"
            log.warning("llama-server for %s failed to start with %s", model.name, last_error)

        raise RuntimeUnavailable(
            f"llama-server for {model.name} failed to become ready. Last try: {last_error}"
        )

    def _launch_profiles(self) -> list[tuple[int, int]]:
        """(n_gpu_layers, ctx) combos, most capable first.

        A small GPU cannot hold the full model: llama.cpp aborts when the user
        forces -ngl 999. Fall back to fewer layers / smaller context.
        """
        profiles: list[tuple[int, int]] = [
            (config.LLAMA_NGL, config.LLAMA_CTX),
            (config.LLAMA_NGL, config.LLAMA_FALLBACK_CTX),
            (24, config.LLAMA_FALLBACK_CTX),
            (12, config.LLAMA_FALLBACK_CTX),
            (0, config.LLAMA_FALLBACK_CTX),
        ]
        seen: set[tuple[int, int]] = set()
        out: list[tuple[int, int]] = []
        for p in profiles:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _log_path(self, model_name: str) -> str:
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", model_name)
            return str(config.DATA_DIR / f"llama-{safe}.log")
        except OSError:
            return str(config.DATA_DIR / "llama.log")

    async def _wait_ready(self, srv: Server) -> bool:
        deadline = time.time() + config.LLAMA_LOAD_TIMEOUT
        while time.time() < deadline:
            if not srv.alive:
                return False
            try:
                with urlopen(srv.base_url + "/health", timeout=2) as res:
                    if res.status == 200:
                        return True
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.5)
        return False

    async def stop(self, name: str) -> None:
        srv = self._servers.pop(name, None)
        if not srv:
            return
        if srv.alive:
            srv.proc.terminate()
            try:
                await asyncio.wait_for(srv.proc.wait(), timeout=10)
            except TimeoutError:
                srv.proc.kill()

    async def stop_all(self) -> None:
        for name in list(self._servers):
            await self.stop(name)

    # ---- model helpers ---------------------------------------------------
    async def resolve_base_url(self, name: str) -> str:
        model = next((m for m in discover_models() if m.name == name), None)
        if model is None:
            raise RuntimeUnavailable(
                f"unknown model '{name}'. Available: "
                + ", ".join(m.name for m in discover_models())
            )
        return await self.ensure(model)

    def running(self) -> list[str]:
        return [n for n, s in self._servers.items() if s.alive]


runtime = LlamaRuntime()
