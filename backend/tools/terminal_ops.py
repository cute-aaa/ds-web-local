"""终端会话内置工具（从 v1 迁移 + 修复僵尸进程）。"""
import asyncio
import os
import queue
import subprocess
import threading
import uuid
from typing import Dict, List, Optional

from core.logger import get_logger

logger = get_logger("tools.terminal_ops")

_sessions: Dict[str, "TerminalSession"] = {}


class TerminalSession:
    def __init__(self, cwd: Optional[str] = None):
        self.id = str(uuid.uuid4())[:8]
        self.cwd = cwd or os.getcwd()
        self.output_queue: queue.Queue = queue.Queue()
        self.history: List[str] = []
        self.running = True
        try:
            self.proc = subprocess.Popen(
                ["cmd.exe"], cwd=self.cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            raise RuntimeError(f"启动终端失败: {e}")
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _reader(self):
        try:
            while self.running:
                line = self.proc.stdout.readline()
                if not line:
                    break
                self.output_queue.put(line)
                self.history.append(line)
                if len(self.history) > 2000:
                    self.history.pop(0)
        except Exception:
            pass
        finally:
            self.running = False

    def write(self, text: str) -> Dict:
        if self.proc.poll() is not None:
            return {"error": "终端已关闭"}
        try:
            if not text.endswith("\n"):
                text += "\n"
            self.proc.stdin.write(text)
            self.proc.stdin.flush()
            return {"status": "sent"}
        except Exception as e:
            return {"error": str(e)}

    def read(self, lines: int = 50) -> List[str]:
        out = []
        while not self.output_queue.empty() and (lines is None or len(out) < lines):
            try:
                out.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return out

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def close(self):
        """关闭终端并清理子进程（修复 v1 僵尸进程问题）。"""
        self.running = False
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass


async def create_terminal(cwd: Optional[str] = None) -> Dict:
    try:
        if cwd and not os.path.isdir(cwd):
            return {"error": f"目录不存在: {cwd}"}
        s = TerminalSession(cwd=cwd)
        _sessions[s.id] = s
        return {"status": "success", "session_id": s.id, "cwd": s.cwd}
    except Exception as e:
        return {"error": str(e)}


async def terminal_input(session_id: str, command: str) -> Dict:
    s = _sessions.get(session_id)
    if not s:
        return {"error": "会话不存在"}
    return s.write(command)


async def terminal_read(session_id: str, lines: int = 50) -> Dict:
    s = _sessions.get(session_id)
    if not s:
        return {"error": "会话不存在"}
    return {"session_id": session_id, "output": "".join(s.read(lines)), "alive": s.is_alive()}


async def terminal_run_command(session_id: str, command: str, wait: float = 1.0) -> Dict:
    s = _sessions.get(session_id)
    if not s:
        return {"error": "会话不存在"}
    r = s.write(command)
    if "error" in r:
        return r
    await asyncio.sleep(wait)
    return await terminal_read(session_id)


async def list_terminals() -> Dict:
    for sid in list(_sessions.keys()):
        if not _sessions[sid].is_alive():
            _sessions[sid].close()
            del _sessions[sid]
    active = [{"id": sid, "cwd": s.cwd} for sid, s in _sessions.items() if s.is_alive()]
    return {"active": active, "count": len(active)}


async def delete_terminal(session_id: str) -> Dict:
    s = _sessions.pop(session_id, None)
    if not s:
        return {"error": "会话不存在"}
    s.close()
    return {"status": "success"}
