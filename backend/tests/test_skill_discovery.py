"""技能发现单元测试：frontmatter 解析 / kebab-case 过滤 / 层级优先级 / digest 稳定性 / legacy 合并。"""
import json
from pathlib import Path

import pytest

from skills.discovery import (
    SkillDiscovery, SKILL_NAME_RE, build_skill_md, parse_skill_file,
)


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _make_discovery(tmp_path, bundled=None, user=None, legacy=None, hermes=None):
    roots = []
    if bundled is not None:
        bundled.mkdir(parents=True, exist_ok=True)
        roots.append((bundled, "bundled"))
    if user is not None:
        user.mkdir(parents=True, exist_ok=True)
        roots.append((user, "user"))
    if hermes is not None:
        hermes.mkdir(parents=True, exist_ok=True)
        roots.append((hermes, "hermes"))
    return SkillDiscovery(roots=roots, legacy=legacy or {})


# ---------- frontmatter 解析 ----------

def test_frontmatter_parsing_defaults(tmp_path):
    bundled = tmp_path / "bundled"
    _write(bundled, "hello/SKILL.md", "---\ndescription: 打招呼技能\n---\n# 正文内容\n第二行")
    d = _make_discovery(tmp_path, bundled=bundled)
    defn = d.get("hello")
    assert defn is not None
    assert defn.description == "打招呼技能"
    assert defn.when_to_use == ""
    assert defn.invocation == {"model": True, "user": True}  # 默认均可调用
    assert defn.source == "bundled"
    assert defn.content == "# 正文内容\n第二行"
    assert defn.steps is None


def test_frontmatter_full_fields(tmp_path):
    bundled = tmp_path / "bundled"
    _write(bundled, "parse-log/SKILL.md",
           "---\ndescription: 解析日志\nwhen_to_use: 分析 log 文件时\nmodel-invocable: false\n---\n正文")
    d = _make_discovery(tmp_path, bundled=bundled)
    s = d.get_summary("parse-log")
    assert s.when_to_use == "分析 log 文件时"
    assert s.invocation == {"model": False, "user": True}


def test_steps_from_frontmatter_and_body(tmp_path):
    bundled = tmp_path / "bundled"
    # frontmatter steps
    _write(bundled, "fm-steps/SKILL.md",
           "---\ndescription: 前置步骤\nsteps:\n  - name: read_file\n    arguments:\n      path: a.txt\n---\n正文")
    # 正文 ```steps``` yaml 代码块
    _write(bundled, "body-steps/SKILL.md",
           "---\ndescription: 正文步骤\n---\n# 说明\n\n```steps\n- name: write_file\n  arguments:\n    path: b.txt\n```\n")
    d = _make_discovery(tmp_path, bundled=bundled)
    assert d.get("fm-steps").steps == [{"name": "read_file", "arguments": {"path": "a.txt"}}]
    assert d.get("body-steps").steps == [{"name": "write_file", "arguments": {"path": "b.txt"}}]


def test_missing_description_skipped(tmp_path):
    bundled = tmp_path / "bundled"
    _write(bundled, "nodesc/SKILL.md", "---\nwhen_to_use: x\n---\n没有描述")
    _write(bundled, "broken/SKILL.md", "---\ndescription: [未闭合\n---\n坏 frontmatter")
    _write(bundled, "plain.md", "没有 frontmatter 的文件")
    d = _make_discovery(tmp_path, bundled=bundled)
    names = [s.name for s in d.list()]
    assert "nodesc" not in names
    assert "broken" not in names
    assert "plain" not in names


def test_kebab_case_filter(tmp_path):
    bundled = tmp_path / "bundled"
    _write(bundled, "good-skill/SKILL.md", "---\ndescription: 合规\n---\nok")
    _write(bundled, "Bad_Name/SKILL.md", "---\ndescription: 大写/下划线不合规\n---\nno")
    _write(bundled, "UPPER/SKILL.md", "---\ndescription: 大写不合规\n---\nno")
    _write(bundled, "README.md", "# 说明文件")  # 非 kebab-case 扁平文件
    _write(bundled, "a--b/SKILL.md", "---\ndescription: 连续连字符不合规\n---\nno")
    d = _make_discovery(tmp_path, bundled=bundled)
    names = [s.name for s in d.list()]
    assert names == ["good-skill"]
    assert SKILL_NAME_RE.match("good-skill")
    assert not SKILL_NAME_RE.match("Bad_Name")
    assert not SKILL_NAME_RE.match("a--b")


