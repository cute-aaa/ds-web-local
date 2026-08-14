"""MCP 客户端：封装官方 mcp SDK，支持 stdio / sse / streamable-http 三种 transport。"""
from typing import Any, Dict, List, Optional

from core.logger import get_logger
from credentials.resolver import resolve_env_spec
from credentials.store import CredentialStore

logger = get_logger("mcp.client")


def _resolve_env(env: Dict[str, Any], store: Optional[CredentialStore] = None) -> Optional[Dict[str, Any]]:
    """解析 env 引用形态：{"ref": ...} / {"set": ...} → 字面值。

    - 引用未配置（resolve 返回 None）→ 跳过该变量并告警；
    - 普通字符串直接透传（兼容旧配置直接写值）。
    """
    out: Dict[str, Any] = {}
    for k, v in env.items():
        if isinstance(v, dict) and ("set" in v or "ref" in v):
            val = resolve_env_spec(v, store=store)
            if val is None:
                logger.warning(f"env[{k}] 引用的凭据未配置，跳过该变量")
                continue
            out[k] = str(val)
        else:
            out[k] = v
    return out or None


def _extract_result(result: Any) -> Any:
    """从 CallToolResult 提取可读结果（优先 structured_content，其次 text）。"""
    if result is None:
        return None
    sc = getattr(result, "structured_content", None)
    if sc is not None:
        return sc
    content = getattr(result, "content", None)
    if not content:
        return None
    texts = []
    for block in content:
        if isinstance(block, dict):
            texts.append(block.get("text", str(block)))
        else:
            t = getattr(block, "text", None)
            texts.append(t if t is not None else str(block))
    if len(texts) == 1:
        return texts[0]
    return texts


class MCPClient:
    """单个 MCP 服务器的客户端会话封装（长连接）。"""

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config
        self.transport = config.get("transport", "stdio")
        self._session = None
        self._transport_cm = None

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters

        if self.transport == "stdio":
            from mcp.client.stdio import stdio_client
            env = self.config.get("env") or None
            if env:
                env = _resolve_env(env)  # 解析 {ref}/{set} 引用形态；解析后为空 → None 保持原行为
            params = StdioServerParameters(
                command=self.config["command"],
                args=self.config.get("args", []),
                env=env,
                cwd=self.config.get("cwd") or None,
            )
            cm = stdio_client(params)
        elif self.transport == "sse":
            from mcp.client.sse import sse_client
            cm = sse_client(self.config["url"])
        elif self.transport in ("streamable-http", "http"):
            from mcp.client.streamable_http import streamablehttp_client
            cm = streamablehttp_client(self.config["url"])
        else:
            raise ValueError(f"不支持的 transport: {self.transport}")

        read, write = await cm.__aenter__()
        self._transport_cm = cm

        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        self._session = session
        logger.info(f"MCP 客户端 {self.name} 已连接（{self.transport}）")

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not self._session:
            raise RuntimeError(f"{self.name} 未连接")
        result = await self._session.list_tools()
        out = []
        for t in result.tools:
            out.append(t.model_dump() if hasattr(t, "model_dump") else dict(t))
        return out

    async def call_tool(self, name: str, arguments: Optional[Dict] = None,
                        retries: int = 2, base_delay: float = 0.5,
                        timeout_ms: int = 60000) -> Any:
        """调用工具，带指数退避重试 + 超时控制（toolCallTimeoutMs，默认 60000ms）。

        超时用 asyncio.wait_for 包裹单次调用，超时后按重试策略重试。
        """
        import asyncio
        if not self._session:
            raise RuntimeError(f"{self.name} 未连接")
        last_err = None
        for attempt in range(retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(name, arguments or {}),
                    timeout=timeout_ms / 1000.0,
                )
                return _extract_result(result)
            except Exception as e:
                last_err = e
                if attempt < retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"{self.name}.{name} 调用失败（第 {attempt + 1} 次），{delay}s 后重试: {e}")
                    await asyncio.sleep(delay)
        raise last_err

    async def close(self) -> None:
        try:
            if self._session is not None:
                await self._session.__aexit__(None, None, None)
                self._session = None
        except Exception as e:
            logger.warning(f"{self.name} 关闭 session 异常: {e}")
        try:
            if self._transport_cm is not None:
                await self._transport_cm.__aexit__(None, None, None)
                self._transport_cm = None
        except Exception as e:
            logger.warning(f"{self.name} 关闭 transport 异常: {e}")
