"""凭据存储 / 引用解析 / MCP env 注入 / API 测试（tmp_path 隔离，不污染真实 data/）。"""
import json
import os
import sys

import pytest

from credentials.store import CredentialStore
from credentials.resolver import resolve_env_spec


@pytest.fixture
def store(tmp_path):
    return CredentialStore(file_path=tmp_path / ".credentials.yaml")


# ---- resolve：env 优先 / 文件值 / 缺失 ----
def test_resolve_env_priority(store, monkeypatch):
    """环境变量优先于文件（遮蔽）。"""
    assert store.set("DSW_TEST_TOKEN", "file-value")
    monkeypatch.setenv("DSW_TEST_TOKEN", "env-value")
    cv = store.resolve("DSW_TEST_TOKEN")
    assert cv is not None
    assert cv.value == "env-value"
    assert cv.source == "env"


def test_resolve_env_prefixed(store, monkeypatch):
    """DSW_CRED_<REF> 前缀环境变量可解析。"""
    monkeypatch.setenv("DSW_CRED_MY_KEY", "abc")
    cv = store.resolve("MY_KEY")
    assert cv.value == "abc"
    assert cv.source == "env"


def test_resolve_file_value(store, monkeypatch):
    monkeypatch.delenv("DSW_TEST_TOKEN", raising=False)
    assert store.set("DSW_TEST_TOKEN", "file-value")
    cv = store.resolve("DSW_TEST_TOKEN")
    assert cv.value == "file-value"
    assert cv.source == "file"


def test_resolve_missing(store):
    assert store.resolve("DSW_NO_SUCH_REF") is None


# ---- describe：永不泄漏值 ----
def test_describe_never_leaks_value(store, monkeypatch):
    store.set("DSW_TEST_A", "value-a")
    monkeypatch.setenv("DSW_TEST_B", "value-b")
    assert store.describe("DSW_TEST_A") == {"configured": True, "source": "file", "writable": True}
    assert store.describe("DSW_TEST_B") == {"configured": True, "source": "env", "writable": False}
    assert store.describe("DSW_TEST_C") == {"configured": False, "source": None, "writable": True}
    # 返回值里不能出现任何实际值
    for ref in ("DSW_TEST_A", "DSW_TEST_B", "DSW_TEST_C"):
        dumped = json.dumps(store.describe(ref), ensure_ascii=False)
        assert "value-a" not in dumped
        assert "value-b" not in dumped


def test_describe_env_shadowed_file(store, monkeypatch):
    """文件与 env 同时存在 → 显示 env 且只读。"""
    store.set("DSW_TEST_K", "file-v")
    monkeypatch.setenv("DSW_TEST_K", "env-v")
    assert store.describe("DSW_TEST_K") == {"configured": True, "source": "env", "writable": False}


# ---- set/unset 生命周期 ----
def test_set_unset_lifecycle(store, monkeypatch):
    monkeypatch.delenv("DSW_TEST_TOKEN", raising=False)
    assert store.resolve("DSW_TEST_TOKEN") is None
    assert store.set("DSW_TEST_TOKEN", "v1") is True
    assert store.resolve("DSW_TEST_TOKEN").value == "v1"
    assert store.set("DSW_TEST_TOKEN", "v2") is True
    assert store.resolve("DSW_TEST_TOKEN").value == "v2"  # 覆盖写
    store.unset("DSW_TEST_TOKEN")
    assert store.resolve("DSW_TEST_TOKEN") is None
    store.unset("DSW_TEST_TOKEN")  # 无条目 no-op
    assert store.resolve("DSW_TEST_TOKEN") is None


def test_set_rejected_when_env_shadowed(store, monkeypatch):
    """被环境变量遮蔽时 set 拒绝（只读）。"""
    monkeypatch.setenv("DSW_TEST_TOKEN", "env-value")
    assert store.set("DSW_TEST_TOKEN", "file-value") is False
    cv = store.resolve("DSW_TEST_TOKEN")
    assert cv.value == "env-value"
    assert cv.source == "env"  # 文件未被写入


def test_unset_keeps_env_shadow(store, monkeypatch):
    """unset 只删文件条目，环境变量遮蔽仍在。"""
    store.set("DSW_TEST_K", "file-v")
    monkeypatch.setenv("DSW_TEST_K", "env-v")
    store.unset("DSW_TEST_K")
    cv = store.resolve("DSW_TEST_K")
    assert cv.value == "env-v"
    assert cv.source == "env"


# ---- 文件格式 / 权限 ----
def test_file_content_and_mode(tmp_path):
    import yaml
    s = CredentialStore(file_path=tmp_path / ".credentials.yaml")
    s.set("K1", "v1")
    s.set("K2", "你好")
    data = yaml.safe_load((tmp_path / ".credentials.yaml").read_text(encoding="utf-8"))
    assert data == {"K1": "v1", "K2": "你好"}
    if os.name != "nt":  # Windows 上 chmod 0600 尽力而为
        assert os.stat(s.file_path).st_mode & 0o777 == 0o600


def test_list_refs(store, monkeypatch):
    store.set("DSW_TEST_FILE_REF", "v")
    monkeypatch.setenv("DSW_CRED_ENV_REF", "e")
    refs = store.list_refs()
    assert "DSW_TEST_FILE_REF" in refs
    assert "ENV_REF" in refs