def test_flat_file_shape(tmp_path):
    user = tmp_path / "user"
    _write(user, "flat-one.md", "---\ndescription: 扁平文件技能\n---\n扁平正文")
    d = _make_discovery(tmp_path, user=user)
    defn = d.get("flat-one")
    assert defn is not None
    assert defn.description == "扁平文件技能"
    assert defn.content == "扁平正文"
    assert defn.source == "user"


# ---------- 层级优先级 / 合并 ----------

def test_hierarchy_priority_bundled_over_user(tmp_path):
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write(bundled, "dup/SKILL.md", "---\ndescription: 内置版\n---\nbundled")
    _write(user, "dup/SKILL.md", "---\ndescription: 用户版\n---\nuser")
    _write(user, "only-user/SKILL.md", "---\ndescription: 仅用户\n---\nu")
    d = _make_discovery(tmp_path, bundled=bundled, user=user)
    assert d.get("dup").source == "bundled"  # 根目录优先级：bundled > user
    assert d.get("dup").content == "bundled"
    assert d.get("only-user").source == "user"


def test_fs_over_legacy(tmp_path):
    user = tmp_path / "user"
    _write(user, "both/SKILL.md", "---\ndescription: fs 版\n---\nfs-content")
    legacy = {
        "both": {"description": "legacy 版", "prompt": "legacy-content"},
        "legacy-only": {"description": "老技能", "prompt": "老正文",
                        "tools": [{"name": "read_file", "arguments": {"path": "x"}}]},
    }
    d = _make_discovery(tmp_path, user=user, legacy=legacy)
    # fs 优先于 legacy json
    assert d.get("both").source == "user"
    assert d.get("both").content == "fs-content"
    # legacy 字段映射：prompt→content、tools→steps
    lo = d.get("legacy-only")
    assert lo.source == "legacy"
    assert lo.content == "老正文"
    assert lo.steps == [{"name": "read_file", "arguments": {"path": "x"}}]
    assert lo.tools == lo.steps  # 保留 tools 列表


# ---------- 快照 / digest / 变更检测 ----------

def test_snapshot_structure(tmp_path):
    user = tmp_path / "user"
    _write(user, "alpha/SKILL.md", "---\ndescription: A\n---\na")
    d = _make_discovery(tmp_path, user=user)
    snap = d.snapshot()
    assert set(snap.keys()) == {"skills", "complete", "digest"}
    assert snap["complete"] is True
    assert len(snap["skills"]) == 1
    assert snap["skills"][0]["name"] == "alpha"
    assert snap["skills"][0]["source"] == "user"
    assert len(snap["digest"]) == 64


def test_digest_stability(tmp_path):
    user = tmp_path / "user"
    _write(user, "alpha/SKILL.md", "---\ndescription: A\n---\na")
    _write(user, "beta/SKILL.md", "---\ndescription: B\n---\nb")
    d1 = _make_discovery(tmp_path, user=user)
    d2 = _make_discovery(tmp_path, user=user)
    assert d1.get_digest() == d2.get_digest()  # 相同内容 → 相同 digest
    digest_before = d1.get_digest()
    # 内容变化 → digest 变化（轮询感知）
    _write(user, "alpha/SKILL.md", "---\ndescription: A2\n---\na2")
    assert d1.get_digest() != digest_before
    assert d2.get_digest() == d1.get_digest()  # 双方轮询后一致
    # 名称排序不影响 digest
    _write(user, "alpha/SKILL.md", "---\ndescription: B\n---\nb")  # alpha 与 beta 内容互换
    d3 = _make_discovery(tmp_path, user=user)
    assert d1.get_digest() == d3.get_digest()


def test_change_detection_polling(tmp_path):
    user = tmp_path / "user"
    d = _make_discovery(tmp_path, user=user)
    assert d.list() == []
    # 目录外新增技能文件 → 下次 snapshot() 自动重新扫描
    _write(user, "new-skill/SKILL.md", "---\ndescription: 新增\n---\nn")
    assert [s.name for s in d.list()] == ["new-skill"]
    # 删除后同样感知
    import shutil
    shutil.rmtree(user / "new-skill")
    assert d.list() == []


