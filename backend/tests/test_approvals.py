"""审批队列测试：ApprovalManager 单元 + /api/approvals API + 桥接层审批流 + ask_user 往返。"""
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import approvals as approvals_api
from api import bridge
from core.config import get_config
from core.errors import register_exception_handlers
from tools import approvals as approvals_mod
from tools import ask_user as ask_user_mod
from tools.register import register_all_builtin_tools
from tools.registry import get_registry

register_all_builtin_tools()  # 与 test_api.py 一致：进程级单例注册（幂等）

LOG_FILE = Path(__file__).parent.parent / "data" / "approvals.log"


def _build_app():
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(approvals_api.router)
    app.include_router(bridge.router)
    return app


@pytest.fixture
def fresh_approvals(monkeypatch):
    monkeypatch.setattr(approvals_mod, "_approval_manager", None)
    yield
    monkeypatch.setattr(approvals_mod, "_approval_manager", None)


@pytest.fixture
def fresh_ask_user(monkeypatch):
    monkeypatch.setattr(ask_user_mod, "_ask_user_manager", None)
    yield
    monkeypatch.setattr(ask_user_mod, "_ask_user_manager", None)


@pytest.fixture
def approval_api_client(fresh_approvals):
    """开启审批策略 + 注册敏感测试工具 + 挂载 approvals/bridge 路由。"""
    # 清空审计文件，避免跨测试累积导致断言 flaky
    if LOG_FILE.exists():
        LOG_FILE.unlink()
    cfg = get_config()
    saved_security = cfg._settings.get("security", {})
    cfg._settings["security"] = {**saved_security, "approval_enabled": True,
                                 "approval_required_tools": ["test_sensitive"]}

    reg = get_registry()

    async def fake_sensitive(**kw):
        return {"ok": True, **kw}

    reg.register_builtin("test_sensitive", fake_sensitive, "敏感测试工具")
    try:
        app = _build_app()
        with TestClient(app) as c:
            yield c
    finally:
        reg._builtin.pop("test_sensitive", None)
        cfg._settings["security"] = saved_security


# ---- ApprovalManager 单元 ----

def test_manager_create_list_approve(fresh_approvals):
    mgr = approvals_mod.get_approval_manager()
    rec = mgr.create("terminal_run", {"command": "ls"})
    assert rec["status"] == "pending"
    assert rec["tool"] == "terminal_run"

    lst = mgr.list()
    assert len(lst) == 1 and lst[0]["id"] == rec["id"]

    out = mgr.approve(rec["id"])
    assert out["status"] == "approved"
    assert out["decided_at"] is not None

    # 重复审批报错
    again = mgr.approve(rec["id"])
    assert "error" in again


def test_manager_reject_and_missing(fresh_approvals):
    mgr = approvals_mod.get_approval_manager()
    rec = mgr.create("ask_user", {"question": "x"})
    out = mgr.reject(rec["id"])
    assert out["status"] == "rejected"
    assert mgr.get("nope") is None


def test_manager_writes_audit_log(fresh_approvals, tmp_path, monkeypatch):
    log_target = tmp_path / "approvals.log"
    monkeypatch.setattr(approvals_mod, "LOG_FILE", log_target)
    mgr = approvals_mod.get_approval_manager()
    rec = mgr.create("terminal_run", {"command": "ls"})
    mgr.approve(rec["id"])
    lines = log_target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # pending + approved
    entry = json.loads(lines[1])
    assert entry["action"] == "approved"
    assert entry["record"]["id"] == rec["id"]


# ---- /api/approvals API ----

def test_approvals_api_list_approve(approval_api_client):
    c = approval_api_client
    r = c.get("/api/approvals")
    assert r.status_code == 200
    assert "approvals" in r.json()
    assert r.json()["approvals"] == []  # 尚未产生审批


def test_approvals_api_unknown_id(approval_api_client):
    c = approval_api_client
    r = c.post("/api/approvals/nope/approve")
    assert "error" in r.json()


# ---- 桥接层审批流 ----

def test_bridge_approval_flow(approval_api_client):
    c = approval_api_client
    # 1) 调用敏感工具 → 不执行，返回 approval_required
    r = c.post("/api/bridge/call", json={"tool": "test_sensitive", "arguments": {"a": 1}})
    body = r.json()
    assert r.status_code == 200
    assert body["results"][0]["approval_required"] is True
    req_id = body["results"][0]["request_id"]
    assert body["results"][0]["tool"] == "test_sensitive"
    assert body["results"][0]["arguments"] == {"a": 1}

    # 2) 审批队列可见（pending）
    lst = c.get("/api/approvals").json()["approvals"]
    assert any(a["id"] == req_id and a["status"] == "pending" and a["tool"] == "test_sensitive"
               for a in lst)

    # 3) 批准
    r = c.post(f"/api/approvals/{req_id}/approve")
    assert r.json()["status"] == "approved"

    # 4) 批准后重试 → 真正执行
    r = c.post("/api/bridge/call", json={"tool": "test_sensitive", "arguments": {"a": 1}})
    assert r.json()["results"][0] == {"ok": True, "a": 1}


