"""后台任务 JobManager 测试：start/list/output/kill + 失败状态。"""
import asyncio

import pytest

from tools import jobs
from tools.jobs import JobManager, get_job_manager


@pytest.fixture
def fresh_jobs(monkeypatch):
    """每个测试用全新的 JobManager 单例，避免跨测试残留。"""
    monkeypatch.setattr(jobs, "_job_manager", None)
    yield
    monkeypatch.setattr(jobs, "_job_manager", None)


async def _wait_status(mgr, job_id, statuses, tries=100):
    """轮询直到任务脱离 running（或 tries 耗尽），返回最新 output。"""
    out = None
    for _ in range(tries):
        out = mgr.output(job_id)
        if out["status"] not in statuses:
            return out
        await asyncio.sleep(0.01)
    return out


async def sample_coro(arg):
    await asyncio.sleep(0.01)
    return f"done:{arg}"


async def test_job_start_list_output(fresh_jobs):
    mgr = get_job_manager()
    job_id = await mgr.start("sample", sample_coro, "hello")
    assert isinstance(job_id, str) and job_id

    listed = mgr.list()
    assert any(j["job_id"] == job_id and j["name"] == "sample" and j["status"] == "running" for j in listed)

    out = await _wait_status(mgr, job_id, ["running"])
    assert out["status"] == "done"
    assert out["output"] == "done:hello"
    assert out["error"] is None


async def test_job_failed(fresh_jobs):
    async def boom():
        raise ValueError("boom-error")

    mgr = get_job_manager()
    job_id = await mgr.start("fail", boom)
    out = await _wait_status(mgr, job_id, ["running"])
    assert out["status"] == "failed"
    assert out["error"] == "boom-error"


async def test_job_kill(fresh_jobs):
    async def long_running():
        await asyncio.sleep(30)
        return "never-returns"

    mgr = get_job_manager()
    job_id = await mgr.start("long", long_running)
    result = await mgr.kill(job_id)
    assert result["status"] == "cancelling"

    out = await _wait_status(mgr, job_id, ["running", "cancelling"])
    assert out["status"] == "cancelled"
    assert out["output"] is None


async def test_job_output_missing(fresh_jobs):
    mgr = get_job_manager()
    out = mgr.output("nope")
    assert "error" in out


async def test_job_kill_missing(fresh_jobs):
    mgr = get_job_manager()
    result = await mgr.kill("nope")
    assert "error" in result


async def test_job_kill_finished(fresh_jobs):
    mgr = get_job_manager()
    job_id = await mgr.start("sample", sample_coro, "x")
    await _wait_status(mgr, job_id, ["running"])
    await asyncio.sleep(0.05)  # 确保底层 task 已结束
    result = await mgr.kill(job_id)  # 已结束：不发送取消信号
    assert result["status"] in ("done", "cancelling")


async def test_tool_handlers(fresh_jobs):
    """模块级工具函数（注册进 registry 的 handler）走通。"""
    mgr = get_job_manager()
    job_id = await mgr.start("sample", sample_coro, "x")
    lst = await jobs.job_list()
    assert any(j["job_id"] == job_id for j in lst["jobs"])
    out = await jobs.job_output(job_id)
    assert out["status"] in ("running", "done", "failed", "cancelled")
    kill = await jobs.job_kill(job_id)
    assert "status" in kill


# 供 registry 注册的 handler 形状：jobs.job_* 是 async 无参/单参函数，这里直接验证可用
async def test_job_manager_class_independent(monkeypatch):
    """直接实例化 JobManager（不依赖单例）。"""
    mgr = JobManager()
    job_id = await mgr.start("direct", sample_coro, "z")
    out = await _wait_status(mgr, job_id, ["running"])
    assert out["output"] == "done:z"
