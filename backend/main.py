"""后端入口：加载配置 → 建 app → uvicorn。"""
import os
import sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from core.logger import setup_logger, get_logger
from core.config import get_config, LOG_DIR
from core.errors import register_exception_handlers

log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：注册内置工具 + 启动 auto_start MCP 服务
    from tools.register import register_all_builtin_tools
    register_all_builtin_tools()
    from mcp_services.manager import get_manager
    mgr = get_manager()
    await mgr.start_all_auto()
    await mgr.start_monitor()  # 健康监控 + 重连状态机（30s 轮询）
    # 启动时清理结果外置临时目录（data/tmp）
    from core.config import DATA_DIR
    _tmp = DATA_DIR / "tmp"
    if _tmp.exists():
        for f in _tmp.glob("result_*.json"):
            try:
                f.unlink()
            except OSError:
                pass
        log.info("已清理结果外置临时目录")
    # 本地监听线程：技能目录变更 + 配置热重载自动感知
    from core.watcher import start_watchers
    start_watchers()
    log.info("系统初始化完成")
    yield
    # 关闭：停止监听 + 停止监控 + 停止所有 MCP 服务
    from core.watcher import stop_watchers
    await stop_watchers()
    await mgr.stop_monitor()
    for name in list(mgr.clients.keys()):
        await mgr.stop(name)
    log.info("系统已关闭")


def create_app() -> FastAPI:
    config = get_config()
    server_cfg = config.get_server_config()

    app = FastAPI(
        title=server_cfg.get("title", "DS Web Local"),
        description=server_cfg.get("description", "可插拔的本地 Agent 能力中台"),
        version=server_cfg.get("version", "3.0.0"),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    # 安全（认证 + 限流）
    from core.security import install_security
    install_security(app)

    # 请求耗时 / 指标打点
    from core.metrics import get_metrics
    metrics = get_metrics()

    @app.middleware("http")
    async def metrics_middleware(request, call_next):
        metrics.request_count += 1
        metrics.request_by_path[request.url.path] += 1
        import time
        t0 = time.time()
        response = await call_next(request)
        response.headers["X-Process-Time"] = f"{time.time() - t0:.4f}"
        return response

    # 挂载路由
    from api import bridge, skills, mcp, admin, sessions, credentials, approvals
    app.include_router(bridge.router)
    app.include_router(skills.router)
    app.include_router(mcp.router)
    app.include_router(admin.router)
    app.include_router(sessions.router)
    app.include_router(credentials.router)
    app.include_router(approvals.router)

    # 托管前端构建产物（如 console/dist 已构建，则经 /console 访问）
    from pathlib import Path
    _dist = Path(__file__).resolve().parent.parent / "console" / "dist"
    if _dist.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/console", StaticFiles(directory=str(_dist), html=True), name="console")

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": server_cfg.get("version", "3.0.0")}

    @app.get("/")
    async def root():
        return {
            "service": server_cfg.get("title", "DS Web Local"),
            "version": server_cfg.get("version", "3.0.0"),
            "docs": "/docs",
        }

    return app


if __name__ == "__main__":
    config = get_config()
    settings = config.get_settings("logging", {})
    log_file = settings.get("file", "logs/app.log")
    if not os.path.isabs(log_file):
        log_file = str(LOG_DIR / os.path.basename(log_file))

    setup_logger(
        level=settings.get("level", "INFO"),
        log_file=log_file,
        rotation=settings.get("rotation", "10 MB"),
        retention=settings.get("retention", "30 days"),
    )

    app = create_app()
    server_cfg = config.get_server_config()
    host = server_cfg.get("host", "0.0.0.0")
    port = int(server_cfg.get("port", 8088))
    log.info(f"启动 DS Web Local 于 http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
