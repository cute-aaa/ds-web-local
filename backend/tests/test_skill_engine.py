import asyncio
import yaml
import pytest

from tools.register import register_all_builtin_tools
from skills.engine import SkillEngine
from skills.discovery import SkillDiscovery


@pytest.fixture
def engine(tmp_path):
    """隔离的发现器 + 引擎：bundled 根 + user 根（含纯指令/流水线技能）。"""
    register_all_builtin_tools()
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    bundled.mkdir(parents=True, exist_ok=True)
    user.mkdir(parents=True, exist_ok=True)
    # 纯指令技能（无 steps）
    (user / "howto").mkdir()
    (user / "howto" / "SKILL.md").write_text(
        "---\ndescription: 使用说明\n---\n# 说明\n这是纯指令正文", encoding="utf-8")
    # 流水线技能（frontmatter steps，写文件）
    steps = [{"name": "write_file", "id": "w",
              "arguments": {"path": "$input.path", "content": "$input.content"}}]
    fm = yaml.safe_dump({"description": "写文件技能", "steps": steps},
                        allow_unicode=True, sort_keys=False)
    (user / "writer").mkdir()
    (user / "writer" / "SKILL.md").write_text(f"---\n{fm}---\n\n写文件正文", encoding="utf-8")
    discovery = SkillDiscovery(roots=[(bundled, "bundled"), (user, "user")], legacy={})
    return SkillEngine(discovery=discovery)


def test_resolve_placeholders(engine):
    val = engine._resolve_value({"path": "$input.p", "n": 1}, {"p": "/tmp/x"}, {})
    assert val["path"] == "/tmp/x"
    assert val["n"] == 1


def test_render_template(engine):
    out = engine._render_template("bytes={{w.bytes}} lines={{r.total_lines}}",
                                  {"w": {"bytes": 20}, "r": {"total_lines": 2}})
    assert out == "bytes=20 lines=2"


def test_execute_skill_not_found(engine):
    r = asyncio.run(engine.execute_skill("nonexistent", {}))
    assert "error" in r


def test_execute_instruction_mode(engine):
    """纯指令技能（无 steps）→ instruction 模式，返回正文。"""
    r = asyncio.run(engine.execute_skill("howto", {}))
    assert r["mode"] == "instruction"
    assert r["output"] == "# 说明\n这是纯指令正文"
    assert "results" not in r


def test_execute_steps_pipeline(engine, tmp_path):
    """steps 技能 → 走工具流水线（mode=pipeline）。"""
    target = tmp_path / "out.txt"
    r = asyncio.run(engine.execute_skill("writer", {"path": str(target), "content": "hello"}))
    assert r["mode"] == "pipeline"
    assert target.read_text(encoding="utf-8") == "hello"
    assert "w" in r["results"]


def test_get_and_list_from_discovery(engine):
    names = engine.list_skills()
    assert "howto" in names and "writer" in names
    skill = engine.get_skill("howto")
    assert skill["description"] == "使用说明"
    assert skill["content"] == "# 说明\n这是纯指令正文"
    assert skill["source"] == "user"
