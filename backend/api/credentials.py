"""凭据管理 API：只暴露引用与状态（describe），永不返回凭据值。"""
from fastapi import APIRouter
from pydantic import BaseModel

from core.errors import ApiError, ErrorCode
from core.logger import get_logger
from credentials.store import get_store

router = APIRouter(prefix="/api/credentials", tags=["credentials"])
logger = get_logger("api.credentials")


class CredentialPutBody(BaseModel):
    """写入请求体：value 为明文值（仅写入时传输，读取侧永远拿不到）。"""

    value: str


@router.get("")
async def list_credentials():
    """列出全部凭据引用及状态（describe 列表，永远不含值）。"""
    store = get_store()
    refs = [{"ref": r, **store.describe(r)} for r in store.list_refs()]
    return {"refs": refs}


@router.put("/{ref}")
async def set_credential(ref: str, body: CredentialPutBody):
    """写入凭据值到 .credentials.yaml；被环境变量遮蔽（只读）时 409。"""
    store = get_store()
    ok = store.set(ref, body.value)
    if not ok:
        raise ApiError(
            ErrorCode.CONFLICT,
            f"凭据 '{ref}' 被环境变量遮蔽（只读），无法写入",
            409,
        )
    return {"status": "success", "ref": ref, **store.describe(ref)}


@router.delete("/{ref}")
async def unset_credential(ref: str):
    """删除文件中的凭据条目；无条目时 no-op。"""
    store = get_store()
    store.unset(ref)
    return {"status": "success", "ref": ref}
