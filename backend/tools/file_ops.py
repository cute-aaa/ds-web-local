"""文件操作内置工具（从 v1 迁移 + 修复）。"""
import ast
import re
from pathlib import Path
from typing import Dict

from core.logger import get_logger

logger = get_logger("tools.file_ops")


def _is_external_result_file(path: str) -> bool:
    """外置结果文件（data/tmp/result_*.json）？读取它时打 _external_source 标记，
    外置分支跳过，避免 read_file → 外置 → read_file 死循环。"""
    from core.config import DATA_DIR
    try:
        p = Path(path).resolve()
        tmp = (DATA_DIR / "tmp").resolve()
        return p.parent == tmp and p.name.startswith("result_") and p.name.endswith(".json")
    except Exception:
        return False


async def search_replace(file_path: str, old_str: str, new_str: str, count: int = -1) -> Dict:
    """搜索替换。count=-1 全量，count=1 第一个（修复 v1 只替换第一个的问题）。"""
    try:
        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"}
        content = p.read_text(encoding="utf-8")
        if old_str not in content:
            return {"error": "old_str not found in file", "file_path": str(p)}
        match_count = content.count(old_str)
        if count == -1:
            new_content = content.replace(old_str, new_str)
            replaced = match_count
        else:
            parts = content.split(old_str, count)
            new_content = new_str.join(parts)
            replaced = min(count, match_count)
        p.write_text(new_content, encoding="utf-8")
        return {"file_path": str(p), "status": "success", "replaced": replaced, "total_matches": match_count}
    except Exception as e:
        logger.error(f"search_replace 失败: {e}")
        return {"error": str(e)}


async def read_file(path: str, offset: int = 1, limit: int = 2000) -> Dict:
    """读取文件（带行号，支持分页；自动检测编码，二进制/未知编码给出明确提示而非乱码）。"""
    MAX_CONTENT = 50000  # 单次返回内容上限（字符），防止大结果撑爆上下文
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        data = p.read_bytes()
        # 二进制检测：NUL 字节或控制字符占比高 → 明确提示而非乱码
        if b"\x00" in data[:8192]:
            return {"error": f"二进制文件（含 NUL 字节），无法以文本读取: {path}", "binary": True}
        # 编码检测链：utf-8 → gbk（中文 Windows 常见）→ utf-16 → latin-1（兜底可逆）
        text = None
        used_enc = None
        for enc in ("utf-8", "gbk", "utf-16", "latin-1"):
            try:
                text = data.decode(enc)
                used_enc = enc
                break
            except (UnicodeDecodeError, ValueError):
                continue
        if text is None:
            return {"error": f"无法识别文件编码: {path}（请用 search_replace 或指定编码处理）"}
        lines = text.splitlines()
        total = len(lines)
        start = max(1, offset)
        end = min(total, start + limit - 1)
        numbered = [f"{i}|{lines[i-1]}" for i in range(start, end + 1)]
        content = "\n".join(numbered)
        truncated = len(content) > MAX_CONTENT
        if truncated:
            content = content[:MAX_CONTENT] + "\n...[内容已截断]"
        external_source = {"_external_source": True} if _is_external_result_file(str(p)) else {}
        return {"path": str(p), "total_lines": total, "offset": start, "limit": limit,
                "encoding": used_enc, "truncated": truncated, "content": content,
                **external_source}
    except Exception as e:
        return {"error": str(e)}


async def write_file(path: str, content: str) -> Dict:
    """写入文件（覆盖，自动建目录）。"""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"path": str(p), "status": "success", "bytes": len(content.encode("utf-8"))}
    except Exception as e:
        return {"error": str(e)}


