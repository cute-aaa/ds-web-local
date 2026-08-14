"""MCP 服务器（契约 C）：把聚合工具暴露为标准 MCP server，供本地 agent 接入。"""
import asyncio
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp import types

from core.logger import get_logger
from mcp_services.manager import get_manager, split_tool_name
from tools.registry import get_registry

logger = get_logger("mcp.server")


async def _list_tools(ctx, params):
    """聚合：内置工具 + MCP 工具。"""
    registry = get_registry()
    manager = get_manager()
    tools = []
    for t in registry.list_builtin_tools():
        tools.append(types.Tool(name=t["name"], description=t["description"], input_schema=t["input_schema"]))
    for full_name, info in manager.tools.items():
        tool = info["tool"]
        tools.append(types.Tool(
            name=full_name,
            description=tool.get("description", ""),
            input_schema=tool.get("input_schema") or {"type": "object", "properties": {}},
        ))
    return types.ListToolsResult(tools=tools)


async def _call_tool(ctx, params):
    name = params.name
    args = params.arguments or {}
    registry = get_registry()
    manager = get_manager()
    try:
        if registry.is_builtin(name):
            result = await registry.call_builtin(name, args)
        else:
            parsed = split_tool_name(name)
            if parsed is None:
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=f"未知工具: {name}")], is_error=True)
            server, raw = parsed  # mcp__server__tool（兼容旧格式 mcp.server.tool）
            result = await manager.call_tool(raw, server, args)
        text = result if isinstance(result, str) else (str(result))
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])
    except Exception as e:
        logger.warning(f"工具 {name} 执行失败: {e}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"工具执行失败: {e}")], is_error=True)


async def serve_stdio():
    """以 stdio 方式对外暴露聚合工具（供 Hermes / Claude Desktop 等接入）。"""
    server = Server(
        name="ds-web-local",
        version="3.0.0",
        title="DS Web Local 工具聚合中心",
        description="聚合内置工具 + 已接入 MCP 工具，供本地 agent 复用",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    asyncio.run(serve_stdio())


if __name__ == "__main__":
    main()
