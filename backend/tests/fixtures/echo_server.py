"""测试用 echo MCP 服务器（stdio）。"""
import asyncio
import sys

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp import types


async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[
        types.Tool(
            name="echo",
            description="回显输入消息",
            input_schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
        ),
        types.Tool(
            name="add",
            description="两数相加",
            input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]},
        ),
    ])


async def call_tool(ctx, params):
    name = params.name
    args = params.arguments or {}
    if name == "echo":
        text = f"echo: {args.get('message', '')}"
    elif name == "add":
        text = str(float(args.get("a", 0)) + float(args.get("b", 0)))
    else:
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"unknown tool: {name}")], is_error=True)
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


async def main():
    server = Server(
        name="echo",
        version="1.0.0",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
