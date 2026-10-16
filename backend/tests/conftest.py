"""测试隔离：所有测试使用临时配置/数据目录。

在 import 任何 backend 模块之前设置 DSW_CONFIG_DIR / DSW_DATA_DIR：
- 不连接真实 mcp.json 里的 MCP 服务（echo/windows-mcp），避免 TestClient
  关闭时 mcp SDK stdio cancel scope 跨任务问题（CancelledError）
- 不污染真实 data/（user-skills、sessions.db、.credentials.yaml 等）
- 测试更快（不启动 windows-mcp）
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TEST_CFG = tempfile.mkdtemp(prefix="dsw-test-cfg-")
_TEST_DATA = tempfile.mkdtemp(prefix="dsw-test-data-")
# 预置空配置文件：ConfigLoader 首次运行会从 *.example.json 生成默认配置（含 windows-mcp），
# 这里显式写成空，避免测试隔离目录被播种出真实 MCP 服务。
with open(os.path.join(_TEST_CFG, "mcp.json"), "w", encoding="utf-8") as _f:
    _f.write('{"services": {}}')
with open(os.path.join(_TEST_CFG, "skills.json"), "w", encoding="utf-8") as _f:
    _f.write("{}")
os.environ["DSW_CONFIG_DIR"] = _TEST_CFG
os.environ["DSW_DATA_DIR"] = _TEST_DATA
