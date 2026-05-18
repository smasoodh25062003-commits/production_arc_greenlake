"""Feedback submission and admin APIs."""
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field

from app.auth.session import read_session
from app.auth.users import role_gte
from app.feedback.logger import (
    save_feedback,
    get_recent_feedback,
    update_feedback_status,
    VALID_STATUSES,
)

router = APIRouter()


class FeedbackSubmit(BaseModel):
    category: str = Field(default="other", max_length=32)
    message: str = Field(min_length=1, max_length=5000)
    page_url: Optional[str] = Field(default=None, max_length=2000)
    source: str = Field(default="platform-tools", max_length=64)


@router.get("/whoami")
async def feedback_whoami(request: Request):
    """Used by Platform Tools to show admin-only Mentors cards."""
    user = read_session(request)
    if not user:
        return {"authenticated": False, "is_admin": False, "role": None}
    return {
        "authenticated": True,
        "is_admin": role_gte(user.get("role", "viewer"), "admin"),
        "role": user.get("role"),
        "display_name": user.get("display_name"),
    }


@router.post("/submit")
async def submit_feedback(request: Request, body: FeedbackSubmit):
    user = read_session(request)
    msg = body.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message is required.")
    fid = save_feedback(
        message=msg,
        category=body.category,
        source=body.source,
        page_url=body.page_url or str(request.headers.get("referer", "")),
        user=user,
    )
    return {"ok": True, "id": fid}


@router.get("/list")
async def list_feedback(request: Request, status: Optional[str] = None, limit: int = 500):
    user = read_session(request)
    if not user or not role_gte(user.get("role", "viewer"), "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    items = get_recent_feedback(limit=min(limit, 1000), status=status)
    return {"items": items}


@router.patch("/{feedback_id}/status")
async def patch_feedback_status(
    request: Request, feedback_id: int, status: str
):
    user = read_session(request)
    if not user or not role_gte(user.get("role", "viewer"), "admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status.")
    if not update_feedback_status(feedback_id, status):
        raise HTTPException(status_code=404, detail="Feedback not found.")
    return {"ok": True}
