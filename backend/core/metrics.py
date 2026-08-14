"""简单内存指标（请求计数 / 工具调用 / 错误）。"""
import time
from collections import defaultdict


class Metrics:
    def __init__(self):
        self.start_time = time.time()
        self.request_count = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.mcp_connects = 0
        self.request_by_path = defaultdict(int)

    def uptime(self) -> float:
        return time.time() - self.start_time

    def snapshot(self) -> dict:
        return {
            "uptime_seconds": round(self.uptime(), 1),
            "request_count": self.request_count,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "mcp_connects": self.mcp_connects,
            "requests_by_path": dict(self.request_by_path),
        }


_metrics = Metrics()


def get_metrics() -> Metrics:
    return _metrics
