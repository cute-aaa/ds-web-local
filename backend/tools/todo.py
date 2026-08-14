"""任务管理内置工具。"""
import json
from typing import Dict, List

from core.config import DATA_DIR
from core.logger import get_logger

logger = get_logger("tools.todo")


async def manage_todos(todos: List[Dict]) -> Dict:
    """写入任务列表到 data/todos.json。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        p = DATA_DIR / "todos.json"
        p.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": "success", "count": len(todos), "file": str(p)}
    except Exception as e:
        return {"error": str(e)}


async def read_todos() -> Dict:
    """读取当前任务列表。"""
    try:
        p = DATA_DIR / "todos.json"
        if not p.exists():
            return {"status": "success", "todos": []}
        return {"status": "success", "todos": json.loads(p.read_text(encoding="utf-8"))}
    except Exception as e:
        return {"error": str(e)}
