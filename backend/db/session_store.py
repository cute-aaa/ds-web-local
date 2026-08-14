"""SQLite 会话存储（标准库 sqlite3，零额外依赖）。"""
import json
import sqlite3
import time
import uuid
from typing import Dict, List, Optional

from core.config import DATA_DIR

DB_PATH = DATA_DIR / "sessions.db"


def _conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, title TEXT, created_at REAL, updated_at REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, tool_calls TEXT, created_at REAL)""")
        # 会话事件流（仅追加）：记录 session_start / user_message / tool_call /
        # tool_result / bridge_ask / approval 等事件，按 id 升序回放
        c.execute("""CREATE TABLE IF NOT EXISTS session_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, event_type TEXT,
            payload TEXT, created_at REAL)""")


def create_session(title: str = "") -> Dict:
    init_db()
    sid = uuid.uuid4().hex[:16]
    now = time.time()
    with _conn() as c:
        c.execute("INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
                  (sid, title, now, now))
    return {"id": sid, "title": title}


def list_sessions() -> List[Dict]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def delete_session(sid: str) -> bool:
    init_db()
    with _conn() as c:
        c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
        c.execute("DELETE FROM session_events WHERE session_id=?", (sid,))
        cur = c.execute("DELETE FROM sessions WHERE id=?", (sid,))
    return cur.rowcount > 0


def add_message(sid: str, role: str, content: str, tool_calls: Optional[List] = None) -> None:
    init_db()
    now = time.time()
    with _conn() as c:
        c.execute("INSERT INTO messages (session_id, role, content, tool_calls, created_at) VALUES (?,?,?,?,?)",
                  (sid, role, content, json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None, now))
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))


def get_messages(sid: str) -> List[Dict]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("tool_calls"):
            d["tool_calls"] = json.loads(d["tool_calls"])
        out.append(d)
    return out


def add_event(sid: str, event_type: str, payload: Dict) -> None:
    """追加一条会话事件（仅追加，不回写），并刷新会话 updated_at。"""
    init_db()
    now = time.time()
    with _conn() as c:
        c.execute("INSERT INTO session_events (session_id, event_type, payload, created_at) VALUES (?,?,?,?)",
                  (sid, event_type, json.dumps(payload, ensure_ascii=False), now))
        c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, sid))


def list_events(sid: str) -> List[Dict]:
    """按 id 升序返回会话事件流，payload 反序列化为 dict。"""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT * FROM session_events WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("payload"):
            d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out
