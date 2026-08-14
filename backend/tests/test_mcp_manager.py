"""MCPManager 单元测试：命名规范化 / 世代机制 / 重连状态机 / 热重载 / 分发。"""
import asyncio
import hashlib
import time

import pytest

from mcp_services.manager import (
    MCPManager,
    STATE_DISABLED,
    STATE_FAILED,
    STATE_RECONNECTING,
    STATE_RUNNING,
    normalize_tool_name,
    split_tool_name,
)


class FakeClient:
    """可控假客户端：记录调用次数与时间戳，按脚本失败/成功。"""

    def __init__(self, name="s1", config=None, fail_until=0):
        self.name = name
        self.config = config or {}
        self.connected = False
        self.connect_calls = 0
        self.connect_times = []
        self._fail_until = fail_until  # 前 N 次 connect 失败
        self._tools = [{"name": "echo", "description": "回显", "input_schema": {"type": "object"}}]

    async def connect(self):
        self.connect_calls += 1
        self.connect_times.append(time.monotonic())
        if self.connect_calls <= self._fail_until:
            raise RuntimeError("connect boom")
        self.connected = True

    async def list_tools(self):
        return [dict(t) for t in self._tools]

    async def call_tool(self, name, arguments=None, **kw):
        return f"{self.name}:{name}"

    async def close(self):
        self.connected = False


def _make_manager(name="s1", **cfg):
    m = MCPManager()
    base = {"transport": "stdio", "command": "echo", "args": []}
    base.update(cfg)
    base.setdefault("reconnect", {"initial_delay_ms": 5, "max_delay_ms": 20, "max_attempts": 3})
    m.register(name, base)
    return m, base


# ---- R1 命名规范化 ----

def test_normalize_basic():
    assert normalize_tool_name("echo", "echo") == "mcp__echo__echo"
    assert normalize_tool_name("srv", "tool") == "mcp__srv__tool"
    assert normalize_tool_name("srv", "a") == "mcp__srv__a"


def test_normalize_long_truncates_with_hash():
    server = "s" * 40
    raw = "r" * 40
    n = normalize_tool_name(server, raw)
    assert len(n) <= 64
    assert n.startswith("mcp__")
    # 截断改变了名称 → 追加确定性 12 位 hash
    digest = hashlib.md5(f"{server}.{raw}".encode()).hexdigest()[:12]
    assert n.endswith("_" + digest)
    assert len(n) == 64


def test_normalize_special_chars_replaced_with_hash():
    n = normalize_tool_name("my server", "do-thing!x")
    # 仅含 [A-Za-z0-9_-]
    assert set(n) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    digest = hashlib.md5("my server.do-thing!x".encode()).hexdigest()[:12]
    assert n.endswith("_" + digest)
    assert "my_server" in n and "do-thing_x" in n  # "-" 合法保留，仅 "!" 被替换


def test_normalize_pure_function():
    # 相同输入两次调用结果一致（连接顺序 / 重连不影响）
    assert normalize_tool_name("srv", "t!") == normalize_tool_name("srv", "t!")
    a = normalize_tool_name("x" * 40, "y" * 40)
    b = normalize_tool_name("x" * 40, "y" * 40)
    assert a == b


def test_split_tool_name():
    assert split_tool_name("mcp__srv__tool") == ("srv", "tool")
    assert split_tool_name("mcp__srv__a__b") == ("srv", "a__b")  # raw 可含 __
    assert split_tool_name("mcp.srv.tool") == ("srv", "tool")    # 旧格式兼容
    assert split_tool_name("mcp__srv__") is None
    assert split_tool_name("read_file") is None
    assert split_tool_name("mcp") is None


# ---- R2 世代机制 ----

