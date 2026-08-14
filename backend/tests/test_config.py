import json
import yaml

from core.config import ConfigLoader


def test_config_load(tmp_path):
    (tmp_path / "settings.yaml").write_text("timeouts:\n  default: 300\n", encoding="utf-8")
    (tmp_path / "mcp.json").write_text(json.dumps({"services": {"s1": {"transport": "stdio"}}}), encoding="utf-8")
    (tmp_path / "skills.json").write_text(json.dumps({"k1": {"description": "d"}}), encoding="utf-8")

    c = ConfigLoader(tmp_path)
    assert c.get_settings("timeouts.default") == 300
    assert "s1" in c.get_all_services()
    assert "k1" in c.get_all_skills()


def test_config_dotted_get(tmp_path):
    (tmp_path / "settings.yaml").write_text("a:\n  b:\n    c: 42\n", encoding="utf-8")
    c = ConfigLoader(tmp_path)
    assert c.get_settings("a.b.c") == 42
    assert c.get_settings("a.b.missing", "default") == "default"


def test_config_save_services(tmp_path):
    c = ConfigLoader(tmp_path)
    c.save_services({"new": {"transport": "stdio"}})
    data = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert "new" in data["services"]


def test_get_timeout_for_tool(tmp_path):
    (tmp_path / "mcp.json").write_text(json.dumps({
        "services": {"s": {"tools": ["t1"], "timeout": 55}}
    }), encoding="utf-8")
    (tmp_path / "settings.yaml").write_text("timeouts:\n  default: 120\n", encoding="utf-8")
    c = ConfigLoader(tmp_path)
    assert c.get_timeout_for_tool("t1") == 55
    assert c.get_timeout_for_tool("unknown") == 120
