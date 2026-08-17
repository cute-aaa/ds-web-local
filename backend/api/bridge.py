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

# 结果外置阈值：单个工具结果序列化超过该长度时，完整内容落 data/tmp 文件，
# 只回填摘要 + 文件路径（模型可随时用 read_file 读取完整内容）
MAX_INLINE_RESULT = 20000


def _externalize_result(result) -> str:
    """把超长结果写入 data/tmp/result_*.json，返回文件路径。"""
    from core.config import DATA_DIR
    tmp_dir = DATA_DIR / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / f"result_{uuid.uuid4().hex[:12]}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return str(path)


class BridgeCallBody(BaseModel):
    tool: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    calls: List[Dict[str, Any]] = Field(default_factory=list)
    session_id: Optional[str] = None  # 可选：提供则记录 tool_call 事件


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
    """单/多工具调用；带 session_id 时逐条记录 tool_call 事件。"""
    results = []
    sid = body.session_id

    async def _run(name: str, args: Dict) -> Any:
        t0 = time.perf_counter()
        res = await _dispatch(name, args)
        if sid:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            _record_tool_call(sid, name, args, res, duration_ms)
        return res

    if body.calls:
        for c in body.calls:
            try:
                results.append(await _run_tracked(c.get("name", ""), c.get("arguments", {}), _run))
            except Exception as e:
                # 单工具失败不中断整批：错误作为结果返回，模型能看到失败详情并针对性重试
                # （审批 ApprovalRequired 是返回 dict 而非抛异常，不受影响）
                results.append({
                    "tool": c.get("name", ""),
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}",
                })
    elif body.tool:
        results.append(await _run_tracked(body.tool, body.arguments, _run))
    else:
        raise ApiError(ErrorCode.BAD_REQUEST, "缺少 tool 或 calls")
    # 结果外置：超长结果落本地临时文件，回填摘要 + 路径（模型用 read_file 自行读取完整内容）
    externalized = 0
    for i, r in enumerate(results):
        s = json.dumps(r, ensure_ascii=False)
        # 跳过已外置来源（read_file 读外置文件带 _external_source 标记）——
        # 否则模型读外置文件的结果又 >20KB 再外置 → read_file → 外置 死循环
        if len(s) > MAX_INLINE_RESULT and not (isinstance(r, dict) and r.get("_external_source")):
            path = _externalize_result(r)
            results[i] = {
                "externalized": True,
                "note": f"结果较长（{len(s)} 字符），完整内容已保存到 {path}。"
                        f"如需完整结果，请调用 read_file 工具读取该文件（path 参数为上面的完整路径）。",
                "summary": s[:8000],
                "file": str(path),
            }
            externalized += 1
    if externalized:
        logger.info(f"{externalized}/{len(results)} 个结果超长已外置到 data/tmp")
    return {"results": results}


async def _run_tracked(name: str, arguments: Dict, runner) -> Dict:
    """执行单个工具并记录调用日志（耗时/状态/结果预览，供控制台查看）。

    异常不吞：ApiError(404)/审批等语义由 runner（call 内的 _run → _dispatch）处理，
    这里只记录后重抛。
    """
    t0 = _time.time()
    try:
        res = await runner(name, arguments)
        status = "error" if (isinstance(res, dict) and res.get("error")) else "ok"
        _record_tool_log({
            "time": _time.strftime("%H:%M:%S"),
            "tool": name,
            "arguments": json.dumps(arguments, ensure_ascii=False)[:300],
            "status": status,
            "elapsed_ms": int((_time.time() - t0) * 1000),
            "result_preview": json.dumps(res, ensure_ascii=False)[:200],
        })
        return res
    except Exception as e:
        _record_tool_log({
            "time": _time.strftime("%H:%M:%S"),
            "tool": name,
            "arguments": json.dumps(arguments, ensure_ascii=False)[:300],
            "status": "error",
            "elapsed_ms": int((_time.time() - t0) * 1000),
            "result_preview": f"异常: {e}",
        })
        raise


@router.get("/tool_logs")
async def tool_logs(limit: int = 50):
    """最近工具调用日志（控制台「工具日志」页）。"""
    return {"logs": list(_tool_logs)[-min(max(limit, 1), TOOL_LOG_MAX):]}


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
async def ask_user_pending():
    """当前挂起的人类确认请求列表（桥接层轮询用）。"""
    from tools.ask_user import get_pending_asks
    return {"pending": get_pending_asks()}


class ConversationLogBody(BaseModel):
    role: str = "assistant"   # assistant | user
    content: str = ""
    session_id: Optional[str] = None


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
    result = answer_ask(body.request_id, body.answer)
    if "error" in result:
        raise ApiError(ErrorCode.NOT_FOUND, result["error"], 404)
    return result
