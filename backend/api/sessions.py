"""会话管理 API（SQLite 审计 + 续接）。"""
from typing import List, Optional
from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.errors import ApiError, ErrorCode
from db import session_store

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionCreateBody(BaseModel):
    title: str = ""


class MessageBody(BaseModel):
    role: str = "user"
    content: str = ""
    tool_calls: Optional[List] = None


@router.get("")
async def list_sessions():
    return {"sessions": session_store.list_sessions()}


@router.post("")
async def create(body: SessionCreateBody):
    return session_store.create_session(body.title)


@router.delete("/{sid}")
async def delete(sid: str):
    if not session_store.delete_session(sid):
        raise ApiError(ErrorCode.NOT_FOUND, "会话不存在", 404)
    return {"status": "success"}


@router.get("/{sid}/messages")
async def messages(sid: str):
    return {"messages": session_store.get_messages(sid)}


@router.get("/{sid}/events")
async def events(sid: str):
    """会话事件流回放（仅追加表，按 id 升序）。"""
    return {"events": session_store.list_events(sid)}


@router.post("/{sid}/messages")
async def add_message(sid: str, body: MessageBody):
    session_store.add_message(sid, body.role, body.content, body.tool_calls)
    return {"status": "success"}
