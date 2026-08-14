"""搜索内置工具。"""
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from core.logger import get_logger

logger = get_logger("tools.search")

IGNORE_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist', 'build', '.idea', '.vscode'}


def _is_binary(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            return b'\x00' in f.read(1024)
    except Exception:
        return True


def _search_file(fpath: str, query: str, max_per_file: int = 10):
    matches = []
    try:
        if _is_binary(fpath):
            return matches
        with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
            for i, line in enumerate(f):
                if query in line:
                    matches.append({"file": fpath, "line": i + 1, "content": line.strip()[:200]})
                    if len(matches) >= max_per_file:
                        break
    except Exception:
        pass
    return matches


async def grep_multi_search(query: str, path: str = ".", extensions: Optional[List[str]] = None,
                            max_results: int = 50) -> Dict:
    """多线程全局搜索，自动跳过二进制与无关目录。"""
    try:
        if extensions:
            extensions = [e if e.startswith('.') else f'.{e}' for e in extensions]
        abs_path = os.path.abspath(path)
        files = []
        for root, dirs, fs in os.walk(abs_path):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for f in fs:
                if extensions and not any(f.endswith(e) for e in extensions):
                    continue
                files.append(os.path.join(root, f))
        results = []
        with ThreadPoolExecutor(max_workers=10) as ex:
            for matches in ex.map(lambda f: _search_file(f, query), files):
                results.extend(matches)
                if len(results) >= max_results:
                    break
        return {"query": query, "results": results[:max_results],
                "count": len(results[:max_results]), "total_scanned": len(files), "status": "success"}
    except Exception as e:
        return {"error": str(e)}
