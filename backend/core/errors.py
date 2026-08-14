"""统一异常与错误码。"""
from typing import Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from core.logger import get_logger

logger = get_logger("errors")


class ErrorCode:
    BAD_REQUEST = "BAD_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL = "INTERNAL_ERROR"
    TIMEOUT = "TIMEOUT"
    UNAUTHORIZED = "UNAUTHORIZED"
    CONFLICT = "CONFLICT"


class ApiError(Exception):
    """业务/系统统一异常。"""
    def __init__(self, code: str, message: str, http_status: int = 400, data: Any = None):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data
        super().__init__(message)


def register_exception_handlers(app: FastAPI):
    @app.exception_handler(ApiError)
    async def _api_error_handler(request: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message, "data": exc.data}},
        )

    @app.exception_handler(Exception)
    async def _generic_handler(request: Request, exc: Exception):
        logger.exception(f"Unhandled exception: {exc}")
        return JSONResponse(
            status_code=500,
            content={"error": {"code": ErrorCode.INTERNAL, "message": "Internal server error"}},
        )
