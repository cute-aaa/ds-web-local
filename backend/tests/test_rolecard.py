import os
import time

import pytest

from tools.register import register_all_builtin_tools
from rolecard.generator import (
    generate_role_card,
    generate_tools_json,
    ensure_template_file,
    load_template,
    TOOLS_PLACEHOLDER,
    SKILLS_PLACEHOLDER,
)


def test_rolecard_contains_tools():
    register_all_builtin_tools()
    rc = generate_role_card()
    assert "write_file" in rc
    assert "@@tool" in rc                 # 输出格式说明（行锚定协议）
    assert "@@tool read_file" in rc       # 正确示例
    assert "start:{" not in rc            # 旧协议不得出现在示例中（否则模型会模仿）


def test_tools_json_structure():
    register_all_builtin_tools()
    data = generate_tools_json()
    assert "tools" in data
    names = [t["name"] for t in data["tools"]]
    assert "read_file" in names
    assert all(t["source"] in ("builtin", "mcp") for t in data["tools"])


# ---- 模板 md 化：改提示词只改 md，不动 Python / 不动 JS / 不重新打包 ----

def test_template_is_markdown_file():
    """模板必须外部化为 md 文件（用户可编辑，首次运行自动生成）。"""
    p = ensure_template_file()
    assert p.exists()
    assert p.suffix == ".md"


def test_placeholders_replaced():
    """两处占位符必须被替换成动态内容，不能原样泄漏给模型。"""
    rc = generate_role_card()
    assert TOOLS_PLACEHOLDER not in rc
    assert SKILLS_PLACEHOLDER not in rc
    assert "## 可用工具清单" in rc
    assert "## 可用技能" in rc


def test_braces_need_no_escaping():
    """改用显式占位符替换后，模板里可自由写 { }；旧 .format 方案会留下 {{ 痕迹。"""
    tpl = load_template()
    assert "{{" not in tpl
    assert "}}" not in tpl
    # 模板里的 JSON 示例应保持单花括号，模型才好照抄
    assert '{"path":"C:/Download/data.txt"}' in tpl


def test_template_hot_reload(tmp_path, monkeypatch):
    """改 md 后重新生成应立刻反映改动（按 mtime 重载，无需重启后端）。"""
    import rolecard.generator as g

    tpl = tmp_path / "rolecard.md"
    tpl.write_text("MARK_A\n" + TOOLS_PLACEHOLDER + "\n" + SKILLS_PLACEHOLDER, encoding="utf-8")
    monkeypatch.setattr(g, "_template_file", lambda: tpl)
    assert "MARK_A" in generate_role_card()

    tpl.write_text("MARK_B\n" + TOOLS_PLACEHOLDER + "\n" + SKILLS_PLACEHOLDER, encoding="utf-8")
    # 强制 mtime 变化，避免同秒写入导致缓存命中、测试偶发失败
    future = time.time() + 10
    os.utime(tpl, (future, future))
    assert "MARK_B" in generate_role_card()
