"""凭据管理：.credentials.yaml 文件层 + 进程环境变量遮蔽层。

- 配置里只存引用不存值（{"ref": "ENV_NAME"} / {"set": "字面量"}）；
- 值统一由 CredentialStore 按操作解析（不缓存）；
- describe 只暴露状态元数据，永不泄漏值。
"""
from credentials.store import CredentialStore, CredentialValue, get_store
from credentials.resolver import resolve_env_spec

__all__ = ["CredentialStore", "CredentialValue", "get_store", "resolve_env_spec"]
