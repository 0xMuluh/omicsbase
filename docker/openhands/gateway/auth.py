"""Authentication, ticket verification, and identity extraction for OmicsBase Gateway."""
import os
from typing import Optional
from fastapi import HTTPException, Request
from pydantic import BaseModel
import jwt

from gateway.core import AUTH_SECRET, logger


class TicketRequest(BaseModel):
    ticket: Optional[str] = None
    conversation_id: Optional[str] = None
    agent_engine: Optional[str] = "openhands"
    model: Optional[str] = None
    theme: Optional[str] = "dark"


class SaveEditorFileRequest(BaseModel):
    content: str


def verify_ticket(ticket: str) -> dict:
    if not AUTH_SECRET:
        raise HTTPException(status_code=503, detail="OMICSBASE_AUTH_SECRET is not configured")
    try:
        claims = jwt.decode(
            ticket,
            AUTH_SECRET,
            algorithms=["HS256"],
            audience="omicsbase-openhands",
            issuer="librechat",
        )
        return claims
    except Exception as e:
        logger.warning(f"Ticket verification failed: {e}")
        raise HTTPException(status_code=403, detail="Invalid or expired ticket")


def get_authenticated_user(request: Request, authorization: Optional[str] = None) -> Optional[str]:
    # 1. Bearer ticket from Authorization header
    auth_hdr = authorization or request.headers.get("Authorization")
    if auth_hdr and auth_hdr.startswith("Bearer "):
        token = auth_hdr.replace("Bearer ", "").strip()
        try:
            claims = verify_ticket(token)
            if claims.get("sub"):
                return str(claims.get("sub"))
        except Exception:
            pass

    # 2. Signed user token cookie
    user_token = request.cookies.get("omicsbase_user_token")
    if user_token and AUTH_SECRET:
        try:
            claims = jwt.decode(user_token, AUTH_SECRET, algorithms=["HS256"])
            if claims.get("sub"):
                return str(claims.get("sub"))
        except Exception:
            pass

    # 3. User ID cookie fallback
    user_id_cookie = request.cookies.get("omicsbase_user_id")
    if user_id_cookie:
        return str(user_id_cookie)

    # 4. Query param ticket
    q_ticket = request.query_params.get("ticket")
    if q_ticket:
        try:
            claims = verify_ticket(q_ticket)
            if claims.get("sub"):
                return str(claims.get("sub"))
        except Exception:
            pass

    return None
