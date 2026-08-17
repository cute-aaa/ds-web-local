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
    f = tmp_path / "t.txt"
    r = client.post("/api/bridge/call", json={"tool": "write_file", "arguments": {"path": str(f), "content": "hi"}})
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "success"


def test_bridge_call_unknown_tool(client):
    r = client.post("/api/bridge/call", json={"tool": "nonexistent", "arguments": {}})
    assert r.status_code == 404


def test_bridge_call_externalized_result(client, tmp_path):
    """超长结果（>MAX_INLINE_RESULT）触发结果外置：返回 externalized 摘要 + 文件路径。
    回归：外置分支曾因 log 未定义（应为 logger）抛 NameError → HTTP 500 → 前端不回填。"""
    f = tmp_path / "big.txt"
    f.write_text("这行内容超过 20KB 阈值" * 2000, encoding="utf-8")
    r = client.post("/api/bridge/call", json={"tool": "read_file", "arguments": {"path": str(f)}})
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["externalized"] is True
    assert res["file"] and Path(res["file"]).exists()
    assert "summary" in res and len(res["summary"]) > 0
    assert "note" in res


def test_bridge_external_file_endpoint(client, tmp_path):
    """外置文件内容端点：正常返回 + 目录穿越拦截。"""
    f = tmp_path / "big.txt"
    f.write_text("x" * 30000, encoding="utf-8")
    r = client.post("/api/bridge/call", json={"tool": "read_file", "arguments": {"path": str(f)}})
    name = Path(r.json()["results"][0]["file"]).name
    r2 = client.get(f"/api/bridge/files/{name}")
    assert r2.status_code == 200
    assert "content" in r2.json() and len(r2.json()["content"]) > 20000
    # 目录穿越 / 非法文件名 → 404
    for bad in ("..%2Fsettings.yaml", "settings.yaml", "result_xx.json", "result_1234567890ab.json.bak"):
        assert client.get(f"/api/bridge/files/{bad}").status_code == 404, bad


def test_bridge_no_double_externalize(client, tmp_path):
    """防二次外置死循环：read_file 读外置文件的结果不再外置（带 _external_source 标记）。"""
    f = tmp_path / "big.txt"
    f.write_text("这行内容超过 20KB 阈值" * 2000, encoding="utf-8")
    r = client.post("/api/bridge/call", json={"tool": "read_file", "arguments": {"path": str(f)}})
    ext_file = r.json()["results"][0]["file"]
    assert ext_file and Path(ext_file).exists()
    # 模型再读外置文件：应完整内联返回（不 externalized）
    r2 = client.post("/api/bridge/call", json={"tool": "read_file", "arguments": {"path": ext_file}})
    assert r2.status_code == 200
    res = r2.json()["results"][0]
    assert res.get("externalized") is not True
    assert res.get("_external_source") is True
    assert "content" in res and len(res["content"]) > 20000


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