def test_save_and_delete_user_root(tmp_path):
    user = tmp_path / "user"
    d = _make_discovery(tmp_path, user=user)
    defn = d.save("my-skill", {"description": "我的技能", "content": "正文",
                               "when_to_use": "需要时", "steps": [{"name": "read_file",
                                                                  "arguments": {"path": "x"}}]})
    assert defn.source == "user"
    assert defn.content == "正文"
    assert defn.steps == [{"name": "read_file", "arguments": {"path": "x"}}]
    assert (user / "my-skill" / "SKILL.md").exists()
    # 文件可被再次解析（roundtrip）
    d2 = _make_discovery(tmp_path, user=user)
    r = d2.get("my-skill")
    assert r.description == "我的技能"
    assert r.when_to_use == "需要时"
    assert r.steps == defn.steps
    # 删除仅限 user 源
    assert d.delete("my-skill") is True
    assert d.get("my-skill") is None
    assert d.delete("my-skill") is False


def test_bundled_and_legacy_readonly(tmp_path):
    bundled = tmp_path / "bundled"
    _write(bundled, "ro-skill/SKILL.md", "---\ndescription: 只读内置\n---\nr")
    legacy = {"ro-legacy": {"description": "只读 legacy", "prompt": "p"}}
    d = _make_discovery(tmp_path, bundled=bundled, legacy=legacy)
    assert d.delete("ro-skill") is False
    assert d.delete("ro-legacy") is False
    assert d.get("ro-skill") is not None
    assert d.get("ro-legacy") is not None


# ---------- 导入 ----------

def test_import_from_hermes_with_resources(tmp_path):
    hermes = tmp_path / "hermes"
    user = tmp_path / "user"
    # 分类目录布局：hermes/devops/cc-switch/SKILL.md + 资源文件
    _write(hermes, "devops/cc-switch/SKILL.md", "---\ndescription: CC 切换\n---\n正文")
    _write(hermes, "devops/cc-switch/assets/note.txt", "资源")
    d = _make_discovery(tmp_path, hermes=hermes, user=user)
    assert [s.name for s in d.list()] == ["cc-switch"]  # 分类目录下探一层发现
    defn = d.import_skill("hermes", "cc-switch")
    assert defn is not None
    assert defn.source == "user"
    assert (user / "cc-switch" / "SKILL.md").exists()
    assert (user / "cc-switch" / "assets" / "note.txt").exists()  # 深拷贝资源
    assert d.get("cc-switch").source == "user"
    # 不存在的技能
    assert d.import_skill("hermes", "nonexistent") is None


def test_import_flat_file(tmp_path):
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write(bundled, "flat-import.md", "---\ndescription: 扁平导入\n---\n内容")
    d = _make_discovery(tmp_path, bundled=bundled, user=user)
    assert d.import_skill("bundled", "flat-import") is not None
    assert (user / "flat-import.md").exists()


# ---------- 序列化 roundtrip ----------

def test_build_skill_md_roundtrip(tmp_path):
    skill = {
        "description": "描述 <&>",
        "when_to_use": "场景",
        "model-invocable": False,
        "content": "正文行1\n正文行2",
        "steps": [{"name": "read_file", "arguments": {"path": "/tmp/x"}}],
        "output_template": "{{r.total_lines}}",
    }
    md = build_skill_md(skill)
    p = tmp_path / "rt" / "SKILL.md"
    p.parent.mkdir(parents=True)
    p.write_text(md, encoding="utf-8")
    defn = parse_skill_file(p, "rt", "user")
    assert defn.description == "描述 <&>"
    assert defn.when_to_use == "场景"
    assert defn.invocation == {"model": False, "user": True}
    assert defn.content == "正文行1\n正文行2"
    assert defn.steps == skill["steps"]
    assert defn.output_template == "{{r.total_lines}}"


def test_legacy_prompt_field_maps_to_content(tmp_path):
    """build_skill_md 兼容旧字段 prompt/tools。"""
    md = build_skill_md({"description": "旧字段", "prompt": "旧正文",
                         "tools": [{"name": "write_file"}]})
    p = tmp_path / "old" / "SKILL.md"
    p.parent.mkdir(parents=True)
    p.write_text(md, encoding="utf-8")
    defn = parse_skill_file(p, "old", "user")
    assert defn.content == "旧正文"
    assert defn.steps == [{"name": "write_file"}]
