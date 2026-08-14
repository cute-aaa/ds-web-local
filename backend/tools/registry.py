"""统一工具注册表：内置工具 + 技能（MCP 工具由 MCPManager 管理）。"""
import inspect
from typing import Any, Callable, Dict, List, Optional

from core.logger import get_logger

logger = get_logger("tools.registry")


class ApprovalRequired(Exception):
    """工具调用命中审批策略：已创建审批记录，等待用户批准后重试。"""

    def __init__(self, request_id: str, tool: str, arguments: Dict):
        self.request_id = request_id
        self.tool = tool
        self.arguments = arguments
        super().__init__(f"工具 {tool} 需要人工审批 (request_id={request_id})")


async def _maybe_await(value: Any) -> Any:
    """兼容同步/异步钩子与处理器。"""
    if inspect.isawaitable(value):
        return await value
    return value


class ToolRegistry:
    def __init__(self):
        self._builtin: Dict[str, Dict] = {}
        self._skills: Dict[str, Dict] = {}

    # ---- 内置工具 ----
    def register_builtin(self, name: str, handler: Callable, description: str = "",
                         input_schema: Optional[Dict] = None,
                         hooks: Optional[Dict] = None) -> None:
        """注册内置工具。

        hooks: 可选 {"pre": fn(arguments)->arguments, "post": fn(result)->result}，
        支持同步/异步；pre 在 handler 之前执行（可改写 arguments），post 之后执行（可改写 result）。
        """
        self._builtin[name] = {
            "handler": handler,
            "description": description,
            "input_schema": input_schema or {"type": "object", "properties": {}},
            "hooks": hooks or {},
        }

    def is_builtin(self, name: str) -> bool:
        return name in self._builtin

    def _approval_needed(self, name: str) -> bool:
        """审批策略：settings security.approval_enabled + approval_required_tools 命中。"""
        try:
            from core.config import get_config
            security = get_config().get_settings("security", {}) or {}
            if not security.get("approval_enabled", False):
                return False
            required = security.get("approval_required_tools", []) or []
            return name in required
        except Exception:
            return False

    async def call_builtin(self, name: str, arguments: Dict) -> Any:
        entry = self._builtin.get(name)
        if not entry:
            raise KeyError(f"未知内置工具: {name}")
        # 审批挂点：命中审批策略 → 创建审批记录并抛 ApprovalRequired（api 层转 approval_required）
        # 已批准过的 (tool, arguments) 直接放行（前端批准后重试同一调用可执行）
        if self._approval_needed(name):
            from tools.approvals import get_approval_manager
            approval_mgr = get_approval_manager()
            if not approval_mgr.is_approved(name, arguments):
                record = approval_mgr.create(name, arguments)
                raise ApprovalRequired(record["id"], name, arguments)
        # 执行链：pre(arguments) → handler → post(result)
        pre = entry.get("hooks", {}).get("pre")
        if pre:
            arguments = await _maybe_await(pre(arguments)) or arguments
        result = await entry["handler"](**arguments)
        post = entry.get("hooks", {}).get("post")
        if post:
            result = await _maybe_await(post(result))
        return result

    def list_builtin_tools(self) -> List[Dict]:
        return [
            {"name": n, "description": e["description"], "input_schema": e["input_schema"], "source": "builtin"}
            for n, e in self._builtin.items()
        ]

    # ---- 技能 ----
    def register_skill(self, name: str, skill: Dict) -> None:
        self._skills[name] = skill

    def is_skill(self, name: str) -> bool:
        return name in self._skills

    def get_skill(self, name: str) -> Optional[Dict]:
        return self._skills.get(name)

    def list_skills(self) -> List[str]:
        return list(self._skills.keys())

    def clear_skills(self) -> None:
        self._skills.clear()


_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
