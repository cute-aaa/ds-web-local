"""安全：Bearer Token 认证（可开关）+ 令牌桶限流。"""
import time
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse

from core.config import get_config
from core.logger import get_logger

logger = get_logger("security")

# 令牌桶：ip -> {"tokens": float, "last": float}
_buckets = defaultdict(lambda: {"tokens": 60.0, "last": time.time()})


def install_security(app):
    """安装认证中间件 + 限流（按 settings.security 配置）。"""
    config = get_config()
    sec = config.get_settings("security", {})
    auth_enabled = bool(sec.get("auth_enabled", False))
    auth_token = sec.get("auth_token", "")
    rate_limit_s = str(sec.get("rate_limit", "60/minute"))

    try:
        limit, window = rate_limit_s.split("/")
        limit = float(limit)
        window = float(window.replace("minute", "60").replace("second", "1").replace("hour", "3600"))
    except Exception:
        limit, window = 60.0, 60.0

    PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        path = request.url.path

        # 认证（跳过公开路径）
        if auth_enabled and auth_token and path not in PUBLIC_PATHS and not path.startswith("/docs"):
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {auth_token}":
                return JSONResponse(status_code=401, content={
                    "error": {"code": "UNAUTHORIZED", "message": "未授权，请提供有效 Token"}})

        # 限流（仅限 /api 路径）
        if path.startswith("/api"):
            ip = request.client.host if request.client else "unknown"
            bucket = _buckets[ip]
            now = time.time()
            bucket["tokens"] = min(limit, bucket["tokens"] + (now - bucket["last"]) * (limit / window))
            bucket["last"] = now
            if bucket["tokens"] < 1:
                return JSONResponse(status_code=429, content={
                    "error": {"code": "RATE_LIMITED", "message": "请求过于频繁，请稍后再试"}})
            bucket["tokens"] -= 1

        return await call_next(request)

    logger.info(f"安全中间件已安装（认证={'开' if auth_enabled else '关'}，限流={rate_limit_s}）")