# ---- resolver：{"set"} / {"ref"} / 普通字符串 ----
def test_resolver_set_ref_plain(store, monkeypatch):
    assert resolve_env_spec({"set": "literal"}) == "literal"
    assert resolve_env_spec({"set": 42}) == "42"  # 字面量强转 str
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_xxx")
    assert resolve_env_spec({"ref": "GITHUB_TOKEN"}, store=store) == "ghp_xxx"
    assert resolve_env_spec({"ref": "DSW_NO_SUCH_REF"}, store=store) is None  # 未配置
    assert resolve_env_spec("plain-value") == "plain-value"  # 普通字符串透传
    assert resolve_env_spec({"unknown": 1}) is None  # dict 但无 set/ref


# ---- MCP client env 解析 ----
def test_client_resolve_env_unit(store, monkeypatch):
    from mcp_services.client import _resolve_env
    monkeypatch.setenv("DSW_TEST_TOKEN", "env-token")
    out = _resolve_env({
        "TOKEN": {"ref": "DSW_TEST_TOKEN"},
        "LIT": {"set": "lit"},
        "PLAIN": "plain",
        "MISSING": {"ref": "DSW_NO_SUCH_REF"},
    }, store=store)
    assert out == {"TOKEN": "env-token", "LIT": "lit", "PLAIN": "plain"}


def test_client_resolve_env_all_unresolved(store):
    from mcp_services.client import _resolve_env
    out = _resolve_env({"A": {"ref": "DSW_NO_SUCH_REF"}}, store=store)
    assert out is None  # 全部未配置 → None（保持 env 缺省行为）


_ENV_SERVER = '''
import asyncio, os
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp import types

async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool(
        name="env", description="读取环境变量",
        input_schema={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]})])

async def call_tool(ctx, params):
    key = (params.arguments or {}).get("key", "")
    return types.CallToolResult(content=[types.TextContent(type="text", text=os.environ.get(key, ""))])

async def main():
    server = Server(name="env-server", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
'''


@pytest.mark.asyncio
async def test_connect_injects_resolved_env(tmp_path, monkeypatch):
    """端到端：connect() 将 {ref}/{set} 解析为字面值并注入子进程 env。"""
    from mcp_services.client import MCPClient
    server_py = tmp_path / "env_server.py"
    server_py.write_text(_ENV_SERVER, encoding="utf-8")
    monkeypatch.setenv("DSW_TEST_TOKEN", "tok-123")
    c = MCPClient("envsrv", {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(server_py)],
        "env": {
            "MY_TOKEN": {"ref": "DSW_TEST_TOKEN"},
            "LIT": {"set": "lit-val"},
            "PLAIN": "plain-val",
        },
    })
    try:
        await c.connect()
        assert await c.call_tool("env", {"key": "MY_TOKEN"}) == "tok-123"
        assert await c.call_tool("env", {"key": "LIT"}) == "lit-val"
        assert await c.call_tool("env", {"key": "PLAIN"}) == "plain-val"
    finally:
        await c.close()


# ---- API ----
@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from main import create_app
    from tools.register import register_all_builtin_tools
    register_all_builtin_tools()  # 手动注册（绕过 lifespan）
    store = CredentialStore(file_path=tmp_path / ".credentials.yaml")
    monkeypatch.setattr("api.credentials.get_store", lambda: store)
    app = create_app()
    with TestClient(app) as c:
        c.cred_store = store
        yield c


def test_api_list_set_delete(api_client):
    r = api_client.get("/api/credentials")
    assert r.status_code == 200
    assert "refs" in r.json()
    # set
    r = api_client.put("/api/credentials/DSW_API_TOKEN", json={"value": "s3cret-value"})
    assert r.status_code == 200
    assert r.json()["configured"] is True and r.json()["source"] == "file"
    # list：只含状态，不含值
    r = api_client.get("/api/credentials")
    item = next(x for x in r.json()["refs"] if x["ref"] == "DSW_API_TOKEN")
    assert item == {"ref": "DSW_API_TOKEN", "configured": True, "source": "file", "writable": True}
    assert "s3cret-value" not in r.text
    # delete
    r = api_client.delete("/api/credentials/DSW_API_TOKEN")
    assert r.status_code == 200
    assert api_client.cred_store.resolve("DSW_API_TOKEN") is None


def test_api_put_shadowed_409(api_client, monkeypatch):
    monkeypatch.setenv("DSW_API_SHADOWED", "env-v")
    r = api_client.put("/api/credentials/DSW_API_SHADOWED", json={"value": "x"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


def test_api_describe_env_never_value(api_client, monkeypatch):
    monkeypatch.setenv("DSW_CRED_ENV_ONLY", "env-secret-42")
    r = api_client.get("/api/credentials")
    item = next(x for x in r.json()["refs"] if x["ref"] == "ENV_ONLY")
    assert item == {"ref": "ENV_ONLY", "configured": True, "source": "env", "writable": False}
    assert "env-secret-42" not in r.text


# ---- config ----
def test_get_credentials_config(tmp_path):
    from core.config import ConfigLoader
    c = ConfigLoader(tmp_path)
    assert c.get_credentials_config() == {"file": "data/.credentials.yaml"}
    (tmp_path / "settings.yaml").write_text("credentials:\n  file: custom.yaml\n", encoding="utf-8")
    c2 = ConfigLoader(tmp_path)
    assert c2.get_credentials_config() == {"file": "custom.yaml"}
