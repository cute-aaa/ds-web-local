"""桥接 API（契约 A）：web 端接入本地能力。"""
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.config import get_config
from core.logger import get_logger
from core.errors import ApiError, ErrorCode
from db import session_store
from rolecard.generator import generate_role_card, generate_tools_json
from tools.registry import get_registry, ApprovalRequired
from mcp_services.manager import get_manager, split_tool_name

router = APIRouter(prefix="/api/bridge", tags=["bridge"])
logger = get_logger("api.bridge")

# 工具调用日志（内存环形缓冲，供控制台查看最近调用；重启清空）
from collections import deque
import time as _time

TOOL_LOG_MAX = 300
_tool_logs: deque = deque(maxlen=TOOL_LOG_MAX)


def _record_tool_log(entry: Dict) -> None:
    _tool_logs.append(entry)

# 调用方身份（caller_id）由请求体传入；带状态工具（ask_user）经 tools/ask_user.CALLER_CTX
# 读取当前请求归属（call() 内 set，沿 async 调用链传播）——多 profile 场景不跨账号代答/互读。


def _safe_dumps(value: Any, ensure_ascii: bool = False) -> str:
    """统一序列化：default=str + 兜底 repr，杜绝工具返回 bytes/Path 等导致的 TypeError 500。"""
    try:
        return json.dumps(value, ensure_ascii=ensure_ascii, default=str)
    except (TypeError, ValueError):
        try:
            return json.dumps({"__repr__": str(value)}, ensure_ascii=ensure_ascii)
        except Exception:
            return '"<unserializable>"'


# 结果内联阈值：单个工具结果序列化超过该长度时，响应只回填预览 + 附件路径，
# 完整内容默认落盘 data/attachments（模型可用 read_file 读取全量）
MAX_INLINE_RESULT = 20000
SUMMARY_LIMIT = 8000  # 大结果回填预览上限（字符）


def _inline_config() -> Dict[str, int]:
    """内联 / 摘要阈值（可配：bridge.inline_max_chars / bridge.summary_max_chars）。

    权衡：阈值调小 → 更省上下文，但模型要额外 read_file 取全量（多一轮往返）；
    调大 → 省轮次但吃上下文。默认 20000/8000，可按上下文窗口与轮次偏好调整。
    每次调用都读配置，支持热改即生效（无需重启后端）。
    """
    try:
        cfg = get_config().get_settings("bridge", {}) or {}
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}

    def _int(key: str, default: int) -> int:
        try:
            v = int(cfg.get(key, default) or 0)
        except (TypeError, ValueError):
            return default
        return v or default

    return {
        "inline_max": _int("inline_max_chars", MAX_INLINE_RESULT),
        "summary_max": _int("summary_max_chars", SUMMARY_LIMIT),
    }


class BridgeCallBody(BaseModel):
    tool: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    calls: List[Dict[str, Any]] = Field(default_factory=list)
    session_id: Optional[str] = None  # 可选：提供则记录 tool_call 事件
    # 调用方身份（P0/P1）：浏览器桥接脚本按 profile 持久 caller_id + 每页 tab_id + 每批 batch_id。
    # 全部可选：旧脚本不传时后端用 "anon" 兜底，功能不受影响（仅失去按调用方隔离/审计能力）。
    caller_id: Optional[str] = None
    tab_id: Optional[str] = None
    batch_id: Optional[str] = None


class BridgeSessionBody(BaseModel):
    title: str = ""
    session_id: Optional[str] = None  # 有则续接已有会话，无则新建


def _sensitive_keys() -> set:
    """事件记录中的敏感参数键（值替换为 [REDACTED]），取自 settings.yaml。"""
    keys = ["command", "password", "token", "authorization", "api_key"]
    try:
        cfg_keys = get_config().get_settings("logging.sensitive_keys")
        if isinstance(cfg_keys, list):
            keys = list(cfg_keys)
    except Exception:
        pass
    # "arguments" 是事件本身要记录的字段，不作为敏感键（仅其内部子键脱敏）
    return {k for k in keys if k != "arguments"}


def _redact(value: Any, sensitive: set) -> Any:
    """递归替换敏感键的值为 [REDACTED]。"""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k in sensitive else _redact(v, sensitive)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, sensitive) for v in value]
    return value


def _summarize(value: Any, limit: int = 500, redact: bool = False) -> str:
    """序列化为字符串并截断（供事件 payload 摘要使用）。"""
    if redact:
        value = _redact(value, _sensitive_keys())
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        s = str(value)
    return s[:limit]


