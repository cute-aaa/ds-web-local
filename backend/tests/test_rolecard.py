import pytest

from tools.register import register_all_builtin_tools
from rolecard.generator import generate_role_card, generate_tools_json


def test_rolecard_contains_tools():
    register_all_builtin_tools()
    rc = generate_role_card()
    assert "write_file" in rc
    assert "start:" in rc  # 输出格式说明
    assert "end" in rc


def test_tools_json_structure():
    register_all_builtin_tools()
    data = generate_tools_json()
    assert "tools" in data
    names = [t["name"] for t in data["tools"]]
    assert "read_file" in names
    assert all(t["source"] in ("builtin", "mcp") for t in data["tools"])
