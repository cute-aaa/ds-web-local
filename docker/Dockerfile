FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# 复制后端 + 前端构建产物
COPY backend/ ./backend/
COPY console/dist/ ./console/dist/

WORKDIR /app/backend
EXPOSE 8024

CMD ["python", "main.py"]
