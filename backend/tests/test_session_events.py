"""会话事件流（session_events 仅追加表 + bridge 事件记录）与配置环境变量插值测试。"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import db.session_store as session_store
from core.config import ConfigLoader
from main import create_app
from tools.register import register_all_builtin_tools

register_all_builtin_tools()  # 手动注册（绕过 lifespan）


@pytest.fixture
def store_db(tmp_path, monkeypatch):
    """把会话存储重定向到临时 DB，避免污染 backend/data。"""
    monkeypatch.setattr(session_store, "DB_PATH", tmp_path / "sessions.db")
    session_store.init_db()
    return session_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "DB_PATH", tmp_path / "sessions.db")
    app = create_app()
    with TestClient(app) as c:
        yield c


# ---- A) session_store：add_event / list_events ----


def test_add_event_roundtrip(store_db):
    sid = store_db.create_session("t")["id"]
    store_db.add_event(sid, "user_message", {"text": "你好"})
    evs = store_db.list_events(sid)
    assert len(evs) == 1
    e = evs[0]
    assert e["event_type"] == "user_message"
    assert e["payload"] == {"text": "你好"}
    assert e["id"] > 0 and e["created_at"] > 0


def test_events_isolated_by_session(store_db):
    a = store_db.create_session("a")["id"]
    b = store_db.create_session("b")["id"]
    store_db.add_event(a, "tool_call", {"tool": "x"})
    store_db.add_event(b, "tool_call", {"tool": "y"})
    assert [e["payload"]["tool"] for e in store_db.list_events(a)] == ["x"]
    assert [e["payload"]["tool"] for e in store_db.list_events(b)] == ["y"]


def test_add_event_updates_updated_at(store_db):
    sid = store_db.create_session("t")["id"]
    old = next(s["updated_at"] for s in store_db.list_sessions() if s["id"] == sid)
    time.sleep(0.01)
    store_db.add_event(sid, "session_start", {})
    new = next(s["updated_at"] for s in store_db.list_sessions() if s["id"] == sid)
    assert new > old


def test_delete_session_removes_events(store_db):
    sid = store_db.create_session("t")["id"]
    store_db.add_event(sid, "session_start", {})
    assert store_db.delete_session(sid)
    assert store_db.list_events(sid) == []


# ---- B/C) bridge 事件记录 + events API ----


def _events(client, sid):
    r = client.get(f"/api/sessions/{sid}/events")
    assert r.status_code == 200
    return r.json()["events"]


def test_bridge_session_creates_and_records_start(client):
    r = client.post("/api/bridge/session", json={"title": "新会话"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    sid = body["session_id"]
    evs = _events(client, sid)
    assert [e["event_type"] for e in evs] == ["session_start"]
    assert evs[0]["payload"].get("title") == "新会话"


def test_bridge_session_resume_existing(client):
    sid = client.post("/api/bridge/session", json={"title": "s1"}).json()["session_id"]
    r = client.post("/api/bridge/session", json={"session_id": sid})
    assert r.status_code == 200
    assert r.json()["session_id"] == sid
    evs = _events(client, sid)
    assert len([e for e in evs if e["event_type"] == "session_start"]) == 2


def test_bridge_session_unknown_id_404(client):
    r = client.post("/api/bridge/session", json={"session_id": "nope"})
    assert r.status_code == 404


def test_bridge_call_records_tool_call(client, tmp_path):
    sid = client.post("/api/bridge/session", json={}).json()["session_id"]
    f = tmp_path / "t.txt"
    r = client.post("/api/bridge/call", json={
        "tool": "write_file",
        "arguments": {"path": str(f), "content": "hi"},
        "session_id": sid,
    })
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "success"
    calls = [e for e in _events(client, sid) if e["event_type"] == "tool_call"]
    assert len(calls) == 1
    p = calls[0]["payload"]
    assert p["tool"] == "write_file"
    assert p["arguments"] == json.dumps({"path": str(f), "content": "hi"}, ensure_ascii=False)
    assert p["duration_ms"] >= 0


def test_bridge_call_redacts_sensitive_args(client, tmp_path):
    sid = client.post("/api/bridge/session", json={}).json()["session_id"]
    f = tmp_path / "t.txt"
    r = client.post("/api/bridge/call", json={
        "tool": "write_file",
        "arguments": {"path": str(f), "content": "x"},
        "session_id": sid,
    })
    assert r.status_code == 200
    p = [e for e in _events(client, sid) if e["event_type"] == "tool_call"][0]["payload"]
    assert p["arguments"] == json.dumps({"path": str(f), "content": "x"}, ensure_ascii=False)


def test_summarize_redacts_and_truncates():
    # 直接单测 _record_tool_call 用的脱敏/截断逻辑（无需真实工具签名配合）
    from api.bridge import _summarize
    s = _summarize({"password": "secret123", "content": "x", "nested": {"token": "abc"}}, redact=True)
    assert '"password": "[REDACTED]"' in s
    assert '"token": "[REDACTED]"' in s
    assert '"content": "x"' in s
    assert "secret123" not in s and "abc" not in s
    long = _summarize({"data": "x" * 1000}, redact=True)
    assert len(long) == 500  # 截断到 500 字符


def test_bridge_call_multi_records_each(client, tmp_path):
    sid = client.post("/api/bridge/session", json={}).json()["session_id"]
    f1, f2 = tmp_path / "a.txt", tmp_path / "b.txt"
    r = client.post("/api/bridge/call", json={
        "calls": [
            {"name": "write_file", "arguments": {"path": str(f1), "content": "1"}},
            {"name": "write_file", "arguments": {"path": str(f2), "content": "2"}},
        ],
        "session_id": sid,
    })
    assert r.status_code == 200
    assert len(r.json()["results"]) == 2
    calls = [e for e in _events(client, sid) if e["event_type"] == "tool_call"]
    assert len(calls) == 2


def test_bridge_call_without_session_id_no_event(client, tmp_path):
    sid = client.post("/api/bridge/session", json={}).json()["session_id"]
    f = tmp_path / "t.txt"
    r = client.post("/api/bridge/call", json={
        "tool": "write_file", "arguments": {"path": str(f), "content": "hi"}})
    assert r.status_code == 200
    assert all(e["event_type"] != "tool_call" for e in _events(client, sid))


# ---- D) 配置环境变量插值 ----


def test_env_interpolate_var(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_HOST", "127.0.0.1")
    (tmp_path / "settings.yaml").write_text("server:\n  host: ${MY_HOST}\n", encoding="utf-8")
    c = ConfigLoader(tmp_path)
    assert c.get_settings("server.host") == "127.0.0.1"


def test_env_interpolate_default(monkeypatch, tmp_path):
    monkeypatch.delenv("MY_PORT", raising=False)
    (tmp_path / "settings.yaml").write_text("server:\n  port: ${MY_PORT:-8088}\n", encoding="utf-8")
    c = ConfigLoader(tmp_path)
    assert c.get_settings("server.port") == "8088"


def test_env_interpolate_default_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_PORT", "9999")
    (tmp_path / "settings.yaml").write_text("server:\n  port: ${MY_PORT:-8088}\n", encoding="utf-8")
    c = ConfigLoader(tmp_path)
    assert c.get_settings("server.port") == "9999"


def test_env_interpolate_unset_keeps_raw(monkeypatch, tmp_path):
    monkeypatch.delenv("NOPE_VAR", raising=False)
    (tmp_path / "settings.yaml").write_text(
        "a: ${NOPE_VAR}\nb: \"pre-${NOPE_VAR}-post\"\nn: 42\n", encoding="utf-8")
    c = ConfigLoader(tmp_path)
    assert c.get_settings("a") == "${NOPE_VAR}"
    assert c.get_settings("b") == "pre-${NOPE_VAR}-post"
    assert c.get_settings("n") == 42  # 非字符串值不受影响


def test_env_interpolate_nested_and_lists(monkeypatch, tmp_path):
    monkeypatch.setenv("SK_DIR", "data/user-skills")
    (tmp_path / "settings.yaml").write_text(
        "skills:\n  dirs:\n    - ${SK_DIR}\n    - bundled\n", encoding="utf-8")
    c = ConfigLoader(tmp_path)
    assert c.get_settings("skills.dirs") == ["data/user-skills", "bundled"]
