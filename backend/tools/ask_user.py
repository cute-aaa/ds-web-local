"""ask_user 人类确认工具：发起挂起请求，等待用户经桥接层确认框应答。

归属隔离：
- caller_id（浏览器 profile 级）：由 api.bridge 在 /call 处理期间写入 CALLER_CTX，
  沿 async 调用链自动传播；查询可按 caller_id 过滤；跨 profile 代答返回 403。
- tab_id（标签页级）：同一 profile 可开多个标签、各自是独立会话。挂起记录同时记 tab_id，
  查询/应答都可按 tab_id 过滤——避免 A 标签的页面弹窗去回答 B 标签会话的确认请求
  （跨会话代答会让用户看到"别人的问题"，应答也会填到错误的会话里）。
- 兼容：老客户端不传 caller_id/tab_id 时不过滤、不校验（宽松策略，仅过渡期）。
"""
import asyncio
import contextvars
import time
import uuid
from typing import Any, Dict, List, Optional

from core.logger import get_logger

logger = get_logger("tools.ask_user")

# 当前请求的调用方身份（/call 处理期间由 api.bridge 设置；缺省 "anon"）
CALLER_CTX: contextvars.ContextVar = contextvars.ContextVar("ask_user_caller_id", default="anon")
# 当前请求的标签页标识（同 profile 多标签隔离；缺省 ""=不隔离）
TAB_CTX: contextvars.ContextVar = contextvars.ContextVar("ask_user_tab_id", default="")


class AskUserManager:
    """挂起的人类确认请求管理器（进程内单例）。"""

    def __init__(self) -> None:
        self._pending: Dict[str, Dict] = {}

    async def ask(self, question: str, timeout_sec: int = 300) -> Any:
        """创建挂起请求并等待应答；超时返回 '超时未应答'。"""
        request_id = uuid.uuid4().hex[:12]
        caller_id = CALLER_CTX.get() or "anon"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        tab_id = TAB_CTX.get() or ""
        record = {
            "request_id": request_id,
            "caller_id": caller_id,
            "tab_id": tab_id,
            "question": question,
            "future": future,
            "created_at": time.time(),
        }
        self._pending[request_id] = record
        logger.info(f"ask_user 挂起: {request_id} caller={caller_id} tab={tab_id} question={question[:80]}")
        try:
            return await asyncio.wait_for(future, timeout=timeout_sec)
        except asyncio.TimeoutError:
            if not future.done():
                future.set_result("超时未应答")
            self._pending.pop(request_id, None)
            logger.info(f"ask_user 超时: {request_id}")
            return "超时未应答"

    def get_pending(self, caller_id: Optional[str] = None,
                    tab_id: Optional[str] = None) -> List[Dict]:
        """未应答的挂起请求列表（供桥接层轮询）。

        caller_id 提供时只返回该调用方的；tab_id 提供时进一步只返回同一标签页发起的请求
        —— 同 profile 多标签场景下，各标签只弹自己会话的确认框。
        记录无 tab_id（老客户端发起）时不按 tab 过滤，保持兼容。
        """
        out = []
        for r in self._pending.values():
            if r["future"].done():
                continue
            if caller_id and r.get("caller_id") != caller_id:
                continue
            if tab_id and r.get("tab_id") and r.get("tab_id") != tab_id:
                continue
            out.append({"request_id": r["request_id"], "caller_id": r.get("caller_id"),
                        "tab_id": r.get("tab_id", ""), "question": r["question"],
                        "created_at": r["created_at"]})
        return out

    def answer(self, request_id: str, answer: str,
               caller_id: Optional[str] = None,
               tab_id: Optional[str] = None) -> Dict:
        """用户应答：唤醒挂起的 ask()。

        归属校验：调用方显式声明 caller_id / tab_id 时，必须与挂起记录一致，否则拒绝
        （返回 code=403）——防止 profile A 的页面代答 profile B、或标签 A 代答标签 B 的确认请求。
        """
        record = self._pending.get(request_id)
        if record is None:
            return {"error": f"挂起请求不存在或已过期: {request_id}"}
        if caller_id and record.get("caller_id") != caller_id:
            return {"error": "该确认请求属于其他调用方，不能代答", "code": 403}
        if tab_id and record.get("tab_id") and record.get("tab_id") != tab_id:
            return {"error": "该确认请求由其他标签页发起，不能代答", "code": 403}
        future = record["future"]
        if future.done():
            return {"error": f"请求 {request_id} 已应答或已超时"}
        future.set_result(answer)
        self._pending.pop(request_id, None)
        logger.info(f"ask_user 应答: {request_id} caller={record.get('caller_id')} answer={str(answer)[:80]}")
        return {"request_id": request_id, "answer": answer, "status": "answered"}


async def ask_user(question: str, timeout_sec: int = 300) -> Any:
    """发起人类确认：挂起等待用户在桥接层确认框应答（结果回填给模型）。"""
    if not question or not str(question).strip():
        return {"error": "缺少问题 question"}
    return await get_ask_user_manager().ask(str(question), int(timeout_sec))


def get_pending_asks(caller_id: Optional[str] = None,
                     tab_id: Optional[str] = None) -> List[Dict]:
    """当前挂起的人类确认请求列表（供 API/桥接层轮询）；可按调用方 + 标签页过滤。"""
    return get_ask_user_manager().get_pending(caller_id, tab_id)


def answer_ask(request_id: str, answer: str, caller_id: Optional[str] = None,
               tab_id: Optional[str] = None) -> Dict:
    """回传应答结果（caller_id / tab_id 提供时做归属校验）。"""
    return get_ask_user_manager().answer(request_id, answer, caller_id, tab_id)


_ask_user_manager: Optional[AskUserManager] = None


def get_ask_user_manager() -> AskUserManager:
    global _ask_user_manager
    if _ask_user_manager is None:
        _ask_user_manager = AskUserManager()
    return _ask_user_manager
