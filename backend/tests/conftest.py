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
os.environ["DSW_CONFIG_DIR"] = _TEST_CFG
os.environ["DSW_DATA_DIR"] = _TEST_DATA
