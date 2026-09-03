import asyncio
import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from main import create_app
from tools.register import register_all_builtin_tools

register_all_builtin_tools()  # 手动注册（绕过 lifespan）


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root(client):
    r = client.get("/")
    assert r.json()["version"] == "3.0.0"


def test_bridge_tools(client):
    r = client.get("/api/bridge/tools")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()["tools"]]
    assert "read_file" in names


def test_bridge_rolecard(client):
    r = client.get("/api/bridge/rolecard")
    assert "write_file" in r.json()["rolecard"]


def test_bridge_call_builtin(client, tmp_path):
    """契约（信封）：/call 返回逐调用全量信封；成功 status=ok。"""
    f = tmp_path / "t.txt"
    r = client.post("/api/bridge/call", json={"tool": "write_file", "arguments": {"path": str(f), "content": "hi"}})
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["status"] == "ok"
    # 信封必需字段
    for key in ("request_id", "tool", "caller_id", "arguments", "error",
                "approval_required", "externalized", "elapsed_ms"):
        assert key in res, key
    assert res["request_id"].startswith("r_")
    assert res["arguments"] == {"path": str(f), "content": "hi"}   # 原始参数回显
    assert res["error"] is None
    assert res["file"] and Path(res["file"]).exists()              # 默认每次落盘附件


def test_bridge_call_unknown_tool(client):
    """未知工具：语义性失败收敛为信封 status=error（HTTP 200，不再 500/404）。"""
    r = client.post("/api/bridge/call", json={"tool": "nonexistent", "arguments": {}})
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["status"] == "error"
    assert res["error"]["type"] and res["error"]["message"]


def test_bridge_call_empty_success_envelope(client):
    """成功调用（即使内容为空）也必须有完整信封，禁止 HTTP 500 / 无返回导致流程停滞。"""
    r = client.post("/api/bridge/call", json={"tool": "read_todos", "arguments": {}})
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["status"] == "ok"
    assert res["request_id"].startswith("r_")
    assert res["error"] is None
    assert res["file"] and Path(res["file"]).exists()


def test_bridge_call_batch_mixed(client, tmp_path):
    """批量一成一败：len(results)==2 且状态各自正确、顺序对应。"""
    f = tmp_path / "t2.txt"
    r = client.post("/api/bridge/call", json={"calls": [
        {"name": "write_file", "arguments": {"path": str(f), "content": "a"}},
        {"name": "nonexistent", "arguments": {}},
    ]})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 2
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"
    assert results[0]["tool"] == "write_file" and results[1]["tool"] == "nonexistent"


def test_bridge_call_big_result_truncated_and_attachment(client, tmp_path):
    """大结果（>MAX_INLINE_RESULT）：响应回填 truncated 预览，完整内容在附件文件里。"""
    f = tmp_path / "big.txt"
    f.write_text("这行内容超过 20KB 阈值" * 2000, encoding="utf-8")
    r = client.post("/api/bridge/call", json={"tool": "read_file", "arguments": {"path": str(f)}})
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["externalized"] is True
    assert res["file"] and Path(res["file"]).exists()
    assert res["truncated"] is True
    assert "summary" in res and len(res["summary"]) > 0
    # 附件文件里是完整内容（信封全文），非截断预览
    assert "这行内容超过 20KB 阈值" in Path(res["file"]).read_text(encoding="utf-8")


def test_bridge_attachments_endpoint_and_ownership(client, tmp_path):
    """附件读取端点：own 读取 200、跨 caller 403、不存在 404。"""
    f = tmp_path / "big.txt"
    f.write_text("x" * 30000, encoding="utf-8")
    r = client.post("/api/bridge/call", json={
        "tool": "read_file", "arguments": {"path": str(f)},
        "caller_id": "caller_a", "batch_id": "b_abc"})
    res = r.json()["results"][0]
    rid, caller = res["request_id"], res["caller_id"]
    assert caller == "caller_a" and res["batch_id"] == "b_abc"
    r2 = client.get("/api/bridge/attachments", params={"caller_id": caller, "request_id": rid})
    assert r2.status_code == 200
    assert r2.json()["content"] == Path(res["file"]).read_text(encoding="utf-8")
    # 跨 caller 读取 → 403
    r3 = client.get("/api/bridge/attachments", params={"caller_id": "caller_b", "request_id": rid})
    assert r3.status_code == 403
    # 不存在 → 404
    r4 = client.get("/api/bridge/attachments",
                    params={"caller_id": "caller_a", "request_id": "r_000000000000"})
    assert r4.status_code == 404
    # 缺少 request_id → 400
    r5 = client.get("/api/bridge/attachments", params={"caller_id": "caller_a"})
    assert r5.status_code == 400


def test_bridge_call_no_double_externalize(client, tmp_path):
    """防二次外置死循环：read_file 读附件文件的结果带 _external_source 标记、完整返回。"""
    f = tmp_path / "big.txt"
    f.write_text("这行内容超过 20KB 阈值" * 2000, encoding="utf-8")
    r = client.post("/api/bridge/call", json={"tool": "read_file", "arguments": {"path": str(f)}})
    ext_file = r.json()["results"][0]["file"]
    assert ext_file and Path(ext_file).exists()
    # 模型再读附件文件：应完整返回（信封内层 result 带 _external_source 标记，不再触发截断/外置循环）
    r2 = client.post("/api/bridge/call", json={"tool": "read_file", "arguments": {"path": ext_file}})
    assert r2.status_code == 200
    res = r2.json()["results"][0]
    nested = res.get("result") or {}
    assert nested.get("_external_source") is True
    assert res.get("truncated") is not True
    assert len(nested.get("content") or "") > 20000