@pytest.mark.asyncio
async def test_generation_start_and_reconnect_replace():
    m, _ = _make_manager("s1")
    fake = FakeClient("s1", m.clients["s1"].config)
    m.clients["s1"] = fake
    assert await m.start("s1")
    assert m.status["s1"] == STATE_RUNNING
    assert m._generations["s1"] == 1
    names = [k for k in m.tools if m.tools[k]["server"] == "s1"]
    assert names == ["mcp__s1__echo"]
    assert m.tools[names[0]]["generation"] == 1
    # 掉线 → 重连成功 → 新世代整代替换旧世代（无重复 / 无泄漏）
    fake.connected = False
    m.status["s1"] = STATE_RECONNECTING
    assert await m._try_reconnect("s1")
    assert m.status["s1"] == STATE_RUNNING
    assert m._generations["s1"] == 2
    names2 = [k for k in m.tools if m.tools[k]["server"] == "s1"]
    assert names2 == ["mcp__s1__echo"]
    assert m.tools[names2[0]]["generation"] == 2
    assert len(m.tools) == 1  # 无重复
    # 两个 server 并存互不干扰
    m.register("s2", {"transport": "stdio", "command": "echo2"})
    fake2 = FakeClient("s2", m.clients["s2"].config)
    m.clients["s2"] = fake2
    assert await m.start("s2")
    assert sorted(k for k in m.tools) == ["mcp__s1__echo", "mcp__s2__echo"]


@pytest.mark.asyncio
async def test_generation_resync_failure_rolls_back():
    m, _ = _make_manager("s1")
    fake = FakeClient("s1", m.clients["s1"].config)
    m.clients["s1"] = fake
    assert await m.start("s1")
    gen1 = m._generations["s1"]
    # list_tools 抛异常 → 重同步失败 → 不产生部分残留，旧世代保留
    async def boom():
        raise RuntimeError("list boom")
    fake.list_tools = boom
    ok = await m._sync_server_tools("s1")
    assert ok is False
    assert m._generations["s1"] == gen1  # 世代未推进
    assert [k for k in m.tools if m.tools[k]["server"] == "s1"] == ["mcp__s1__echo"]
    assert m.tools["mcp__s1__echo"]["generation"] == gen1


@pytest.mark.asyncio
async def test_tool_list_changed_resync():
    m, _ = _make_manager("s1")
    fake = FakeClient("s1", m.clients["s1"].config)
    m.clients["s1"] = fake
    assert await m.start("s1")
    gen1 = m._generations["s1"]
    # 工具列表变化 → 摘要不同 → 整代重同步
    fake._tools = [
        {"name": "echo", "description": "回显", "input_schema": {"type": "object"}},
        {"name": "add", "description": "加法", "input_schema": {"type": "object"}},
    ]
    await m._check_tool_changes("s1")
    names = sorted(k for k in m.tools if m.tools[k]["server"] == "s1")
    assert names == ["mcp__s1__add", "mcp__s1__echo"]
    assert m._generations["s1"] == gen1 + 1
    assert all(m.tools[n]["generation"] == gen1 + 1 for n in names)
    # 摘要未变 → 不重同步
    await m._check_tool_changes("s1")
    assert m._generations["s1"] == gen1 + 1


# ---- R3 重连状态机 ----

def test_backoff_delay_exponential_capped():
    cfg = {"initial_delay_ms": 500, "max_delay_ms": 30000}
    assert MCPManager._backoff_delay(1, cfg) == 500
    assert MCPManager._backoff_delay(2, cfg) == 1000
    assert MCPManager._backoff_delay(6, cfg) == 16000
    assert MCPManager._backoff_delay(7, cfg) == 30000  # 封顶 max_delay_ms
    assert MCPManager._backoff_delay(10, cfg) == 30000


@pytest.mark.asyncio
async def test_reconnect_backoff_and_recovery():
    cfg = {"transport": "stdio", "command": "echo",
           "reconnect": {"initial_delay_ms": 10, "max_delay_ms": 40, "max_attempts": 10}}
    m = MCPManager()
    m.register("s1", cfg)
    fake = FakeClient("s1", cfg, fail_until=3)  # 前 3 次 connect 失败
    m.clients["s1"] = fake
    m._running = True
    assert await m.start("s1") is False  # 首次启动失败
    assert m.status["s1"] == STATE_RECONNECTING
    # 等待重连 worker 指数退避后成功（10/20/40ms）
    for _ in range(200):
        if m.status.get("s1") == STATE_RUNNING:
            break
        await asyncio.sleep(0.05)
    assert m.status["s1"] == STATE_RUNNING
    assert fake.connected
    assert fake.connect_calls == 4  # start 1 次 + worker 3 次
    # 退避延迟单调递增（宽松下界，避免 CI 抖动）
    t = fake.connect_times
    assert t[1] - t[0] >= 0.009   # 第 1 次重试前 sleep 10ms
    assert t[2] - t[1] >= 0.019   # 第 2 次重试前 sleep 20ms
    assert t[3] - t[2] >= 0.039   # 第 3 次重试前 sleep 40ms
    assert m._retry["s1"]["attempts"] == 0  # 成功后预算重置


