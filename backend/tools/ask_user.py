"""ask_user 人类确认工具：发起挂起请求，等待用户经桥接层确认框应答。"""
import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional

from core.logger import get_logger

logger = get_logger("tools.ask_user")


class AskUserManager:
    """挂起的人类确认请求管理器（进程内单例）。"""

    def __init__(self) -> None:
        self._pending: Dict[str, Dict] = {}

    async def ask(self, question: str, timeout_sec: int = 300) -> Any:
        """创建挂起请求并等待应答；超时返回 '超时未应答'。"""
        request_id = uuid.uuid4().hex[:12]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        record = {
            "request_id": request_id,
            "question": question,
            "future": future,
            "created_at": time.time(),
        }
        self._pending[request_id] = record
        logger.info(f"ask_user 挂起: {request_id} question={question[:80]}")
        try:
            return await asyncio.wait_for(future, timeout=timeout_sec)
        except asyncio.TimeoutError:
            if not future.done():
                future.set_result("超时未应答")
            self._pending.pop(request_id, None)
            logger.info(f"ask_user 超时: {request_id}")
            return "超时未应答"

    def get_pending(self) -> List[Dict]:
        """未应答的挂起请求列表（供桥接层轮询）。"""
        return [
            {"request_id": r["request_id"], "question": r["question"],
             "created_at": r["created_at"]}
            for r in self._pending.values() if not r["future"].done()
        ]

    def answer(self, request_id: str, answer: str) -> Dict:
        """用户应答：唤醒挂起的 ask()。"""
        record = self._pending.get(request_id)
        if record is None:
            return {"error": f"挂起请求不存在或已过期: {request_id}"}
        future = record["future"]
        if future.done():
            return {"error": f"请求 {request_id} 已应答或已超时"}
        future.set_result(answer)
        self._pending.pop(request_id, None)
        logger.info(f"ask_user 应答: {request_id} answer={str(answer)[:80]}")
        return {"request_id": request_id, "answer": answer, "status": "answered"}


async def ask_user(question: str, timeout_sec: int = 300) -> Any:
    """发起人类确认：挂起等待用户在桥接层确认框应答（结果回填给模型）。"""
    if not question or not str(question).strip():
        return {"error": "缺少问题 question"}
    return await get_ask_user_manager().ask(str(question), int(timeout_sec))


def get_pending_asks() -> List[Dict]:
    """当前挂起的人类确认请求列表（供 API/桥接层轮询）。"""
    return get_ask_user_manager().get_pending()


def answer_ask(request_id: str, answer: str) -> Dict:
    """回传应答结果。"""
    return get_ask_user_manager().answer(request_id, answer)


_ask_user_manager: Optional[AskUserManager] = None


def get_ask_user_manager() -> AskUserManager:
    global _ask_user_manager
    if _ask_user_manager is None:
        _ask_user_manager = AskUserManager()
    return _ask_user_manager
