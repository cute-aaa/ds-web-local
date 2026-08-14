"""管理 API：系统状态 / 日志 / 指标。"""
import os
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from core.logger import get_logger
from core.config import get_config, LOG_DIR
from mcp_services.manager import get_manager
from tools.registry import get_registry

router = APIRouter(prefix="/api/admin", tags=["admin"])
logger = get_logger("api.admin")


@router.get("/status")
async def status():
    manager = get_manager()
    registry = get_registry()
    config = get_config()
    from skills.discovery import get_discovery
    return {
        "server": config.get_server_config(),
        "services": manager.get_status(),
        "builtin_tools": len(registry.list_builtin_tools()),
        "mcp_tools": len(manager.tools),
        "skills": len(get_discovery().snapshot()["skills"]),
    }


@router.get("/metrics")
async def metrics():
    """Prometheus 风格文本指标（简单版）。"""
    from core.metrics import get_metrics
    m = get_metrics().snapshot()
    lines = [
        "# HELP ds_web_local_uptime_seconds 运行时长",
        "# TYPE ds_web_local_uptime_seconds gauge",
        f"ds_web_local_uptime_seconds {m['uptime_seconds']}",
        "# TYPE ds_web_local_request_count counter",
        f"ds_web_local_request_count {m['request_count']}",
        "# TYPE ds_web_local_tool_calls counter",
        f"ds_web_local_tool_calls {m['tool_calls']}",
        "# TYPE ds_web_local_tool_errors counter",
        f"ds_web_local_tool_errors {m['tool_errors']}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@router.get("/logs")
async def logs(lines: int = 100):
    """读取应用日志尾部。"""
    try:
        log_file = LOG_DIR / "app.log"
        if not log_file.exists():
            return {"logs": "", "note": "暂无日志"}
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        tail = "\\n".join(content.splitlines()[-lines:])
        return {"logs": tail, "lines": min(lines, len(content.splitlines()))}
    except Exception as e:
        return {"error": str(e)}