def _record_tool_call(sid: str, tool: str, arguments: Dict, result: Any, duration_ms: int) -> None:
    """记录 tool_call 事件（参数/结果各截断 500 字符，敏感字段脱敏）。"""
    session_store.add_event(sid, "tool_call", {
        "tool": tool,
        "arguments": _summarize(arguments, redact=True),
        "result": _summarize(result),
        "duration_ms": duration_ms,
    })


class AskUserBody(BaseModel):
    request_id: str = ""
    answer: str = ""
    caller_id: Optional[str] = None  # 应答方声明归属；与挂起记录不一致时拒绝（403，防跨 profile 代答）
    tab_id: Optional[str] = None     # 应答方标签页；与挂起记录不一致时拒绝（403，防跨标签/跨会话代答）


async def _dispatch(tool_name: str, arguments: Dict) -> Any:
    from core.metrics import get_metrics
    metrics = get_metrics()
    metrics.tool_calls += 1
    try:
        registry = get_registry()
        manager = get_manager()
        if registry.is_builtin(tool_name):
            return await registry.call_builtin(tool_name, arguments)
        parsed = split_tool_name(tool_name)
        if parsed is not None:
            # mcp__server__tool（新格式）；split_tool_name 同时兼容旧格式 mcp.server.tool
            server, raw = parsed
            return await manager.call_tool(raw, server, arguments)
        raise ApiError(ErrorCode.NOT_FOUND, f"未知工具: {tool_name}", 404)
    except ApprovalRequired as e:
        # 审批挂起：不报 500，返回 approval_required 标记，前端提示用户去审批后重试
        logger.info(f"工具 {e.tool} 需要审批，request_id={e.request_id}")
        return {
            "approval_required": True,
            "request_id": e.request_id,
            "tool": e.tool,
            "arguments": e.arguments,
        }
    except Exception:
        metrics.tool_errors += 1
        raise


@router.get("/rolecard")
async def get_rolecard():
    return {"rolecard": generate_role_card()}


@router.get("/tools")
async def get_tools():
    return generate_tools_json()


@router.post("/call")
async def call(body: BridgeCallBody):
    """单/多工具调用 → 逐调用全量信封（契约见 docs/tool-call-reliability-spec.md §2.2）。

    变更要点（P0）：
    - 语义性失败（未知工具/参数错/执行异常）放入信封 status="error"，不再 HTTP 500；
    - 审批挂起放入信封 status="approval_required"（沿用审批记录 id）；
    - 每次调用结果默认落盘附件（data/attachments/<caller>/<date>/...），信封带 file 路径；
    - 大结果只回填预览（≤8000 字符），完整内容可 read_file 读取（不再自动全量内联）。
    """
    if not body.calls and not body.tool:
        raise ApiError(ErrorCode.BAD_REQUEST, "缺少 tool 或 calls")
    sid = body.session_id
    caller_id = (body.caller_id or "anon").strip() or "anon"
    tab_id = body.tab_id or ""
    batch_id = body.batch_id or ""
    # 让 ask_user 等带状态工具知道当前请求的归属（沿 async 调用链传播）
    from tools.ask_user import CALLER_CTX as ASK_USER_CTX, TAB_CTX as ASK_USER_TAB_CTX
    ASK_USER_CTX.set(caller_id)
    ASK_USER_TAB_CTX.set(tab_id)   # 同 profile 多标签隔离：确认请求只弹给发起它的标签页

    calls_to_run = body.calls or [{"name": body.tool, "arguments": body.arguments}]
    results = []
    for c in calls_to_run:
        name = str(c.get("name") or c.get("tool") or "")
        args = c.get("arguments") if isinstance(c.get("arguments"), dict) else {}
        if not name:
            results.append({
                "request_id": "r_" + uuid.uuid4().hex[:12],
                "batch_id": batch_id, "tool": "", "caller_id": caller_id,
                "arguments": args, "status": "error",
                "error": {"type": "BAD_REQUEST", "message": "缺少工具名"},
                "approval_required": False, "externalized": False,
                "file": None, "elapsed_ms": 0,
            })
            continue
        results.append(await _run_one(name, args, caller_id, tab_id, batch_id, sid))
    return {"batch_id": batch_id, "results": results}


