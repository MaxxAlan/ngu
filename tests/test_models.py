from pathlib import Path

from app import config, models


def _make(path: Path, name: str, size: int = 1024) -> Path:
    p = path / name
    p.write_bytes(b"\0" * size)
    return p


def test_alias_for_known_files():
    assert models._alias_for("Qwen2.5-Coder-3B-Instruct-abliterated-Q4_K_M") == "qwen2.5-coder-3b"
    assert models._alias_for("Qwen2.5-Coder-1.5B-Instruct-abliterated-Q4_K_M") == "qwen2.5-coder-1.5b"
    assert models._alias_for("Qwen2.5-7B-Instruct-Q4_K_M") == "qwen2.5-7b"
    assert models._alias_for("Qwen2.5-VL-3B-Instruct-Q4_K_M") == "qwen2.5-vl-3b"


def test_discover_models(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    _make(tmp_path, "Qwen2.5-Coder-3B-Instruct-abliterated-Q4_K_M.gguf")
    _make(tmp_path, "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf")
    _make(tmp_path, "mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf")

    found = {m.name: m for m in models.discover_models()}
    assert "qwen2.5-coder-3b" in found
    assert found["qwen2.5-coder-3b"].kind == "text"
    assert found["qwen2.5-vl-3b"].kind == "vision"
    assert found["qwen2.5-vl-3b"].mmproj is not None
    assert found["qwen2.5-vl-3b"].mmproj.name.startswith("mmproj-")


def test_mmproj_alternative_naming(tmp_path: Path, monkeypatch):
    """Projector dat ten kieu khac (mmproj o giua ten) van phai duoc nhan."""
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    _make(tmp_path, "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf")
    _make(tmp_path, "Qwen2.5-VL-3B-Instruct.mmproj-fp16.gguf")

    found = {m.name: m for m in models.discover_models()}
    assert found["qwen2.5-vl-3b"].kind == "vision"


def test_text_model_does_not_get_mmproj(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    _make(tmp_path, "Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf")
    _make(tmp_path, "mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf")

    found = {m.name: m for m in models.discover_models()}
    assert found["qwen2.5-coder-3b"].kind == "text"
    assert found["qwen2.5-coder-3b"].mmproj is None


def test_default_model_fallback(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(config, "DEFAULT_MODEL", "khong-co-model-nay")
    assert models.default_model() is None
    _make(tmp_path, "Qwen2.5-Coder-3B-Instruct-abliterated-Q4_K_M.gguf")
    assert models.default_model().name == "qwen2.5-coder-3b"