@pytest.mark.asyncio
async def test_reconnect_exhausts_to_disabled_and_unregisters():
    cfg = {"transport": "stdio", "command": "echo",
           "reconnect": {"initial_delay_ms": 5, "max_delay_ms": 10, "max_attempts": 3}}
    m = MCPManager()
    m.register("s1", cfg)
    fake = FakeClient("s1", cfg)
    m.clients["s1"] = fake
    m._running = True
    # 先成功一次，注册工具
    assert await m.start("s1")
    assert [k for k in m.tools if m.tools[k]["server"] == "s1"] == ["mcp__s1__echo"]
    # 掉线 → 重连全部失败 → disabled + 工具注销
    fake.connected = False
    fake._fail_until = 999
    m.status["s1"] = STATE_RECONNECTING
    m._schedule_reconnect("s1")
    for _ in range(200):
        if m.status.get("s1") == STATE_DISABLED:
            break
        await asyncio.sleep(0.05)
    assert m.status["s1"] == STATE_DISABLED
    assert [k for k in m.tools if m.tools[k]["server"] == "s1"] == []
    assert m._generations.get("s1") is None


@pytest.mark.asyncio
async def test_fail_on_startup_error():
    cfg = {"transport": "stdio", "command": "echo", "failOnStartupError": True}
    m = MCPManager()
    m.register("s1", cfg)
    m.clients["s1"] = FakeClient("s1", cfg, fail_until=999)
    m._running = True
    assert await m.start("s1") is False
    assert m.status["s1"] == STATE_FAILED
    assert not m._reconnect_tasks.get("s1")  # 不进入重连状态机


@pytest.mark.asyncio
async def test_reset_budget_when_healthy():
    cfg = {"transport": "stdio", "command": "echo",
           "reconnect": {"initial_delay_ms": 5, "max_delay_ms": 5000, "max_attempts": 10}}
    m = MCPManager()
    m.register("s1", cfg)
    m.clients["s1"] = FakeClient("s1", cfg)
    assert await m.start("s1")
    r = m._retry["s1"]
    # 存活超过 max_delay → 重置尝试预算
    r["attempts"] = 7
    r["connected_since"] = time.monotonic() - 10
    m._reset_budget_if_healthy("s1")
    assert r["attempts"] == 0
    # 未达存活时长 → 不重置
    r["attempts"] = 5
    r["connected_since"] = time.monotonic()
    m._reset_budget_if_healthy("s1")
    assert r["attempts"] == 5


@pytest.mark.asyncio
async def test_monitor_detects_disconnect_and_reconnects():
    cfg = {"transport": "stdio", "command": "echo",
           "reconnect": {"initial_delay_ms": 5, "max_delay_ms": 10, "max_attempts": 5}}
    m = MCPManager()
    m.register("s1", cfg)
    fake = FakeClient("s1", cfg)
    m.clients["s1"] = fake
    m._running = True
    assert await m.start("s1")
    assert m.status["s1"] == STATE_RUNNING
    # 模拟进程崩溃：连接断开但状态仍是 running
    fake.connected = False
    m._monitor_task = asyncio.create_task(m._monitor_loop(0.01))
    for _ in range(400):
        if m.status.get("s1") == STATE_RUNNING and fake.connected and fake.connect_calls >= 2:
            break
        await asyncio.sleep(0.025)
    assert m.status["s1"] == STATE_RUNNING
    assert fake.connect_calls >= 2
    assert [k for k in m.tools if m.tools[k]["server"] == "s1"] == ["mcp__s1__echo"]
    # 清理
    m._running = False
    if m._monitor_task:
        m._monitor_task.cancel()
    m._cancel_reconnect("s1")


# ---- R4 配置热重载 ----

@pytest.mark.asyncio
async def test_reload_transport_change_restarts():
    m, _ = _make_manager("s1")
    fake = FakeClient("s1", m.clients["s1"].config)
    m.clients["s1"] = fake
    assert await m.start("s1")
    assert fake.connected
    # command 变化 → stop + start（auto_start=true）
    new_cfg = dict(fake.config, command="echo2", auto_start=True)
    assert await m.reload("s1", new_cfg)
    assert m.status["s1"] == STATE_RUNNING
    assert fake.connected
    assert m.clients["s1"].config["command"] == "echo2"


