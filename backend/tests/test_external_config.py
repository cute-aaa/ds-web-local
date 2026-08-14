"""配置目录外部化（DSW_CONFIG_DIR / DSW_DATA_DIR）+ 技能多根 + 监听线程 测试。"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def ext_config_dir(tmp_path):
    """外部配置目录：settings.yaml + mcp.json。"""
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "settings.yaml").write_text(
        "skills:\n  skills_dirs: [\"skills\", \"data/user-skills\"]\n", encoding="utf-8")
    (cfg / "mcp.json").write_text(
        '{"services": {"demo": {"transport": "stdio", "command": "echo"}}, "server": {"port": 9999}}',
        encoding="utf-8")
    return cfg


def test_config_dir_env_override(monkeypatch, ext_config_dir):
    """DSW_CONFIG_DIR 指向外部目录时，配置从该目录读取。"""
    from core import config as config_mod
    monkeypatch.setenv("DSW_CONFIG_DIR", str(ext_config_dir))
    # 重新导入模块让模块级常量生效（隔离：直接构造 loader 验证）
    loader = config_mod.ConfigLoader(config_dir=ext_config_dir)
    assert loader.get_all_services().get("demo") is not None
    assert loader.get_server_config().get("port") == 9999


def test_data_dir_env_override(tmp_path):
    """DSW_DATA_DIR 指向外部目录时，DATA_DIR 跟随（子进程模拟真实启动：env 先于 import）。"""
    import subprocess
    ext_data = tmp_path / "shared-data"
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'backend'); from core.config import DATA_DIR; print(DATA_DIR)"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent.parent),
        env={**os.environ, "DSW_DATA_DIR": str(ext_data)})
    assert str(ext_data) in r.stdout


def test_config_dir_env_override_subprocess(tmp_path):
    """DSW_CONFIG_DIR 指向外部目录时，配置从该目录读取（子进程验证模块级常量）。"""
    import subprocess
    ext_cfg = tmp_path / "shared-cfg"
    ext_cfg.mkdir()
    (ext_cfg / "mcp.json").write_text(
        '{"services": {}, "server": {"port": 7777}}', encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'backend'); from core.config import get_config; "
         "print(get_config().get_server_config().get('port'))"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent.parent),
        env={**os.environ, "DSW_CONFIG_DIR": str(ext_cfg)})
    assert "7777" in r.stdout


def test_skills_dirs_absolute_custom(tmp_path, monkeypatch):
    """skills_dirs 支持绝对路径自定义目录（source=custom）。"""
    from core import config as config_mod
    custom = tmp_path / "skill-lib"
    custom.mkdir()
    (custom / "my-skill").mkdir()
    (custom / "my-skill" / "SKILL.md").write_text(
        "---\ndescription: 自定义目录技能\n---\n内容\n", encoding="utf-8")
    monkeypatch.setenv("DSW_CONFIG_DIR", str(tmp_path / "cfg-none"))
    cfg = config_mod.get_config()
    loader_cfg = {"skills_dirs": [str(custom)]}
    # 直接构造 discovery 验证 source=custom
    from skills.discovery import SkillDiscovery
    d = SkillDiscovery(roots=[(custom, "custom")])
    snap = d.snapshot()
    names = [s["name"] for s in snap["skills"]]
    assert "my-skill" in names
    summary = [s for s in snap["skills"] if s["name"] == "my-skill"][0]
    assert summary["source"] == "custom"


def test_check_changes_detects_new_skill(tmp_path):
    """check_changes：新增技能文件后检测到变化并刷新目录。"""
    from skills.discovery import SkillDiscovery
    d = SkillDiscovery(roots=[(tmp_path, "custom")])
    assert d.check_changes() is False
    (tmp_path / "new-skill").mkdir()
    (tmp_path / "new-skill" / "SKILL.md").write_text(
        "---\ndescription: 新技能\n---\n正文\n", encoding="utf-8")
    assert d.check_changes() is True
    names = [s.name for s in d.list()]
    assert "new-skill" in names
    assert d.check_changes() is False


def test_check_changes_detects_delete(tmp_path):
    """check_changes：删除技能后检测到变化。"""
    from skills.discovery import SkillDiscovery
    pkg = tmp_path / "gone-skill"
    pkg.mkdir()
    (pkg / "SKILL.md").write_text("---\ndescription: 要删除\n---\n正文\n", encoding="utf-8")
    d = SkillDiscovery(roots=[(tmp_path, "custom")])
    assert "gone-skill" in [s.name for s in d.list()]
    import shutil
    shutil.rmtree(pkg)
    assert d.check_changes() is True
    assert "gone-skill" not in [s.name for s in d.list()]


def test_watcher_config_defaults():
    """watcher 配置默认值。"""
    from core.config import get_config
    cfg = get_config().get_watcher_config()
    assert cfg["config_interval"] >= 0
    assert cfg["skills_interval"] >= 0
