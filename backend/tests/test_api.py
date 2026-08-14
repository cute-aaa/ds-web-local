import pytest
from fastapi.testclient import TestClient

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