async def _run_one(name: str, arguments: Dict, caller_id: str, tab_id: str,
                   batch_id: str, sid: Optional[str]) -> Dict:
    """执行单个工具 → 返回逐调用全量信封（含工具日志与默认附件落盘）。

    单个工具的任何异常都在此收敛为信封 status="error"，绝不上抛成 HTTP 500；
    审批（ApprovalRequired 在 _dispatch 内转为返回 dict）收敛为 status="approval_required"。
    """
    from core.attachments import persist_attachment, persist_attachment_md, append_call_journal
    t0 = _time.time()
    request_id = "r_" + uuid.uuid4().hex[:12]
    status: str = "ok"
    result: Any = None
    error: Optional[Dict] = None
    approval_required = False
    try:
        res = await _dispatch(name, arguments)
        if isinstance(res, dict) and res.get("approval_required"):
            status = "approval_required"
            approval_required = True
            # 沿用审批记录 id（hex12），保证 /api/approvals/<id>/approve 与旧桥接流程一致
            request_id = str(res.get("request_id") or request_id)
        else:
            result = res
            if sid:
                _record_tool_call(sid, name, arguments, result,
                                  int((_time.time() - t0) * 1000))
    except Exception as e:
        status = "error"
        error = {"type": type(e).__name__,
                 "message": str(getattr(e, "message", "") or e)[:500]}

    elapsed_ms = int((_time.time() - t0) * 1000)
    envelope: Dict[str, Any] = {
        "request_id": request_id,
        "batch_id": batch_id,
        "tool": name,
        "caller_id": caller_id,
        "arguments": arguments,
        "status": status,
        "result": result if status == "ok" else None,
        "error": error,
        "approval_required": approval_required,
        "elapsed_ms": elapsed_ms,
    }
    # 附件默认全量落盘（含完整 result/error 的信封全文，供回溯与 read_file 读全量）
    path = persist_attachment(caller_id, envelope)
    envelope["externalized"] = path is not None
    envelope["file"] = path or None
    # Markdown 视图与 .json 并存。必须在下方 result 截断之前落盘，否则 md 里只剩预览
    envelope["file_md"] = persist_attachment_md(caller_id, envelope)
    if path is None:
        envelope["note"] = "附件落盘失败，已降级为仅内联摘要"
    elif status == "ok" and result is not None:
        # 大结果且非“读附件/外置文件返回”→ 响应只回预览，完整内容在附件文件里；
        # 该逻辑须在落盘之后执行，保证附件里是完整内容（_external_source 防死循环）
        s = _safe_dumps(result)
        inline_cfg = _inline_config()
        if len(s) > inline_cfg["inline_max"] and not (isinstance(result, dict)
                                                      and result.get("_external_source")):
            lim = inline_cfg["summary_max"]
            envelope["result"] = s[:lim]
            envelope["summary"] = s[:lim]
            envelope["truncated"] = True  # 浏览器据此把该结果渲染为“预览+附件路径”，避免与正常结果混淆

    # 调用流水（按天追加 jsonl）：一行一个 request_id，命令与结果同在，供人工审计/回放
    append_call_journal(caller_id, envelope)

    # 控制台工具日志（含 caller/tab 归属，P1 起控制台可按 caller 筛选）
    _record_tool_log({
        "time": _time.strftime("%H:%M:%S"),
        "caller_id": caller_id,
        "tab_id": tab_id,
        "request_id": request_id,
        "tool": name,
        "arguments": _safe_dumps(arguments)[:300],
        "status": status,
        "elapsed_ms": elapsed_ms,
        "result_preview": (f"error: {error['message']}" if status == "error"
                           else _safe_dumps(result)[:200]),
    })
    return envelope


@router.get("/tool_logs")
async def tool_logs(limit: int = 50, caller_id: Optional[str] = None):
    """最近工具调用日志（控制台「工具日志」页）；可选按 caller_id 过滤。"""
    logs = list(_tool_logs)[-min(max(limit, 1), TOOL_LOG_MAX):]
    if caller_id:
        cid = caller_id.strip()
        logs = [e for e in logs if e.get("caller_id") == cid]
    return {"logs": logs}


