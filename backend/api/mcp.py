"""MCP 服务器管理 API（契约 B）。"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.logger import get_logger
from core.errors import ApiError, ErrorCode
from core.config import get_config
from mcp_services.manager import get_manager

router = APIRouter(prefix="/api/mcp", tags=["mcp"])
logger = get_logger("api.mcp")


class MCPBody(BaseModel):
    name: str = ""
    transport: str = "stdio"
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    auto_start: bool = False
    timeout: int = 120
    description: str = ""


def _validate(body: MCPBody):
    if body.transport == "stdio" and not body.command:
        raise ApiError(ErrorCode.BAD_REQUEST, "stdio transport 需要 command")
    if body.transport in ("sse", "streamable-http", "http") and not body.url:
        raise ApiError(ErrorCode.BAD_REQUEST, f"{body.transport} transport 需要 url")


def _cfg(body: MCPBody) -> dict:
    d = body.model_dump()
    d.pop("name", None)
    d.pop("status", None)
    return d


@router.get("")
async def list_mcp():
    config = get_config()
    manager = get_manager()
    status = manager.get_status()
    out = []
    for name, cfg in config.get_all_services().items():
        item = dict(cfg)
        item["name"] = name
        item["status"] = status.get(name, "stopped")
        item["state"] = manager.get_state(name)
        item["tools"] = [t.get("name") for t in manager.list_server_tools(name)]
        out.append(item)
    return {"services": out}


@router.post("")
async def add_mcp(body: MCPBody):
    if not body.name:
        raise ApiError(ErrorCode.BAD_REQUEST, "缺少服务名 name")
    _validate(body)
    config = get_config()
    if body.name in config.get_all_services():
        raise ApiError(ErrorCode.CONFLICT, f"服务 '{body.name}' 已存在", 409)
    cfg = _cfg(body)
    services = config.get_all_services()
    services[body.name] = cfg
    config.save_services(services)
    manager = get_manager()
    manager.register(body.name, cfg)
    if body.auto_start:
        await manager.start(body.name)
    return {"status": "success", "name": body.name}


@router.post("/reload-all")
async def reload_all():
    """重载全部已配置的 MCP 服务（配置热重载 + 重启，供桥接面板「重载 MCP」按钮）。"""
    from mcp_services.manager import get_manager
    mgr = get_manager()
    results = {}
    for name, cfg in get_config().get_all_services().items():
        try:
            await mgr.reload(name, cfg)
            results[name] = "reloaded"
        except Exception as e:
            results[name] = f"error: {e}"
    return {"status": "ok", "results": results}


@router.get("/{name}")
async def get_mcp(name: str):
    config = get_config()
    cfg = config.get_service_config(name)
    if not cfg:
        raise ApiError(ErrorCode.NOT_FOUND, f"服务 '{name}' 不存在", 404)
    manager = get_manager()
    return {"name": name, "config": cfg,
            "status": manager.get_status().get(name, "stopped"),
            "state": manager.get_state(name),
            "tools": manager.list_server_tools(name)}


@router.put("/{name}")
async def update_mcp(name: str, body: MCPBody):
    config = get_config()
    if name not in config.get_all_services():
        raise ApiError(ErrorCode.NOT_FOUND, f"服务 '{name}' 不存在", 404)
    _validate(body)
    cfg = _cfg(body)
    services = config.get_all_services()
    services[name] = cfg
    config.save_services(services)
    manager = get_manager()
    # 热重载：仅 transport/command/url/env 等变化才 stop+start，其余字段热更新
    await manager.reload(name, cfg)
    return {"status": "success", "name": name}


@router.delete("/{name}")
async def delete_mcp(name: str):
    config = get_config()
    if name not in config.get_all_services():
        raise ApiError(ErrorCode.NOT_FOUND, f"服务 '{name}' 不存在", 404)
    services = config.get_all_services()
    del services[name]
    config.save_services(services)
    manager = get_manager()
    await manager.remove(name)
    return {"status": "success", "name": name}


@router.post("/{name}/start")
async def start_mcp(name: str):
    config = get_config()
    cfg = config.get_service_config(name)
    if not cfg:
        raise ApiError(ErrorCode.NOT_FOUND, f"服务 '{name}' 不存在", 404)
    manager = get_manager()
    manager.register(name, cfg)
    ok = await manager.start(name)
    return {"status": "success" if ok else "failed", "name": name}


@router.post("/{name}/stop")
async def stop_mcp(name: str):
    manager = get_manager()
    await manager.stop(name)
    return {"status": "success", "name": name}


@router.post("/{name}/restart")
async def restart_mcp(name: str):
    config = get_config()
    cfg = config.get_service_config(name)
    if not cfg:
        raise ApiError(ErrorCode.NOT_FOUND, f"服务 '{name}' 不存在", 404)
    manager = get_manager()
    manager.register(name, cfg)
    ok = await manager.restart(name)
    return {"status": "success" if ok else "failed", "name": name}


@router.get("/{name}/tools")
async def list_mcp_tools(name: str):
    manager = get_manager()
    return {"name": name, "tools": manager.list_server_tools(name)}
