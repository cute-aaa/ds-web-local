"""类型化配置加载：settings.yaml + mcp.json + skills.json，支持热重载。"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/


def _env_path(name: str, default: Path) -> Path:
    """环境变量指定目录（支持 ~ 展开），未设置时用默认值。"""
    v = os.environ.get(name)
    if v:
        return Path(v).expanduser()
    return default


# 配置/数据目录可外部化：DSW_CONFIG_DIR / DSW_DATA_DIR 环境变量指定
# （可指向网盘/共享盘/Git 仓库，实现多机共用同一套配置与技能数据）
CONFIG_DIR = _env_path("DSW_CONFIG_DIR", BASE_DIR / "config")
DATA_DIR = _env_path("DSW_DATA_DIR", BASE_DIR / "data")
LOG_DIR = BASE_DIR / "logs"

# 环境变量插值完整模式：${VAR} 或 ${VAR:-default}（仅整串匹配才替换）
_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}$")


class ConfigLoader:
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = Path(config_dir) if config_dir else CONFIG_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._settings: Dict[str, Any] = {}
        self._services: Dict[str, Any] = {}
        self._server: Dict[str, Any] = {}
        self._skills: Dict[str, Any] = {}
        self._mtime: Dict[str, float] = {}
        self._load_all()

    def _interpolate_env(self, value: Any) -> Any:
        """递归替换字符串值中的 ${VAR} / ${VAR:-default}（仅整串匹配）。"""
        if isinstance(value, dict):
            return {k: self._interpolate_env(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._interpolate_env(v) for v in value]
        if isinstance(value, str):
            m = _ENV_PATTERN.match(value)
            if m:
                name, default = m.group(1), m.group(2)
                if name in os.environ:
                    return os.environ[name]
                if default is not None:
                    # 有缺省值（可为空串），缺省本身也支持再插值
                    return self._interpolate_env(default)
                # 变量未设置且无缺省：保留原文
                return value
        return value

    def _load_yaml(self, name: str) -> Dict:
        p = self.config_dir / name
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return self._interpolate_env(data)
        return {}

    def _load_json(self, name: str) -> Dict:
        p = self.config_dir / name
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        return {}

    def _load_all(self):
        self._settings = self._load_yaml("settings.yaml")
        mcp_data = self._load_json("mcp.json")
        self._services = mcp_data.get("services", {})
        self._server = mcp_data.get("server", {})
        self._skills = self._load_json("skills.json")
        for name in ["settings.yaml", "mcp.json", "skills.json"]:
            p = self.config_dir / name
            self._mtime[name] = p.stat().st_mtime if p.exists() else 0

    def check_reload(self) -> bool:
        """配置文件 mtime 变化则重载，返回是否重载。"""
        changed = False
        for name in ["settings.yaml", "mcp.json", "skills.json"]:
            p = self.config_dir / name
            mt = p.stat().st_mtime if p.exists() else 0
            if mt != self._mtime.get(name, 0):
                changed = True
                break
        if changed:
            self._load_all()
        return changed

    # ---- getters ----
    def get_settings(self, key: Optional[str] = None, default=None):
        if key is None:
            return self._settings
        if "." in key:
            cur = self._settings
            for part in key.split("."):
                if isinstance(cur, dict) and part in cur:
                    cur = cur[part]
                else:
                    return default
            return cur
        return self._settings.get(key, default)

    def get_all_services(self) -> Dict:
        return self._services

    def get_service_config(self, name: str) -> Optional[Dict]:
        return self._services.get(name)

    def get_server_config(self) -> Dict:
        return self._server

    def get_all_skills(self) -> Dict:
        return self._skills

    def get_skill(self, name: str) -> Optional[Dict]:
        return self._skills.get(name)

    def get_skills_config(self) -> Dict[str, Any]:
        """技能系统配置（含默认值）：skills_dirs / hermes_import_ro / 描述截断长度。"""
        defaults: Dict[str, Any] = {
            "enabled": True,
            "storage": "config/skills.json",
            "skills_dirs": ["skills", "data/user-skills"],
            "hermes_import_ro": False,
            "hermes_import_dir": "D:/Hermes/skills",
            "catalog_description_max_length": 500,
            "watch_interval": 5,  # 技能目录监听轮询间隔（秒）；0=关闭监听
        }
        cfg = self._settings.get("skills", {})
        merged = dict(defaults)
        if isinstance(cfg, dict):
            merged.update(cfg)
        return merged

    def get_watcher_config(self) -> Dict[str, Any]:
        """配置/技能监听线程配置（含默认值）。"""
        defaults: Dict[str, Any] = {
            "config_interval": 5,  # mcp.json/settings.yaml 监听间隔（秒）；0=关闭
            "skills_interval": 5,  # 技能目录监听间隔（秒）；0=关闭
        }
        cfg = self._settings.get("watcher", {})
        merged = dict(defaults)
        if isinstance(cfg, dict):
            merged.update(cfg)
        return merged

    def get_credentials_config(self) -> Dict[str, Any]:
        """凭据存储配置（含默认值）：file 指向 data/.credentials.yaml（相对 backend/）。"""
        defaults: Dict[str, Any] = {"file": "data/.credentials.yaml"}
        cfg = self._settings.get("credentials", {})
        merged = dict(defaults)
        if isinstance(cfg, dict):
            merged.update(cfg)
        return merged

    def get_timeout_for_tool(self, tool_name: str) -> int:
        for svc in self._services.values():
            if tool_name in svc.get("tools", []):
                t = svc.get("timeout")
                if t:
                    return int(t)
        return int(self.get_settings("timeouts.default", 120))

    # ---- 写入（供 API 热更新）----
    def save_services(self, services: Dict) -> None:
        self._services = services
        p = self.config_dir / "mcp.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"services": services, "server": self._server}, f, ensure_ascii=False, indent=2)
        self._mtime["mcp.json"] = p.stat().st_mtime

    def save_skills(self, skills: Dict) -> None:
        self._skills = skills
        p = self.config_dir / "skills.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(skills, f, ensure_ascii=False, indent=2)
        self._mtime["skills.json"] = p.stat().st_mtime


_config: Optional[ConfigLoader] = None


def get_config() -> ConfigLoader:
    global _config
    if _config is None:
        _config = ConfigLoader()
    return _config
