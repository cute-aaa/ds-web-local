"""审批队列 API：查看 / 批准 / 拒绝挂起的工具调用审批（审计写入 data/approvals.log）。"""
from fastapi import APIRouter

from core.logger import get_logger
from tools.approvals import get_approval_manager

router = APIRouter(prefix="/api/approvals", tags=["approvals"])
logger = get_logger("api.approvals")


@router.get("")
async def list_approvals():
    """全部审批记录（含已决）。"""
    return {"approvals": get_approval_manager().list()}


@router.get("/{approval_id}")
async def get_approval(approval_id: str):
    """单条审批记录。"""
    record = get_approval_manager().get(approval_id)
    if record is None:
        return {"error": f"审批记录不存在: {approval_id}"}
    return record


@router.post("/{approval_id}/approve")
async def approve(approval_id: str):
    """批准：标记 approved 并记录日志（前端随后重试原工具调用）。"""
    return get_approval_manager().approve(approval_id)


@router.post("/{approval_id}/reject")
async def reject(approval_id: str):
    """拒绝：标记 rejected 并记录日志。"""
    return get_approval_manager().reject(approval_id)
