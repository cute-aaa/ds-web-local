"""凭据存储：.credentials.yaml（文件层）+ 进程环境变量（遮蔽层）。

dsh 风格设计：
- 配置文件（mcp.json / settings.yaml）里只存引用不存值：
  env 值写成 {"ref": "ENV_NAME"} 或 {"set": "字面量"}，由 resolver 在连接时解析；
- 值存放在 backend/data/.credentials.yaml（chmod 0600，Windows 上尽力而为）；
- 进程环境变量优先：同名环境变量遮蔽文件值；被遮蔽时禁止写入（只读）；
- 所有操作按需读取文件（不缓存），resolve 按操作解析。
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from core.config import BASE_DIR, get_config
from core.logger import get_logger

logger = get_logger("credentials.store")

# 环境变量枚举前缀：env 中形如 DSW_CRED_<REF> 的变量视为凭据引用（list_refs 可枚举）
ENV_PREFIX = "DSW_CRED_"


@dataclass(frozen=True)
class CredentialValue:
    """解析出的凭据值及其来源（env / file）。"""

    value: str
    source: str  # "env" | "file"


class CredentialStore:
    """凭据存取：文件层 + 环境变量遮蔽层。"""

    def __init__(self, file_path: Optional[Path] = None, env_prefix: str = ENV_PREFIX):
        if file_path is None:
            file_path = Path(
                get_config().get_credentials_config().get("file", "data/.credentials.yaml")
            )
        self.file_path = Path(file_path)
        if not self.file_path.is_absolute():
            # 相对路径以 backend/ 为基准（如 "data/.credentials.yaml"）
            self.file_path = BASE_DIR / self.file_path
        self.env_prefix = env_prefix

    # ---- 文件层 ----
    def _load_file(self) -> Dict[str, str]:
        """读取文件层全部条目（每次操作实时读取，不缓存）。"""
        if not self.file_path.exists():
            return {}
        try:
            data = yaml.safe_load(self.file_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning(f"读取凭据文件失败: {e}")
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v is not None}

    def _save_file(self, data: Dict[str, str]) -> None:
        """原子写回凭据文件（临时文件 + os.replace），并尽力 chmod 0600。"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file_path.with_name(self.file_path.name + ".tmp")
        tmp.write_text(
            yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=True),
            encoding="utf-8",
        )
        try:
            os.chmod(tmp, 0o600)  # 先对临时文件设置权限再替换，避免窗口期
        except OSError:
            pass  # Windows 上尽力而为
        os.replace(tmp, self.file_path)
        try:
            os.chmod(self.file_path, 0o600)
        except OSError:
            pass

    # ---- 环境变量层 ----
    def _env_value(self, ref: str) -> Optional[str]:
        """取环境变量值：直接同名优先，其次带前缀形式（DSW_CRED_<REF>）。"""
        if ref in os.environ:
            return os.environ[ref]
        prefixed = f"{self.env_prefix}{ref}"
        if prefixed in os.environ:
            return os.environ[prefixed]
        return None

    def _env_refs(self) -> Dict[str, str]:
        """枚举可识别的环境变量引用（按需）：带前缀 DSW_CRED_<REF> 的变量。"""
        refs: Dict[str, str] = {}
        for name, value in os.environ.items():
            if name.startswith(self.env_prefix):
                refs[name[len(self.env_prefix):]] = value
        return refs

    # ---- 核心操作 ----
    def resolve(self, ref: str) -> Optional[CredentialValue]:
        """按操作解析凭据值：环境变量优先（遮蔽文件），无缓存。

        返回 CredentialValue{value, source}；文件与环境变量均无 → None。
        """
        env_val = self._env_value(ref)
        if env_val is not None:
            return CredentialValue(value=env_val, source="env")
        file_data = self._load_file()
        if ref in file_data:
            return CredentialValue(value=file_data[ref], source="file")
        return None

    def describe(self, ref: str) -> Dict[str, Any]:
        """描述凭据状态：{configured, source, writable}。永不返回值。"""
        if self._env_value(ref) is not None:
            # 环境变量遮蔽 → 只读
            return {"configured": True, "source": "env", "writable": False}
        file_data = self._load_file()
        if ref in file_data:
            return {"configured": True, "source": "file", "writable": True}
        return {"configured": False, "source": None, "writable": True}

    def set(self, ref: str, value: str) -> bool:
        """写入文件层；被环境变量遮蔽时拒绝并返回 False。"""
        if self._env_value(ref) is not None:
            logger.warning(f"凭据 {ref} 被环境变量遮蔽（只读），拒绝写入")
            return False
        data = self._load_file()
        data[ref] = str(value)
        self._save_file(data)
        return True

    def unset(self, ref: str) -> None:
        """删除文件条目；无条目时 no-op。"""
        data = self._load_file()
        if ref in data:
            del data[ref]
            self._save_file(data)

    def list_refs(self) -> List[str]:
        """全部引用：文件键 + 可枚举的环境变量引用（DSW_CRED_* 前缀）。"""
        refs = set(self._load_file().keys())
        refs.update(self._env_refs().keys())
        return sorted(refs)


_store: Optional[CredentialStore] = None


def get_store() -> CredentialStore:
    """全局单例（基于配置构建）；测试中可自行构造 CredentialStore 注入。"""
    global _store
    if _store is None:
        _store = CredentialStore()
    return _store
