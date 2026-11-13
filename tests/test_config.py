from pathlib import Path

from app import config


def test_config_file_is_loaded():
    assert config.CONFIG_FILE.exists(), "thieu file .config o thu muc project"
    assert config.DEFAULT_MODEL


def test_load_config_file(tmp_path: Path):
    p = tmp_path / ".config"
    p.write_text('# comment\nLA_HOST=0.0.0.0\nLA_PORT="9999"\n\n# x\n', encoding="utf-8")
    cfg = config._load_config_file(p)
    assert cfg["LA_HOST"] == "0.0.0.0"
    assert cfg["LA_PORT"] == "9999"


def test_int_bool_parsing():
    assert config._int("LA_PORT", 1) == 8765
    assert config._bool("LA_ASK_PERMISSION", False) is True
    assert config._bool("LA_AGENT_ALLOW_ABS", True) is False


def test_is_creative():
    assert config.is_creative("viết truyện ngắn cho tôi")
    assert config.is_creative("write a novel chapter 3")
    assert not config.is_creative("fix bug trong python")
    assert not config.is_creative("chuong trinh python don gian")


def test_needs_tools():
    assert config.needs_tools("đọc file notes.md và tóm tắt")
    assert config.needs_tools("fix the bug in run_shell script")
    assert not config.needs_tools("chào bạn, hôm nay thế nào?")


def test_model_profile():
    prof = config.model_profile("qwen2.5-coder-3b")
    assert prof["call_tokens"] >= 1024
    assert prof["max_calls"] >= 1
    # alias khong biet -> dung entry "default"
    assert config.model_profile("model-khong-biet") == config.model_profile("default")


def test_creative_target(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path)
    (tmp_path / "chapter01.txt").write_text("chapter one", encoding="utf-8")
    target = config.creative_target("viết truyện: viết tiếp chapter01.txt trong " + str(tmp_path))
    assert target is not None
    # novel pipeline cung dat ten theo scheme dang co (chapter + so, khong zero-pad)
    assert target.name == "chapter2.txt"


def test_path_regex_supports_posix_and_windows():
    # windows
    assert config._PATH_RE.search("xem C:\\Users\\me\\novel") is not None
    # posix
    assert config._PATH_RE.search("xem /home/me/novel") is not None
    # khong phai path
    assert config._PATH_RE.search("xem https://example.com/a") is None
    assert config._PATH_RE.search("5/10") is None


def test_system_prompt_selection():
    assert config.system_prompt_for("viết truyện ngắn") == config.CREATIVE_SYSTEM_PROMPT
    assert config.system_prompt_for("fix the parser") == config.AGENT_SYSTEM_PROMPT
