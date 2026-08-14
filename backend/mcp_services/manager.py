"""MCP 服务生命周期管理 + 健康监控（dsh 风格：mcp__ 命名 / 世代机制 / 指数退避重连 / 配置热重载）。"""
import asyncio
import hashlib
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger
from core.config import get_config
from mcp_services.client import MCPClient

logger = get_logger("mcp.manager")

# 状态机对外可见状态
STATE_STOPPED = "stopped"
STATE_CONNECTING = "connecting"
STATE_RUNNING = "running"
STATE_RECONNECTING = "reconnecting"
STATE_FAILED = "failed"
STATE_DISABLED = "disabled"

# 默认重连参数（与 dsh 一致）
DEFAULT_RECONNECT = {
    "initial_delay_ms": 500,
    "max_delay_ms": 30000,
    "max_attempts": 10,
}

# 工具名长度上限
MAX_TOOL_NAME_LEN = 64

# 触发重启的传输相关配置字段（其余字段热更新即可）
RESTART_FIELDS = ("transport", "command", "args", "url", "env", "cwd")


def normalize_tool_name(server_name: str, raw_name: str) -> str:
    """规范化工具名：mcp__{server}__{raw}，总长 ≤64 且仅含 [A-Za-z0-9_-]。

    若因替换非法字符或截断改变了名称，追加确定性 12 位十六进制 hash
    （md5(f"{server}.{raw}")[:12]）。名称是 (server, raw) 的纯函数，
    连接顺序 / 重连次数不影响结果。
    """
    base = f"mcp__{server_name}__{raw_name}"
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", base)
    if sanitized != base or len(sanitized) > MAX_TOOL_NAME_LEN:
        digest = hashlib.md5(f"{server_name}.{raw_name}".encode()).hexdigest()[:12]
        suffix = f"_{digest}"
        return sanitized[: MAX_TOOL_NAME_LEN - len(suffix)] + suffix
    return sanitized