@pytest.mark.asyncio
async def test_reload_same_transport_no_restart():
    m, _ = _make_manager("s1")
    fake = FakeClient("s1", m.clients["s1"].config)
    m.clients["s1"] = fake
    assert await m.start("s1")
    calls_before = fake.connect_calls
    # 非传输字段（toolCallTimeoutMs）→ 仅热更新，不重启
    new_cfg = dict(fake.config, toolCallTimeoutMs=9999)
    assert await m.reload("s1", new_cfg)
    assert fake.connect_calls == calls_before
    assert fake.connected
    assert m.clients["s1"].config["toolCallTimeoutMs"] == 9999


@pytest.mark.asyncio
async def test_reload_auto_start_false_stops():
    m, _ = _make_manager("s1")
    fake = FakeClient("s1", m.clients["s1"].config)
    m.clients["s1"] = fake
    assert await m.start("s1")
    new_cfg = dict(fake.config, command="echo2", auto_start=False)
    await m.reload("s1", new_cfg)
    assert not fake.connected
    assert m.status["s1"] == "stopped"


@pytest.mark.asyncio
async def test_reload_unregistered_registers():
    m = MCPManager()
    ok = await m.reload("new", {"transport": "stdio", "command": "echo", "auto_start": False})
    assert ok
    assert "new" in m.clients


# ---- R5 超时配置 ----

@pytest.mark.asyncio
async def test_call_tool_timeout_config_passthrough():
    cfg = {"transport": "stdio", "command": "echo", "toolCallTimeoutMs": 12345}
    m = MCPManager()
    m.register("s1", cfg)

    class FakeWithKw(FakeClient):
        def __init__(self):
            super().__init__("s1", cfg)
            self.last_kw = None

        async def call_tool(self, name, arguments=None, **kw):
            self.last_kw = kw
            return "ok"

    fake = FakeWithKw()
    m.clients["s1"] = fake
    await m.start("s1")
    r = await m.call_tool("echo", "s1", {"x": 1})
    assert r == "ok"
    assert fake.last_kw.get("timeout_ms") == 12345


@pytest.mark.asyncio
async def test_call_tool_default_timeout():
    cfg = {"transport": "stdio", "command": "echo"}
    m = MCPManager()
    m.register("s1", cfg)

    class FakeWithKw(FakeClient):
        def __init__(self):
            super().__init__("s1", cfg)
            self.last_kw = None

        async def call_tool(self, name, arguments=None, **kw):
            self.last_kw = kw
            return "ok"

    fake = FakeWithKw()
    m.clients["s1"] = fake
    await m.start("s1")
    await m.call_tool("echo", "s1", {})
    assert fake.last_kw.get("timeout_ms") == 60000


# ---- F bridge 分发 ----

@pytest.mark.asyncio
async def test_bridge_dispatch_mcp_names(monkeypatch):
    from api import bridge

    class FakeManager:
        async def call_tool(self, raw, server, arguments):
            return f"{server}:{raw}:{arguments.get('a')}"

    monkeypatch.setattr(bridge, "get_manager", lambda: FakeManager())
    # 新格式 mcp__server__tool
    r = await bridge._dispatch("mcp__srv1__tool_a", {"a": 1})
    assert r == "srv1:tool_a:1"
    # 旧格式 mcp.server.tool 兼容
    r2 = await bridge._dispatch("mcp.srv1.tool_a", {"a": 2})
    assert r2 == "srv1:tool_a:2"


@pytest.mark.asyncio
async def test_bridge_dispatch_mcp_name_with_underscores(monkeypatch):
    from api import bridge

    class FakeManager:
        async def call_tool(self, raw, server, arguments):
            return f"{server}|{raw}"

    monkeypatch.setattr(bridge, "get_manager", lambda: FakeManager())
    # raw 名内含 "__" 也能正确拆分
    r = await bridge._dispatch("mcp__srv__do_thing", {})
    assert r == "srv|do_thing"


# ---- 状态查询 ----

@pytest.mark.asyncio
async def test_get_state_detail():
    m, _ = _make_manager("s1")
    fake = FakeClient("s1", m.clients["s1"].config)
    m.clients["s1"] = fake
    await m.start("s1")
    st = m.get_state("s1")
    assert st["status"] == STATE_RUNNING
    assert st["generation"] == 1
    assert "attempts" in st and "delay_ms" in st
