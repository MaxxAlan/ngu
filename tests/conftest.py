import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Workspace rieng cho test tools (khong dung thu muc project that)."""
    from app import tools

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(config, "AGENT_ALLOW_ABS", False)
    monkeypatch.setattr(config, "ASK_PERMISSION", False)
    tools.set_workspace(ws)
    yield ws
    tools.set_workspace(ROOT)