def split_tool_name(tool_name: str) -> Optional[Tuple[str, str]]:
    """解析工具名 → (server, raw)。支持 mcp__ 新格式与 mcp. 旧格式；非 MCP 工具返回 None。

    raw 内可含 "__"（split("__", 2) 只切前两个分隔符）。
    """
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__", 2)
        if len(parts) == 3 and parts[0] == "mcp" and parts[1] and parts[2]:
            return parts[1], parts[2]
        return None
    if tool_name.startswith("mcp."):
        parts = tool_name.split(".", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            return parts[1], parts[2]
    return None


class MCPManager:
    """管理多个 MCP 客户端：世代化工具注册 + 指数退避重连 + 健康监控 + 配置热重载。"""

    def __init__(self):
        self.clients: Dict[str, MCPClient] = {}
        # tool_name(规范化) -> {"server": name, "tool": info, "raw_name": raw, "generation": gen}
        self.tools: Dict[str, Dict] = {}
        self.status: Dict[str, str] = {}
        self._generations: Dict[str, int] = {}   # server -> 当前世代 id
        self._digests: Dict[str, str] = {}       # server -> 工具列表摘要（变化检测）
        self._retry: Dict[str, Dict] = {}        # server -> {attempts, delay, connected_since}
        self._reconnect_tasks: Dict[str, asyncio.Task] = {}
        self._monitor_task = None
        self._running = False

    # ---- 生命周期 ----
    def register(self, name: str, config: Dict) -> None:
        if name not in self.clients:
            self.clients[name] = MCPClient(name, config)
            self.status[name] = STATE_STOPPED
        else:
            # 已存在：仅热更新配置（toolCallTimeoutMs 等非传输字段立即生效）
            self.clients[name].config = config

    async def start(self, name: str) -> bool:
        client = self.clients.get(name)
        if not client:
            logger.error(f"服务 {name} 未注册")
            return False
        if client.connected:
            if self.status.get(name) != STATE_RUNNING:
                self.status[name] = STATE_RUNNING
            return True
        fail_on_startup = bool(client.config.get("failOnStartupError", False))
        self._retry[name] = {"attempts": 0, "delay": 0, "connected_since": None}
        self.status[name] = STATE_CONNECTING
        try:
            await client.connect()
            await self._sync_server_tools(name)
            self.status[name] = STATE_RUNNING
            r = self._retry.setdefault(name, {})
            r["attempts"] = 0
            r["connected_since"] = time.monotonic()
            logger.info(f"服务 {name} 启动成功")
            return True
        except Exception as e:
            logger.error(f"启动服务 {name} 失败: {e}")
            if fail_on_startup:
                self.status[name] = STATE_FAILED
            else:
                # 默认：记录日志并进入重连状态机
                self.status[name] = STATE_RECONNECTING
                self._schedule_reconnect(name)
            return False

    async def stop(self, name: str) -> bool:
        client = self.clients.get(name)
        if not client:
            return False
        self._cancel_reconnect(name)
        await client.close()
        self._remove_server_tools(name)
        self._generations.pop(name, None)
        self._digests.pop(name, None)
        self._retry.pop(name, None)
        self.status[name] = STATE_STOPPED
        return True

    async def restart(self, name: str) -> bool:
        await self.stop(name)
        await asyncio.sleep(1)
        return await self.start(name)

    async def remove(self, name: str) -> bool:
        await self.stop(name)
        if name in self.clients:
            del self.clients[name]
            del self.status[name]
            return True
        return False

    # ---- 世代化工具注册 ----
    async def _sync_server_tools(self, name: str) -> bool:
        """成功 list_tools 后整代原子注册。

        新世代构建完成后一次性整代替换旧世代：失败则回滚（绝不部分残留），
        旧世代保留不变。返回是否同步成功。
        """
        client = self.clients[name]
        try:
            tools = await client.list_tools()
        except Exception as e:
            logger.warning(f"同步 {name} 工具表失败: {e}")
            return False
        gen = self._generations.get(name, 0) + 1
        entries = {}
        for t in tools:
            raw = t["name"]
            full = normalize_tool_name(name, raw)
            entries[full] = {"server": name, "tool": t, "raw_name": raw, "generation": gen}
        # 整代替换：先清空旧世代再写入，避免重复与泄漏
        self._remove_server_tools(name)
        self.tools.update(entries)
        self._generations[name] = gen
        self._digests[name] = self._tool_digest(tools)
        logger.info(f"服务 {name} 世代 {gen} 注册 {len(entries)} 个工具")
        return True

    @staticmethod
    def _tool_digest(tools: List[Dict]) -> str:
        """工具列表摘要：按 (name, description) 排序后 md5，用于变化检测。"""
        sig = sorted((t.get("name", ""), t.get("description", "")) for t in tools)
        return hashlib.md5(repr(sig).encode()).hexdigest()

    async def _check_tool_changes(self, name: str) -> None:
        """工具列表变化检测。

        官方 mcp SDK（2.0.0）ClientSession 未提供 set_list_tools_changed_callback，
        故在 monitor 轮询中对比工具列表摘要：变化时整代重同步。
        """
        client = self.clients.get(name)
        if not client or not client.connected:
            return
        try:
            tools = await client.list_tools()
        except Exception as e:
            logger.warning(f"检查 {name} 工具变化失败: {e}")
            return
        digest = self._tool_digest(tools)
        if self._digests.get(name) != digest:
            logger.info(f"服务 {name} 工具列表变化，整代重同步")
            await self._sync_server_tools(name)

    def _remove_server_tools(self, name: str) -> None:
        for full in list(self.tools.keys()):
            if self.tools[full]["server"] == name:
                del self.tools[full]

    # ---- 调用与查询 ----
    async def call_tool(self, raw_name: str, server: str, arguments: Dict):
        client = self.clients.get(server)
        if not client or not client.connected:
            raise RuntimeError(f"服务 {server} 未运行")
        timeout_ms = int(client.config.get("toolCallTimeoutMs", 60000))
        return await client.call_tool(raw_name, arguments, timeout_ms=timeout_ms)

    def list_server_tools(self, name: str) -> list:
        return [self.tools[f]["tool"] for f in self.tools if self.tools[f]["server"] == name]

    def get_status(self) -> Dict:
        return {name: self.status.get(name, STATE_STOPPED) for name in self.clients}

    def get_state(self, name: str) -> Dict:
        """对外可见的详细状态（含重连尝试次数 / 当前世代）。"""
        r = self._retry.get(name, {})
        return {
            "status": self.status.get(name, STATE_STOPPED),
            "attempts": r.get("attempts", 0),
            "delay_ms": r.get("delay", 0),
            "generation": self._generations.get(name, 0),
        }

    # ---- 重连状态机 ----
    def _reconnect_cfg(self, name: str) -> Dict:
        cfg = self.clients[name].config if name in self.clients else {}
        rc = cfg.get("reconnect") or {}
        return {
            "initial_delay_ms": int(rc.get("initial_delay_ms", DEFAULT_RECONNECT["initial_delay_ms"])),
            "max_delay_ms": int(rc.get("max_delay_ms", DEFAULT_RECONNECT["max_delay_ms"])),
            "max_attempts": int(rc.get("max_attempts", DEFAULT_RECONNECT["max_attempts"])),
        }

    @staticmethod
    def _backoff_delay(attempt: int, cfg: Dict) -> int:
        """指数退避延迟：attempt 从 1 起，delay = initial * 2^(attempt-1)，封顶 max_delay_ms。"""
        return min(int(cfg["initial_delay_ms"]) * (2 ** (attempt - 1)), int(cfg["max_delay_ms"]))

    def _schedule_reconnect(self, name: str) -> None:
        task = self._reconnect_tasks.get(name)
        if task and not task.done():
            return
        self._reconnect_tasks[name] = asyncio.create_task(self._reconnect_worker(name))

    async def _reconnect_worker(self, name: str) -> None:
        cfg = self._reconnect_cfg(name)
        try:
            while self._running and self.status.get(name) == STATE_RECONNECTING:
                r = self._retry.setdefault(name, {"attempts": 0, "delay": 0, "connected_since": None})
                r["attempts"] += 1
                if r["attempts"] > int(cfg["max_attempts"]):
                    logger.error(
                        f"服务 {name} 连续 {r['attempts'] - 1} 次重连失败，"
                        f"达到上限 {cfg['max_attempts']}，已禁用并注销工具")
                    self._disable(name)
                    return
                delay_ms = self._backoff_delay(r["attempts"], cfg)
                r["delay"] = delay_ms
                logger.info(f"服务 {name} 第 {r['attempts']}/{cfg['max_attempts']} 次重连，{delay_ms}ms 后重试")
                await asyncio.sleep(delay_ms / 1000.0)
                if self.status.get(name) != STATE_RECONNECTING:
                    return
                if await self._try_reconnect(name):
                    return
        except asyncio.CancelledError:
            pass
        finally:
            self._reconnect_tasks.pop(name, None)

    async def _try_reconnect(self, name: str) -> bool:
        client = self.clients.get(name)
        if not client:
            return False
        self.status[name] = STATE_CONNECTING
        try:
            await client.connect()
            ok = await self._sync_server_tools(name)
            self.status[name] = STATE_RUNNING
            r = self._retry.setdefault(name, {})
            r["attempts"] = 0
            r["connected_since"] = time.monotonic()
            if not ok:
                logger.warning(f"服务 {name} 已连接但工具同步失败（旧世代保留）")
            logger.info(f"服务 {name} 重连成功，世代 {self._generations.get(name)}")
            return True
        except Exception as e:
            logger.warning(f"服务 {name} 重连失败: {e}")
            self.status[name] = STATE_RECONNECTING
            return False

    def _disable(self, name: str) -> None:
        """连续失败达上限：注销全部工具、停止重连、状态标记 disabled。"""
        self._cancel_reconnect(name)
        self._remove_server_tools(name)
        self._generations.pop(name, None)
        self._digests.pop(name, None)
        self.status[name] = STATE_DISABLED

    def _cancel_reconnect(self, name: str) -> None:
        task = self._reconnect_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()

    def _reset_budget_if_healthy(self, name: str, now: float = None) -> None:
        """连接成功存活超过 max_delay 时长则重置尝试预算（attempts=0）。"""
        r = self._retry.get(name)
        if not r or self.status.get(name) != STATE_RUNNING or not r.get("connected_since"):
            return
        now = now if now is not None else time.monotonic()
        max_delay_ms = self._reconnect_cfg(name)["max_delay_ms"]
        if (now - r["connected_since"]) * 1000 >= max_delay_ms:
            r["attempts"] = 0

    # ---- 配置热重载 ----
    async def reload(self, name: str, new_cfg: Dict) -> bool:
        """配置热重载：对比旧配置，transport/command/url/env 等变化 → stop 后按 auto_start 决定是否 start。

        serverName=name 不变，工具名天然不变（纯函数）。非传输字段仅热更新配置，不重启。
        """
        client = self.clients.get(name)
        if not client:
            self.register(name, new_cfg)
            if new_cfg.get("auto_start"):
                return await self.start(name)
            return True
        old_cfg = client.config
        changed = any(old_cfg.get(k) != new_cfg.get(k) for k in RESTART_FIELDS)
        client.config = dict(new_cfg)
        if not changed:
            logger.info(f"服务 {name} 配置热更新（无传输变化，不重启）")
            return True
        logger.info(f"服务 {name} 传输相关配置变化，重启生效")
        await self.stop(name)
        if new_cfg.get("auto_start", False):
            return await self.start(name)
        return True

    # ---- 自动启动 + 健康监控 ----
    async def start_all_auto(self) -> None:
        config = get_config()
        for name, cfg in config.get_all_services().items():
            self.register(name, cfg)
            if cfg.get("auto_start", False):
                await self.start(name)

    async def start_monitor(self, interval: float = 30) -> None:
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop(interval))

    async def _monitor_loop(self, interval: float) -> None:
        while self._running:
            await asyncio.sleep(interval)
            now = time.monotonic()
            for name, client in list(self.clients.items()):
                st = self.status.get(name)
                if st == STATE_RUNNING:
                    if not client.connected:
                        logger.warning(f"服务 {name} 掉线，进入重连状态机")
                        self.status[name] = STATE_RECONNECTING
                        self._schedule_reconnect(name)
                    else:
                        await self._check_tool_changes(name)
                        self._reset_budget_if_healthy(name, now)
                elif st == STATE_RECONNECTING:
                    # worker 意外退出则重新拉起
                    task = self._reconnect_tasks.get(name)
                    if not task or task.done():
                        self._schedule_reconnect(name)

    async def stop_monitor(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except (asyncio.CancelledError, Exception):
                pass
            self._monitor_task = None
        for name in list(self._reconnect_tasks.keys()):
            task = self._reconnect_tasks.pop(name, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass


_manager: Optional[MCPManager] = None


def get_manager() -> MCPManager:
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
