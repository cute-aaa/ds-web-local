"""工具结果附件落盘：data/attachments/<caller_id>/<date>/result_<request_id>.json

设计（docs/tool-call-reliability-spec.md §2.3）：
- 每次工具调用（无论大小/成败）默认生成一个附件文件，内容为该调用的完整信封 JSON
  （含完整 result / error / approval 信息），供模型用 read_file 读取全量、供审计回溯。
- 目录按 caller + 本地日期分层，杜绝跨 profile 混放、便于按保留策略清理。
- request_id 文件名做严格净化，防目录穿越；跨 caller 读取由 lookup_attachment 判定。
"""
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import DATA_DIR
from core.logger import get_logger

logger = get_logger("core.attachments")

# 文件名净化（request_id 允许 [A-Za-z0-9_-]，去掉其余字符）
_FILE_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]")
_CALLER_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def attachments_root() -> Path:
    return DATA_DIR / "attachments"


def _safe_caller(caller_id: Optional[str]) -> str:
    c = (caller_id or "anon").strip()
    c = _CALLER_SAFE_RE.sub("_", c)[:64]
    return c or "anon"


def _safe_rid(request_id: Optional[str]) -> str:
    c = _FILE_SAFE_RE.sub("", str(request_id or ""))[:64]
    return c or "unknown"


def persist_attachment(caller_id: Optional[str], envelope: Dict) -> Optional[str]:
    """把单个调用的完整信封写入附件文件，返回文件路径；失败返回 None（不阻断主流程）。"""
    try:
        base = attachments_root() / _safe_caller(caller_id) / time.strftime("%Y-%m-%d")
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"result_{_safe_rid(envelope.get('request_id'))}.json"
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=1, default=str),
                        encoding="utf-8")
        return str(path)
    except Exception as e:
        logger.warning(f"附件落盘失败: {e}")
        return None


def render_envelope_markdown(envelope: Dict) -> str:
    """把调用信封渲染为 Markdown。

    与 .json 附件面向不同消费者：
      - .json：程序解析（read_file 取结构化字段、脚本二次处理）
      - .md  ：模型阅读与人工查看（层次清晰、无需脑内解析转义，也便于直接贴进聊天框）
    """
    tool = envelope.get("tool") or "unknown"
    status = envelope.get("status") or "unknown"
    elapsed = envelope.get("elapsed_ms")
    args = envelope.get("arguments") or {}
    result = envelope.get("result")
    error = envelope.get("error")

    lines = [f"# 工具调用结果 · {tool}", ""]
    lines.append(f"- request_id: `{envelope.get('request_id') or 'unknown'}`")
    lines.append("- 状态: **%s**%s" % (status, f"（{elapsed}ms）" if elapsed is not None else ""))
    if envelope.get("caller_id"):
        lines.append(f"- 调用方: `{envelope['caller_id']}`")
    if envelope.get("approval_required"):
        lines.append("- ⏸ 需要人工审批：请在审批控制台批准后重发原提示")
    lines.append("")

    # 原始调用：便于模型核对参数，也便于人工复制后重试
    try:
        args_json = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        args_json = str(args)
    lines += ["## 原始调用", "", "```", f"@@tool {tool} {args_json}", "```", ""]

    if status == "ok":
        lines += ["## 结果", ""]
        if result is None:
            lines.append("（返回 null：调用成功但没有可返回的内容）")
        elif isinstance(result, str):
            lines.append(result)
        else:
            lines += ["```json",
                      json.dumps(result, ensure_ascii=False, indent=1, default=str),
                      "```"]
    elif error:
        lines += ["## 错误", "",
                  f"- 类型: `{error.get('type')}`",
                  f"- 信息: {error.get('message')}"]
    else:
        # 非 ok 且无 error（如 approval_required）：也要有交代，否则 md 里结果段空白
        lines += ["## 结果", "", f"（状态：{status}，无返回内容）"]
    lines.append("")
    return "\n".join(lines)


def persist_attachment_md(caller_id: Optional[str], envelope: Dict) -> Optional[str]:
    """把调用信封渲染为 Markdown 落盘（与 .json 并存），返回路径；失败返回 None。

    注意：须在信封 result 被截断之前调用，否则落盘的只是预览而非完整内容。
    """
    try:
        base = attachments_root() / _safe_caller(caller_id) / time.strftime("%Y-%m-%d")
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"result_{_safe_rid(envelope.get('request_id'))}.md"
        path.write_text(render_envelope_markdown(envelope), encoding="utf-8")
        return str(path)
    except Exception as e:
        logger.warning(f"Markdown 附件落盘失败: {e}")
        return None


