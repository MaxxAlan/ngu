import asyncio
import json

from app import novel


def _fake_resp(payload: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


def test_extract_lore_saves_world_and_threads(tmp_path, monkeypatch):
    root = tmp_path / "story"
    root.mkdir()
    (root / "chapter1.txt").write_text("Mot cau chuyen mo dau.", encoding="utf-8")
    project = novel.Project(root)

    payload = {
        "characters": {"An": {"role": "hero", "relationships": "ban cua Binh"}},
        "world": {"place": "Sai Gon", "time": "2040", "atmosphere": "noir", "rules": ""},
        "threads": ["An tim em gai mat tich"],
    }

    async def fake_chat(model, messages, tools=None, max_tokens=None):
        return _fake_resp(payload)

    monkeypatch.setattr(novel.llm, "chat", fake_chat)

    chars = asyncio.run(novel.extract_lore(project, "fake-model"))
    assert "An" in chars
    assert project.meta["world"]["place"] == "Sai Gon"
    assert project.meta["threads"] == ["An tim em gai mat tich"]
    assert "Setting:" in project.world_block()
    assert "Open plot threads" in project.threads_block()


def test_suggest_ideas_uses_context(tmp_path, monkeypatch):
    root = tmp_path / "story2"
    root.mkdir()
    (root / "chapter1.txt").write_text("Chuong mo dau.", encoding="utf-8")
    project = novel.Project(root)
    project.meta["threads"] = ["bi mat ve ke phan boi"]
    project.save()

    payload = {
        "ideas": [
            {"title": "Phan boi lo dien", "pitch": "Ke phan boi bi phat hien.", "uses_thread": "bi mat ve ke phan boi"},
            {"title": "", "pitch": "", "uses_thread": ""},
        ]
    }

    async def fake_chat(model, messages, tools=None, max_tokens=None):
        return _fake_resp(payload)

    monkeypatch.setattr(novel.llm, "chat", fake_chat)

    ideas = asyncio.run(novel.suggest_ideas(project, "fake-model"))
    assert len(ideas) == 1
    assert ideas[0]["title"] == "Phan boi lo dien"
    assert project.meta["ideas"]["items"] == ideas
