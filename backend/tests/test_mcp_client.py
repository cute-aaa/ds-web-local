import os
import sys

import pytest

from mcp_services.client import MCPClient

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "echo_server.py")


@pytest.fixture
async def client():
    c = MCPClient("echo", {
        "transport": "stdio",
        "command": sys.executable,
        "args": [FIXTURE],
        "cwd": os.path.dirname(FIXTURE),
    })
    await c.connect()
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_connect_and_list_tools(client):
    tools = await client.list_tools()
    names = [t["name"] for t in tools]
    assert "echo" in names
    assert "add" in names


@pytest.mark.asyncio
async def test_call_echo_chinese(client):
    r = await client.call_tool("echo", {"message": "你好世界"})
    assert r == "echo: 你好世界"


@pytest.mark.asyncio
async def test_call_add(client):
    r = await client.call_tool("add", {"a": 2, "b": 3})
    assert float(r) == 5.0


@pytest.mark.asyncio
async def test_call_with_timeout_ms(client):
    r = await client.call_tool("echo", {"message": "timeout"}, timeout_ms=30000)
    assert r == "echo: timeout"
