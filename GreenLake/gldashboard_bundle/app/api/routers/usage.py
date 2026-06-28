"""Platform Tools usage logging — open submit, admin-only read."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.session import read_session
from app.auth.users import role_gte
from app.usage.logger import get_recent_usage, get_usage_stats, save_usage_event

router = APIRouter()


class UsageEventSubmit(BaseModel):
    visitor_id: str = Field(min_length=8, max_length=64)
    action: str = Field(default="page_view", max_length=64)
    tool: str = Field(default="unknown", max_length=64)
    user_label: Optional[str] = Field(default="", max_length=128)
    page: Optional[str] = Field(default=None, max_length=512)
    detail: Optional[str] = Field(default=None, max_length=200)


@router.post("/event")
async def submit_usage_event(request: Request, body: UsageEventSubmit):
    """Log a tool usage event. No login required."""
    user = read_session(request)
    session_user = None
    if user:
        session_user = user.get("username") or user.get("display_name")

    eid = save_usage_event(
        visitor_id=body.visitor_id.strip(),
        action=body.action.strip(),
        tool=body.tool.strip(),
        user_label=(body.user_label or "").strip(),
        session_user=session_user,
        page=body.page or str(request.headers.get("referer", ""))[:512],
        detail=body.detail,
    )
    return {"ok": True, "id": eid}


@router.get("/list")
async def list_usage(
    request: Request,
    limit: int = 500,
    tool: Optional[str] = None,
    visitor_id: Optional[str] = None,
):
    user = read_session(request)
    if not user or not role_gte(user.get("role", "viewer"), "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    items = get_recent_usage(
        limit=min(limit, 2000),
        tool=tool,
        visitor_id=visitor_id,
    )
    return {"items": items}


@router.get("/stats")
async def usage_stats(request: Request):
    user = read_session(request)
    if not user or not role_gte(user.get("role", "viewer"), "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return get_usage_stats()
