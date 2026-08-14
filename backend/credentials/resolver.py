"""env 引用形态解析：{"set": 字面量} / {"ref": 凭据引用}。

MCP 服务配置的 env 支持三种写法：
- {"set": "字面量"}：直接使用字面量（不落盘、不查凭据库）；
- {"ref": "ENV_NAME"}：运行时从 CredentialStore 解析（文件 + 环境变量叠加），
  未配置返回 None，由调用方决定跳过该变量；
- 其他值（普通字符串等）：原样透传，兼容旧配置直接写值。
"""
from typing import Any, Optional

from credentials.store import CredentialStore, get_store


def resolve_env_spec(spec: Any, store: Optional[CredentialStore] = None) -> Optional[str]:
    """解析单个 env 值为字符串；无法解析/未配置 → None。

    - {"set": "字面量"} → 返回字面量（str 强转）
    - {"ref": "ENV_NAME"} → store.resolve() 取 value；未配置 → None
    - dict 但无 set/ref 键 → None（无法解析）
    - 其他形态 → 原样返回（普通字符串透传）
    """
    if isinstance(spec, dict):
        if "set" in spec:
            return str(spec["set"])
        if "ref" in spec:
            store = store or get_store()
            cv = store.resolve(str(spec["ref"]))
            if cv is None:
                return None
            return str(cv.value)
        return None
    return spec
