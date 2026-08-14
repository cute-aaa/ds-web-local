"""技能执行引擎：占位符解析 + 工具流水线 + 输出模板（从 v2 迁移 + 修复）。

SKILL.md 目录格式下的执行策略：
- 技能有 steps（或 legacy tools）→ 现有工具流水线执行（mode=pipeline）
- 纯指令技能（无 steps）→ 返回 {"output": content, "mode": "instruction"} 供模型读取
"""
import re
from typing import Any, Dict, List, Optional

from core.logger import get_logger
from tools.registry import get_registry
from mcp_services.manager import get_manager, split_tool_name
from skills.discovery import SkillDiscovery, get_discovery

logger = get_logger("skills.engine")


class SkillEngine:
    def __init__(self, discovery: Optional[SkillDiscovery] = None):
        self.registry = get_registry()
        self.manager = get_manager()
        self.discovery = discovery if discovery is not None else get_discovery()

    def get_skill(self, name: str) -> Optional[Dict]:
        d = self.discovery.get(name)
        return d.to_dict() if d else None

    def list_skills(self) -> List[str]:
        return [s.name for s in self.discovery.list()]

    def _resolve_value(self, value: Any, inputs: Dict, context: Dict) -> Any:
        """递归解析 $input.xxx / $context.xxx 占位符。"""
        if isinstance(value, str):
            if value == "$input":
                return inputs
            if value == "$context":
                return context
            m = re.fullmatch(r'\$input\.(.+)', value)
            if m:
                return inputs.get(m.group(1), value)
            m = re.fullmatch(r'\$context\.(.+)', value)
            if m:
                return context.get(m.group(1), value)
            return value
        if isinstance(value, dict):
            return {k: self._resolve_value(v, inputs, context) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_value(v, inputs, context) for v in value]
        return value

    def _render_template(self, template: str, results: Dict) -> str:
        """渲染 {{toolN.field}} 输出模板。"""
        def repl(match):
            expr = match.group(1).strip()
            parts = expr.split(".")
            if len(parts) < 2:
                return match.group(0)
            key = parts[0]
            if key not in results:
                return match.group(0)
            value = results[key]
            for field in parts[1:]:
                if isinstance(value, dict) and field in value:
                    value = value[field]
                elif isinstance(value, list) and field.isdigit() and int(field) < len(value):
                    value = value[int(field)]
                else:
                    return match.group(0)
            return str(value) if value is not None else ""
        return re.sub(r'\{\{\s*([^}]+)\s*\}\}', repl, template)

    async def _dispatch_tool(self, tool_name: str, arguments: Dict) -> Any:
        if self.registry.is_builtin(tool_name):
            return await self.registry.call_builtin(tool_name, arguments)
        parsed = split_tool_name(tool_name)
        if parsed is not None:
            # mcp__server__tool（兼容旧格式 mcp.server.tool）
            server, raw = parsed
            return await self.manager.call_tool(raw, server, arguments)
        return {"error": f"未知工具: {tool_name}"}

    async def execute_skill(self, skill_name: str, inputs: Dict) -> Dict:
        """执行技能：有 steps 走工具流水线；纯指令返回正文。"""
        skill = self.discovery.get(skill_name)
        if not skill:
            return {"error": f"技能 '{skill_name}' 不存在"}

        steps = skill.steps or skill.tools
        if not steps:
            # 纯指令技能：无工具步骤，返回正文供模型读取
            return {"skill": skill_name, "output": skill.content, "mode": "instruction"}

        context: Dict = {}
        results: Dict = {}
        errors: List = []
        for i, tool_def in enumerate(steps):
            tool_name = tool_def.get("name")
            if not tool_name:
                errors.append({"step": i, "error": "缺少工具名"})
                continue
            resolved = self._resolve_value(tool_def.get("arguments", {}), inputs, context)
            try:
                result = await self._dispatch_tool(tool_name, resolved)
                key = tool_def.get("id", f"tool{i}")
                results[key] = result
                if isinstance(result, dict) and "error" in result:
                    errors.append({"step": i, "tool": tool_name, "error": result["error"]})
            except Exception as e:
                errors.append({"step": i, "tool": tool_name, "error": str(e)})

        template = skill.output_template
        output = self._render_template(template, results) if template else results
        return {
            "skill": skill_name,
            "output": output,
            "results": results,
            "errors": errors,
            "mode": "pipeline",
        }


_engine: Optional[SkillEngine] = None


def get_skill_engine() -> SkillEngine:
    global _engine
    if _engine is None:
        _engine = SkillEngine()
    return _engine