def append_call_journal(caller_id: Optional[str], envelope: Dict) -> Optional[str]:
    """把单次调用信封追加写入当日流水 calls_<date>.jsonl（一行一个 request_id）。

    与 per-request 附件（result_<request_id>.json）互补，面向不同消费者：
      - 单文件：供模型用 read_file 按 request_id 取全量（模型侧消费，随机访问）
      - 流水文件：按时间顺序记录该 caller 当天的每一次调用，一行同时含**命令与结果**
        （arguments / result / error / status），便于人工审计与回放：
        「哪条命令得到了什么结果」按 request_id 一查即得。
    """
    try:
        base = attachments_root() / _safe_caller(caller_id) / time.strftime("%Y-%m-%d")
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"calls_{time.strftime('%Y-%m-%d')}.jsonl"
        # 单行化：JSON 内不允许裸换行，否则破坏 jsonl 的行边界
        line = json.dumps(envelope, ensure_ascii=False, default=str).replace("\n", " ")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return str(path)
    except Exception as e:
        logger.warning(f"调用流水写入失败: {e}")
        return None


def _iter_date_dirs(caller_id: str):
    """列出某 caller 下的日期目录。"""
    base = attachments_root() / _safe_caller(caller_id)
    if not base.is_dir():
        return
    for d in sorted(base.iterdir()):
        if d.is_dir() and _DATE_RE.match(d.name):
            yield d


def _path_in_caller(caller_id: str, request_id: str) -> Optional[Path]:
    rid = _safe_rid(request_id)
    for d in _iter_date_dirs(caller_id):
        p = d / f"result_{rid}.json"
        if p.is_file():
            return p
    return None


def _find_any(request_id: str, exclude_caller: Optional[str] = None) -> Optional[Path]:
    """在全部 caller 目录里定位附件（跨 caller 判定用）。"""
    rid = _safe_rid(request_id)
    root = attachments_root()
    if not root.is_dir():
        return None
    try:
        for caller_dir in root.iterdir():
            if not caller_dir.is_dir():
                continue
            if exclude_caller and caller_dir.name == _safe_caller(exclude_caller):
                continue
            for p in caller_dir.glob(f"*/result_{rid}.json"):
                if p.is_file():
                    return p
    except Exception:
        pass
    return None


def lookup_attachment(caller_id: Optional[str], request_id: str) -> Tuple[str, Optional[Path]]:
    """归属校验式附件定位。

    返回 ("own", path)  附件属于该 caller，path 有效
    返回 ("foreign", path)  附件属于其他 caller（调用方不得读取）
    返回 ("missing", None)  不存在/已清理
    """
    own = _path_in_caller(caller_id, request_id)
    if own:
        return "own", own
    foreign = _find_any(request_id, exclude_caller=caller_id)
    if foreign:
        return "foreign", foreign
    return "missing", None


def _attachment_config():
    """附件保留配置（默认：7 天 / 每 caller 500 个；0 表示不清理）。"""
    try:
        from core.config import get_config
        cfg = get_config().get_settings("bridge", {}) or {}
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "retention_days": int(cfg.get("attachment_retention_days", 7) or 0),
        "max_per_caller": int(cfg.get("attachment_max_per_caller", 500) or 0),
    }


def sweep_attachments() -> Dict:
    """按保留策略清理过期/超量附件文件（启动时 + 周期任务调用，幂等）。"""
    cfg = _attachment_config()
    removed = 0
    try:
        root = attachments_root()
        if not root.is_dir():
            return {"removed": 0}
        now = time.time()
        retention = cfg["retention_days"] * 86400
        max_per_caller = cfg["max_per_caller"]
        for caller_dir in root.iterdir():
            if not caller_dir.is_dir():
                continue
            files: List[Path] = []
            # 同时清理 .json 与 .md（两种视图并存，缺一即残留）
            for p in caller_dir.rglob("result_*"):
                if p.is_file() and p.suffix in (".json", ".md"):
                    files.append(p)
                    # 过期清理（retention>0 时）
                    if retention > 0 and (now - p.stat().st_mtime) > retention:
                        try:
                            p.unlink()
                            removed += 1
                        except Exception as e:
                            logger.warning(f"清理附件失败 {p}: {e}")
            # 每 caller 数量上限（按文件名即 request_id 排序近似时间序，删最旧）
            if max_per_caller > 0 and len(files) > max_per_caller:
                files.sort(key=lambda p: p.name)
                for p in files[: len(files) - max_per_caller]:
                    if p.exists():
                        try:
                            p.unlink()
                            removed += 1
                        except Exception as e:
                            logger.warning(f"清理附件失败 {p}: {e}")
    except Exception as e:
        logger.warning(f"附件清理异常: {e}")
    if removed:
        logger.info(f"附件清理完成，移除 {removed} 个文件")
    return {"removed": removed}
