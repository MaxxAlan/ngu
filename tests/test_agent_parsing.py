from app.agent import parse_tool_calls


def test_fenced_json_tool_call():
    calls = parse_tool_calls('```json\n{"name": "list_dir", "arguments": {"path": "."}}\n```')
    assert len(calls) == 1
    assert calls[0]["name"] == "list_dir"
    assert calls[0]["arguments"]["path"] == "."


def test_plain_json_tool_call():
    calls = parse_tool_calls('{"name": "read_file", "arguments": {"path": "a.py"}}')
    assert len(calls) == 1
    assert calls[0]["name"] == "read_file"


def test_tool_call_tag():
    calls = parse_tool_calls('<tool_call>{"name": "list_dir", "arguments": {}}</tool_call>')
    assert len(calls) == 1
    assert calls[0]["name"] == "list_dir"


def test_unknown_tool_is_ignored():
    assert parse_tool_calls('```json\n{"name": "rm_rf", "arguments": {}}\n```') == []


def test_plain_text_is_not_a_tool_call():
    assert parse_tool_calls("Toi se doc file x.py roi chay lai.") == []


def test_arguments_as_string():
    calls = parse_tool_calls('```json\n{"name": "read_file", "arguments": "{\\"path\\": \\"x.py\\"}"}\n```')
    assert len(calls) == 1
    assert calls[0]["arguments"]["path"] == "x.py"


def test_first_valid_call_wins():
    text = (
        "Ke hoach: doc file roi sua.\n"
        '```json\n{"name": "list_dir", "arguments": {"path": "."}}\n```\n'
        '```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```'
    )
    calls = parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "list_dir"