@router.get("/attachments")
async def get_attachment(caller_id: str = "anon", request_id: str = ""):
    """按 caller + request_id 读取附件文件全文（归属强校验：跨 caller 读取返回 403）。

    取代旧 /files/{name} 的“只按文件名裸读”：先按 caller 目录定位，
    若文件属于其他 caller 则拒绝，杜绝跨 profile 互读。
    """
    from core.attachments import lookup_attachment
    if not request_id:
        raise ApiError(ErrorCode.BAD_REQUEST, "缺少 request_id")
    result, path = lookup_attachment(caller_id, request_id)
    if result == "own" and path:
        return {"caller_id": caller_id, "request_id": request_id,
                "file": str(path), "content": path.read_text(encoding="utf-8")}
    if result == "foreign":
        raise ApiError(ErrorCode.UNAUTHORIZED, "该附件属于其他调用方，不能读取", 403)
    raise ApiError(ErrorCode.NOT_FOUND, "附件不存在或已清理", 404)


@router.get("/files/{name}")
async def get_external_file(name: str):
    """返回外置结果文件（data/tmp/result_*.json）的完整内容。

    供前端把超长结果自动内联回填给模型（"伪附件"）：模型无需再调 read_file
    读外置文件，减少一轮往返，也避免 read_file → 外置 → read_file 死循环。
    只允许 result_<12位hex>.json，防目录穿越。
    """
    if not re.fullmatch(r"result_[0-9a-f]{12}\.json", name):
        raise ApiError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    from core.config import DATA_DIR
    path = DATA_DIR / "tmp" / name
    if not path.is_file():
        raise ApiError(ErrorCode.NOT_FOUND, "文件不存在", 404)
    return {"name": name, "content": path.read_text(encoding="utf-8")}


@router.post("/session")
async def start_session(body: Optional[BridgeSessionBody] = None):
    """创建或续接会话并记录 session_start 事件（事件流回放的起点）。"""
    body = body or BridgeSessionBody()
    if body.session_id:
        # 有 session_id：校验会话存在（续接），不存在则 404
        existing = {s["id"] for s in session_store.list_sessions()}
        if body.session_id not in existing:
            raise ApiError(ErrorCode.NOT_FOUND, "会话不存在", 404)
        sid = body.session_id
    else:
        sid = session_store.create_session(body.title)["id"]
    session_store.add_event(sid, "session_start", {"title": body.title})
    return {"session_id": sid, "status": "ok"}


@router.get("/ask_user/pending")
async def ask_user_pending(caller_id: Optional[str] = None, tab_id: Optional[str] = None):
    """当前挂起的人类确认请求列表（桥接层轮询用）。

    可选按调用方（profile）+ 标签页过滤：同 profile 开多标签时，每个标签只看到自己
    发起的确认请求，避免跨会话代答。
    """
    from tools.ask_user import get_pending_asks
    return {"pending": get_pending_asks(caller_id, tab_id)}


class ConversationLogBody(BaseModel):
    role: str = "assistant"   # assistant | user
    content: str = ""
    session_id: Optional[str] = None
    caller_id: Optional[str] = None  # 发起方归属（多 profile 审计用）


@router.post("/log")
async def conversation_log(body: ConversationLogBody):
    """桥接端对话输出落盘：data/conversations/YYYY-MM-DD.log（每行 JSON，供调试/审计）。

    设计：web 端模型输出由桥接脚本捕获后写入此文件，方便查看模型实际输出内容。
    """
    import json
    import time
    from core.config import DATA_DIR
    content = (body.content or "").strip()
    if not content:
        return {"status": "ok", "skipped": True}
    conv_dir = DATA_DIR / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    date = time.strftime("%Y-%m-%d")
    f = conv_dir / f"{date}.log"
    record = {
        "time": time.strftime("%H:%M:%S"),
        "role": body.role,
        "session_id": body.session_id,
        "caller_id": body.caller_id or "anon",
        "content": content[:20000],  # 单条上限 20KB，防爆盘
    }
    with open(f, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"status": "ok", "file": str(f)}


@router.post("/ask_user")
async def ask_user_answer(body: AskUserBody):
    """回传人类确认应答（桥接层确认框 → 后端 ask_user）。"""
    from tools.ask_user import answer_ask
    if not body.request_id:
        raise ApiError(ErrorCode.BAD_REQUEST, "缺少 request_id")
    result = answer_ask(body.request_id, body.answer,
                        caller_id=body.caller_id, tab_id=body.tab_id)
    if result.get("code") == 403:
        raise ApiError(ErrorCode.UNAUTHORIZED, result.get("error", "归属校验失败"), 403)
    if "error" in result:
        raise ApiError(ErrorCode.NOT_FOUND, result["error"], 404)
    return result