def test_bridge_approval_reject(approval_api_client):
    c = approval_api_client
    r = c.post("/api/bridge/call", json={"tool": "test_sensitive", "arguments": {}})
    req_id = r.json()["results"][0]["request_id"]
    r = c.post(f"/api/approvals/{req_id}/reject")
    assert r.json()["status"] == "rejected"
    # 拒绝后重试 → 生成新的审批请求（原记录不可重复审批）
    r = c.post("/api/bridge/call", json={"tool": "test_sensitive", "arguments": {}})
    assert r.json()["results"][0]["approval_required"] is True
    assert r.json()["results"][0]["request_id"] != req_id


def test_bridge_approval_audit_log_file(approval_api_client):
    """审批落盘审计（backend/data/approvals.log）。"""
    c = approval_api_client
    r = c.post("/api/bridge/call", json={"tool": "test_sensitive", "arguments": {}})
    req_id = r.json()["results"][0]["request_id"]
    c.post(f"/api/approvals/{req_id}/approve")
    if LOG_FILE.exists():
        content = LOG_FILE.read_text(encoding="utf-8")
        assert req_id in content


# ---- ask_user 桥接往返 ----

def test_bridge_ask_user_roundtrip(fresh_ask_user, fresh_approvals):
    """后台线程发起 ask_user（挂起）→ 轮询 pending → 回传应答 → 挂起解除。"""
    # 默认 settings.yaml 将 ask_user 列入审批；此处关闭审批，直接测确认框往返
    cfg = get_config()
    saved_security = cfg._settings.get("security", {})
    cfg._settings["security"] = {**saved_security, "approval_enabled": False}
    try:
        app = _build_app()
        with TestClient(app) as c:
            result = {}

            def do_ask():
                try:
                    r = c.post("/api/bridge/call", json={
                        "tool": "ask_user", "arguments": {"question": "继续吗?", "timeout_sec": 30}})
                    result["resp"] = r.json()
                except Exception as e:  # noqa: BLE001
                    result["err"] = str(e)

            t = threading.Thread(target=do_ask)
            t.start()

            # 轮询 pending 出现挂起请求
            pending = []
            deadline = time.time() + 10
            while time.time() < deadline:
                r = c.get("/api/bridge/ask_user/pending")
                assert r.status_code == 200
                pending = r.json()["pending"]
                if pending:
                    break
                time.sleep(0.05)
            assert pending, "未发现挂起请求"
            assert pending[0]["question"] == "继续吗?"

            # 回传应答
            r = c.post("/api/bridge/ask_user", json={
                "request_id": pending[0]["request_id"], "answer": "是"})
            assert r.json()["status"] == "answered"

            t.join(timeout=10)
            assert not t.is_alive(), "ask_user 未解除挂起"
            assert "err" not in result
            assert result["resp"]["results"][0] == "是"

            # 应答后 pending 清空
            r = c.get("/api/bridge/ask_user/pending")
            assert r.json()["pending"] == []
    finally:
        cfg._settings["security"] = saved_security


def test_bridge_ask_user_missing_request_id(fresh_ask_user):
    app = _build_app()
    with TestClient(app) as c:
        r = c.post("/api/bridge/ask_user", json={"answer": "x"})
        assert r.status_code == 400


def test_bridge_ask_user_unknown_request(fresh_ask_user):
    app = _build_app()
    with TestClient(app) as c:
        r = c.post("/api/bridge/ask_user", json={"request_id": "nope", "answer": "x"})
        assert r.status_code == 404


async def test_ask_user_timeout(fresh_ask_user):
    """超时未应答 → 返回 '超时未应答'。"""
    ans = await ask_user_mod.ask_user("测试?", timeout_sec=0)
    assert ans == "超时未应答"
    assert ask_user_mod.get_pending_asks() == []


async def test_ask_user_answer_direct(fresh_ask_user):
    """直接单元：ask() 挂起 → answer() 唤醒。"""
    import asyncio
    mgr = ask_user_mod.get_ask_user_manager()
    question = "是否执行?"

    async def do_ask():
        return await mgr.ask(question, timeout_sec=10)

    task = asyncio.create_task(do_ask())
    await asyncio.sleep(0.05)
    pending = mgr.get_pending()
    assert len(pending) == 1 and pending[0]["question"] == question
    out = mgr.answer(pending[0]["request_id"], "否")
    assert out["status"] == "answered"
    ans = await task
    assert ans == "否"