async def list_directory(path: str = ".", recursive: bool = False) -> Dict:
    """列出目录内容（recursive=true 时递归收集文件，供按大小/名称排序；结果截断防爆上下文）。"""
    MAX_ENTRIES = 500
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"Directory not found: {path}"}
        entries = []
        total = 0
        if recursive:
            # 递归模式：收集所有文件（相对路径 + 大小），目录不展开
            for e in sorted(p.rglob("*")):
                try:
                    if e.is_file():
                        total += 1
                        if len(entries) < MAX_ENTRIES:
                            entries.append({
                                "name": str(e.relative_to(p)).replace("\\", "/"),
                                "type": "file",
                                "size": e.stat().st_size,
                            })
                except OSError:
                    continue
        else:
            for e in sorted(p.iterdir()):
                total += 1
                if len(entries) < MAX_ENTRIES:
                    entries.append({
                        "name": e.name,
                        "type": "dir" if e.is_dir() else "file",
                        "size": e.stat().st_size if e.is_file() else None,
                    })
        truncated = total > MAX_ENTRIES
        return {
            "path": str(p), "count": len(entries), "entries": entries,
            "truncated": truncated, "total_count": total,
        }
    except Exception as e:
        return {"error": str(e)}


async def line_edit(file_path: str, edits: str) -> Dict:
    """行编辑：支持 SEARCH/REPLACE 代码块多处精确编辑。"""
    try:
        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"}
        content = p.read_text(encoding="utf-8")
        pattern = r'<{7}\s+SEARCH\s*\n(.*?)\n={7}\s*\n(.*?)\n>{7}\s+REPLACE'
        matches = re.findall(pattern, edits, re.DOTALL)
        if not matches:
            return {"error": "No valid SEARCH/REPLACE blocks found",
                    "format": "<<<<<<< SEARCH\n[old]\n=======\n[new]\n>>>>>>> REPLACE"}
        modified = content
        applied, errors = [], []
        for i, (search_block, replace_block) in enumerate(matches):
            if search_block not in modified:
                errors.append({"block": i + 1, "error": "Search block not found"})
                continue
            if modified.count(search_block) > 1:
                errors.append({"block": i + 1, "error": f"Search block matches {modified.count(search_block)} times (must be unique)"})
                continue
            modified = modified.replace(search_block, replace_block, 1)
            applied.append({"block": i + 1, "status": "applied"})
        if errors and not applied:
            return {"error": "All edit blocks failed", "errors": errors, "file_path": str(p)}
        p.write_text(modified, encoding="utf-8")
        result = {"file_path": str(p), "status": "success", "applied": len(applied), "total": len(matches)}
        if errors:
            result["warnings"] = errors
        return result
    except Exception as e:
        return {"error": str(e)}


async def get_file_outline(file_path: str) -> Dict:
    """获取文件大纲（类/函数列表）。Python 用 AST，其他用正则。"""
    try:
        p = Path(file_path)
        if not p.exists():
            return {"error": f"File not found: {file_path}"}
        content = p.read_text(encoding="utf-8", errors="ignore")
        outline = []
        if file_path.endswith(".py"):
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        outline.append({
                            "type": "class" if isinstance(node, ast.ClassDef) else "function",
                            "name": node.name, "line": node.lineno,
                            "args": [a.arg for a in node.args.args] if hasattr(node, "args") else [],
                        })
                outline.sort(key=lambda x: x["line"])
                return {"file_path": str(p), "outline": outline, "status": "success"}
            except SyntaxError:
                pass
        patterns = [
            (r'^\s*(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_]+)', "function"),
            (r'^\s*(?:export\s+)?class\s+([a-zA-Z0-9_]+)', "class"),
            (r'^\s*def\s+([a-zA-Z0-9_]+)', "function"),
            (r'^\s*class\s+([a-zA-Z0-9_]+)', "class"),
        ]
        for i, line in enumerate(content.splitlines()):
            for pat, t in patterns:
                m = re.search(pat, line)
                if m:
                    outline.append({"type": t, "name": m.group(1), "line": i + 1})
                    break
        return {"file_path": str(p), "outline": outline, "status": "success"}
    except Exception as e:
        return {"error": str(e)}
