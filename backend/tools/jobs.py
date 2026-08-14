"""后台任务 JobManager：长命令等耗时操作放入 asyncio 后台任务运行。"""
import asyncio
import time
import uuid
from typing import Any, Callable, Coroutine, Dict, List, Optional

from core.logger import get_logger

logger = get_logger("tools.jobs")


class JobManager:
    """后台任务管理器（进程内单例）：start/list/output/kill。"""

    def __init__(self) -> None:
        self._jobs: Dict[str, Dict] = {}

    async def start(self, name: str, coro_fn: Callable[..., Coroutine],
                    *args: Any, **kwargs: Any) -> str:
        """启动一个后台协程任务，返回 job_id。"""
        job_id = uuid.uuid4().hex[:12]
        record = {
            "job_id": job_id,
            "name": name,
            "status": "running",  # running | done | failed | cancelled
            "created_at": time.time(),
            "error": None,
            "output": None,
            "task": None,
        }
        record["task"] = asyncio.create_task(self._run(job_id, coro_fn, *args, **kwargs))
        self._jobs[job_id] = record
        # 让任务至少被调度一次：若在首次调度前 cancel，协程体不会执行，
        # _run 里的 CancelledError 处理（置为 cancelled）将永远不触发。
        await asyncio.sleep(0)
        logger.info(f"后台任务启动: {job_id} name={name}")
        return job_id

    async def _run(self, job_id: str, coro_fn: Callable[..., Coroutine],
                   *args: Any, **kwargs: Any) -> None:
        record = self._jobs.get(job_id)
        if record is None:
            return
        try:
            result = await coro_fn(*args, **kwargs)
            record["output"] = result
            record["status"] = "done"
        except asyncio.CancelledError:
            record["status"] = "cancelled"
            record["error"] = "任务已被取消"
        except Exception as e:
            record["status"] = "failed"
            record["error"] = str(e)
            logger.exception(f"后台任务失败: {job_id} name={record.get('name')} err={e}")

    def list(self) -> List[Dict]:
        """任务列表（不含 output，避免超大结果）。"""
        return [
            {"job_id": r["job_id"], "name": r["name"], "status": r["status"],
             "created_at": r["created_at"], "error": r["error"]}
            for r in self._jobs.values()
        ]

    def output(self, job_id: str) -> Dict:
        """查询单个任务状态 + 结果。"""
        r = self._jobs.get(job_id)
        if r is None:
            return {"error": f"任务不存在: {job_id}"}
        return {"job_id": job_id, "name": r["name"], "status": r["status"],
                "output": r.get("output"), "error": r.get("error")}

    async def kill(self, job_id: str) -> Dict:
        """取消任务（cancel 底层 asyncio.Task）。"""
        r = self._jobs.get(job_id)
        if r is None:
            return {"error": f"任务不存在: {job_id}"}
        task = r.get("task")
        if task is not None and not task.done():
            task.cancel()
            return {"job_id": job_id, "status": "cancelling", "note": "取消信号已发送"}
        return {"job_id": job_id, "status": r["status"], "note": "任务已结束，无需取消"}


# ---- 内置工具处理器 ----

async def job_list() -> Dict:
    """列出全部后台任务。"""
    return {"jobs": get_job_manager().list()}


async def job_output(job_id: str) -> Dict:
    """查询后台任务状态与结果。"""
    return get_job_manager().output(job_id)


async def job_kill(job_id: str) -> Dict:
    """取消后台任务。"""
    return await get_job_manager().kill(job_id)


_job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager()
    return _job_manager
