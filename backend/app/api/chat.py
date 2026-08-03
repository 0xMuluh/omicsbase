"""Pre-project chat API — answer first, create a study only when intended."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_tenant, get_current_user_id
from app.database import get_db

router = APIRouter(prefix="/api/chat", tags=["chat"])


class HomeChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    project_id: str | None = Field(default=None)


def _ndjson(event: dict) -> str:
    return json.dumps(event, default=str) + "\n"


@router.post("/home")
async def home_chat(
    data: HomeChatRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
    user_id: str = Depends(get_current_user_id),
):
    """Stream a landing-page reply or a start_study decision, creating/updating a persistent project entry."""
    from app.services.home_agent import stream_home_chat

    async def event_stream():
        async for event in stream_home_chat(
            message=data.message.strip(),
            db=db,
            tenant_id=tenant_id,
            user_id=user_id,
            project_id=data.project_id,
        ):
            yield _ndjson(event)

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")

