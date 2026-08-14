@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==========================================
echo   DS Web Local 本地 Agent 能力中台
echo ==========================================
echo.
echo   后端:     http://localhost:8088
echo   控制台:   http://localhost:8088/console
echo   API 文档: http://localhost:8088/docs
echo.

rem 使用本地 Python（若依赖缺失会在下方给出安装提示）
set "PY=python"
echo 使用解释器: %PY%
echo.

rem 依赖完整性检查（缺依赖时给出安装指引而不是闪退）
"%PY%" -c "import fastapi, uvicorn, mcp, loguru, pydantic, yaml, httpx" >nul 2>&1
if errorlevel 1 (
    echo [提示] 缺少依赖，请先安装：
    echo.
    echo   %PY% -m pip install -r backend\requirements.txt
    echo.
    pause
    exit /b 1
)

echo 正在启动后端（Ctrl+C 停止）...
echo.
"%PY%" backend\main.py
if errorlevel 1 (
    echo.
    echo 后端已停止（若是手动关闭请忽略；异常退出请查看上方日志）。
    echo.
    pause
)
endlocal
