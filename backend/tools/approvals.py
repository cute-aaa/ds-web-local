"""审批队列：ApprovalRequired 挂起的工具调用记录 + data/approvals.log 审计。"""
import json
import time
import uuid
from typing import Any, Dict, List, Optional

from core.config import DATA_DIR
from core.logger import get_logger

logger = get_logger("tools.approvals")

LOG_FILE = DATA_DIR / "approvals.log"


class ApprovalManager:
    """审批记录管理器（进程内单例）：create/list/approve/reject。"""

    def __init__(self) -> None:
        self._records: Dict[str, Dict] = {}

    def create(self, tool: str, arguments: Dict) -> Dict:
        """为需要审批的工具调用创建挂起记录。"""
        record = {
            "id": uuid.uuid4().hex[:12],
            "tool": tool,
            "arguments": arguments,
            "created_at": time.time(),
            "status": "pending",  # pending | approved | rejected
            "decided_at": None,
        }
        self._records[record["id"]] = record
        self._log("pending", record)
        logger.info(f"审批请求 #{record['id']} tool={tool} 进入等待队列")
        return record

    def list(self) -> List[Dict]:
        return [dict(r) for r in self._records.values()]

    def get(self, record_id: str) -> Optional[Dict]:
        r = self._records.get(record_id)
        return dict(r) if r else None

    def approve(self, record_id: str) -> Dict:
        return self._decide(record_id, "approved")

    def reject(self, record_id: str) -> Dict:
        return self._decide(record_id, "rejected")

    def is_approved(self, tool: str, arguments: Dict) -> bool:
        """该 (tool, arguments) 是否已有批准的记录（批准后重试可直接放行）。"""
        for r in self._records.values():
            if r["status"] == "approved" and r["tool"] == tool and r["arguments"] == arguments:
                return True
        return False

    def _decide(self, record_id: str, status: str) -> Dict:
        r = self._records.get(record_id)
        if r is None:
            return {"error": f"审批记录不存在: {record_id}"}
        if r["status"] != "pending":
            return {"error": f"审批记录 {record_id} 状态为 {r['status']}，不能重复审批"}
        r["status"] = status
        r["decided_at"] = time.time()
        self._log(status, r)
        logger.info(f"审批 #{record_id} → {status}")
        return dict(r)

    def _log(self, action: str, record: Dict) -> None:
        """追加审计日志（data/approvals.log，一行一条 JSON）。"""
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            entry = {"action": action, "time": time.time(), "record": record}
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"审批日志写入失败: {e}")


_approval_manager: Optional[ApprovalManager] = None


def get_approval_manager() -> ApprovalManager:
    global _approval_manager
    if _approval_manager is None:
        _approval_manager = ApprovalManager()
    return _approval_manager
