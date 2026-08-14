"""Git 操作内置工具（从 v1 迁移 + 修复）。"""
import asyncio
import os
from typing import Dict, List, Optional

from core.logger import get_logger

logger = get_logger("tools.git_ops")

MAX_DIFF = 100000  # 限制 diff 大小，防止撑爆内存


def _find_git_root(path: str) -> Optional[str]:
    path = os.path.abspath(path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    cur = path
    for _ in range(30):
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


async def _run_git(args: List[str], cwd: str) -> Dict:
    try:
        effective = os.path.abspath(cwd) if cwd not in (".", None) else None
        if effective is None:
            root = _find_git_root(cwd or os.getcwd())
            effective = root or os.getcwd()
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=effective,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        return {"returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", "ignore"),
                "stderr": stderr.decode("utf-8", "ignore"), "cwd": effective}
    except FileNotFoundError:
        return {"error": "Git not found. Please install Git."}
    except Exception as e:
        return {"error": str(e)}


async def git_status(working_dir: str = ".") -> Dict:
    res = await _run_git(["status", "--porcelain"], working_dir)
    if "error" in res:
        return res
    if res["returncode"] != 0:
        return {"error": f"git status failed: {res['stderr']}", "cwd": res.get("cwd")}
    changes = [{"status": line[:2], "file": line[3:]} for line in res["stdout"].splitlines() if len(line) >= 4]
    return {"status": "success", "working_dir": res.get("cwd"), "changes": changes, "clean": len(changes) == 0}


async def git_diff(file_path: Optional[str] = None, cached: bool = False, working_dir: str = ".") -> Dict:
    start = working_dir
    if (working_dir in (".", None)) and file_path and os.path.exists(file_path):
        start = os.path.dirname(os.path.abspath(file_path))
    args = ["diff"]
    if cached:
        args.append("--cached")
    if file_path:
        args.append(file_path)
    res = await _run_git(args, start)
    if "error" in res:
        return res
    if res["returncode"] != 0:
        return {"error": f"git diff failed: {res['stderr']}", "cwd": res.get("cwd")}
    diff = res["stdout"]
    return {"status": "success", "working_dir": res.get("cwd"), "diff": diff[:MAX_DIFF], "truncated": len(diff) > MAX_DIFF}


async def git_commit(message: str, add_all: bool = False, working_dir: str = ".") -> Dict:
    if add_all:
        add_res = await _run_git(["add", "-A"], working_dir)
        if add_res.get("returncode", 0) != 0:
            return {"error": f"git add failed: {add_res.get('stderr')}", "cwd": add_res.get("cwd")}
    res = await _run_git(["commit", "-m", message], working_dir)
    if "error" in res:
        return res
    if res["returncode"] == 0:
        return {"status": "success", "working_dir": res.get("cwd"), "message": res["stdout"].strip()}
    return {"status": "failed", "working_dir": res.get("cwd"), "error": (res["stdout"] + res["stderr"]).strip()}