def test_bridge_tool_log_caller_filter(client, tmp_path):
    """工具日志按 caller 过滤（多 profile 审计归属）。"""
    f = tmp_path / "c.txt"
    client.post("/api/bridge/call", json={
        "tool": "write_file", "arguments": {"path": str(f), "content": "x"},
        "caller_id": "user_pa", "tab_id": "t1", "batch_id": "b1"})
    logs = client.get("/api/bridge/tool_logs", params={"caller_id": "user_pa"}).json()["logs"]
    assert logs and logs[-1]["caller_id"] == "user_pa"
    assert logs[-1]["tab_id"] == "t1"
    anon_logs = client.get("/api/bridge/tool_logs", params={"caller_id": "anon"}).json()["logs"]
    assert all(e["caller_id"] != "user_pa" for e in anon_logs)


def test_bridge_tools_idempotent_flag(client):
    """/tools 暴露幂等元数据：read_file 幂等、write_file 非幂等。"""
    tools = {t["name"]: t for t in client.get("/api/bridge/tools").json()["tools"]}
    assert tools["read_file"]["idempotent"] is True
    assert tools["list_directory"]["idempotent"] is True
    assert tools["write_file"]["idempotent"] is False
    assert tools["run_in_terminal"]["idempotent"] is False


def test_skills_crud(client):
    r = client.post("/api/skills", json={"name": "test-skill", "description": "测试"})
    assert r.status_code == 200
    assert r.json()["skill"]["source"] == "user"
    r = client.get("/api/skills/test-skill")
    assert r.json()["name"] == "test-skill"
    r = client.delete("/api/skills/test-skill")
    assert r.status_code == 200
    r = client.get("/api/skills/test-skill")
    assert r.status_code == 404


def test_skills_catalog(client):
    r = client.get("/api/skills/catalog")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"skills", "complete", "digest"}
    assert isinstance(data["digest"], str) and len(data["digest"]) == 64
    assert all("source" in s for s in data["skills"])


def test_skills_kebab_case_required(client):
    r = client.post("/api/skills", json={"name": "Bad_Name", "description": "x"})
    assert r.status_code == 400


def test_skills_load_and_execute_instruction(client):
    r = client.post("/api/skills", json={"name": "instruct-skill", "description": "说明技能",
                                         "prompt": "这是说明正文"})
    assert r.status_code == 200
    try:
        r = client.get("/api/skills/instruct-skill/load")
        assert r.status_code == 200
        assert r.json()["skill"]["content"] == "这是说明正文"
        r = client.post("/api/skills/instruct-skill/execute", json={"inputs": {}})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "instruction"
        assert body["output"] == "这是说明正文"
    finally:
        client.delete("/api/skills/instruct-skill")


def test_admin_status(client):
    r = client.get("/api/admin/status")
    assert r.status_code == 200
    assert r.json()["builtin_tools"] >= 17


def test_ask_user_caller_isolation():
    """多 profile 隔离：挂起按 caller 归属；查询过滤；跨 caller 应答返回 403。"""
    from tools.ask_user import AskUserManager, CALLER_CTX

    mgr = AskUserManager()
    loop = asyncio.new_event_loop()
    try:
        tok_a = CALLER_CTX.set("caller_pa")
        fa = loop.create_task(mgr.ask("问题A?", timeout_sec=300))
        loop.run_until_complete(asyncio.sleep(0))
        CALLER_CTX.reset(tok_a)

        tok_b = CALLER_CTX.set("caller_pb")
        fb = loop.create_task(mgr.ask("问题B?", timeout_sec=300))
        loop.run_until_complete(asyncio.sleep(0))
        CALLER_CTX.reset(tok_b)

        # 各 caller 只见自己的挂起
        assert [p["question"] for p in mgr.get_pending("caller_pa")] == ["问题A?"]
        assert [p["question"] for p in mgr.get_pending("caller_pb")] == ["问题B?"]
        assert len(mgr.get_pending("caller_pc")) == 0

        # A 尝试代答 B 的请求 → 403
        pend_b = mgr.get_pending("caller_pb")[0]
        denied = mgr.answer(pend_b["request_id"], "A说允许", caller_id="caller_pa")
        assert denied.get("code") == 403

        # B 自己应答成功
        ok = mgr.answer(pend_b["request_id"], "B允许", caller_id="caller_pb")
        assert ok["status"] == "answered"
        assert loop.run_until_complete(fb) == "B允许"

        # A 自己的仍挂起
        assert mgr.get_pending("caller_pa")[0]["question"] == "问题A?"
        # 老客户端不带 caller 也能应答（宽松过渡策略）
        ok2 = mgr.answer(mgr.get_pending("caller_pa")[0]["request_id"], "ok", caller_id=None)
        assert ok2["status"] == "answered"
    finally:
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()
