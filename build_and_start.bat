@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] 安装后端依赖...
cd backend
python -m pip install -r requirements.txt -q
cd ..

echo [2/3] 构建前端...
cd console
call npm install -q
call npm run build
cd ..

echo [3/3] 启动服务...
python backend\main.py
