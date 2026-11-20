"""Authentication, ticket verification, and identity extraction for OmicsBase Gateway."""
import time
from typing import Optional
from urllib.parse import parse_qs, urlsplit
from fastapi import HTTPException, Request
from pydantic import BaseModel
import jwt

from gateway.core import AUTH_SECRET, ROIHU, logger

USER_TOKEN_COOKIE = "omicsbase_user_token"
USER_TOKEN_SECONDS = 86400 * 7


class TicketRequest(BaseModel):
    ticket: Optional[str] = None
    conversation_id: Optional[str] = None
    agent_engine: Optional[str] = "openhands"
    model: Optional[str] = None
    theme: Optional[str] = "dark"


class SaveEditorFileRequest(BaseModel):
    content: str


def verify_ticket(ticket: str) -> dict:
    if ROIHU:
        # The job holds no OmicsBase secret. The caller already passed the session-key
        # check (only the job's owner has it); the ticket just names the report.
        try:
            return jwt.decode(ticket, options={"verify_signature": False, "verify_exp": True})
        except Exception as e:
            logger.warning(f"Ticket could not be read: {e}")
            raise HTTPException(status_code=403, detail="Invalid or expired ticket")
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
    user_token = request.cookies.get(USER_TOKEN_COOKIE)
    if user_token and AUTH_SECRET:
        try:
            claims = jwt.decode(user_token, AUTH_SECRET, algorithms=["HS256"])
            if claims.get("sub"):
                return str(claims.get("sub"))
        except Exception:
            pass

    # 3. Query param ticket
    q_ticket = request.query_params.get("ticket")
    if q_ticket:
        try:
            claims = verify_ticket(q_ticket)
            if claims.get("sub"):
                return str(claims.get("sub"))
        except Exception:
            pass

    return None


def require_user(request: Request) -> Optional[str]:
    """The signed-in user, required when one gateway serves several users.

    In a Roihu job the session-key check already limits every request to the job's
    owner, so no identity is needed there and None is returned.
    """
    if ROIHU:
        return None
    user_id = get_authenticated_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


def set_user_token(response, user_id: str, project_id: str) -> None:
    """Set the signed cookie that identifies the user on later requests."""
    if not AUTH_SECRET:
        return
    token = jwt.encode(
        {"sub": str(user_id), "project_id": str(project_id), "exp": int(time.time()) + USER_TOKEN_SECONDS},
        AUTH_SECRET,
        algorithm="HS256",
    )
    response.set_cookie(key=USER_TOKEN_COOKIE, value=token, max_age=USER_TOKEN_SECONDS,
                        path="/", samesite="lax", httponly=True)


def request_is_authorized(request: Request) -> bool:
    """Whether a request to the public OpenHands host may pass Caddy's login gate.

    Only signed credentials count: a launch ticket (Authorization header or the
    original URL's ``ticket`` query) or the signed ``omicsbase_user_token`` cookie
    set by /api/omicsbase/session.
    """
    tickets = []
    auth_hdr = request.headers.get("Authorization", "")
    if auth_hdr.startswith("Bearer "):
        tickets.append(auth_hdr[len("Bearer "):].strip())
    original_uri = request.headers.get("X-Forwarded-Uri", "")
    tickets += parse_qs(urlsplit(original_uri).query).get("ticket", [])
    for ticket in tickets:
        try:
            if verify_ticket(ticket).get("sub"):
                return True
        except HTTPException:
            pass
    user_token = request.cookies.get(USER_TOKEN_COOKIE)
    if user_token and AUTH_SECRET:
        try:
            return bool(jwt.decode(user_token, AUTH_SECRET, algorithms=["HS256"]).get("sub"))
        except jwt.PyJWTError:
            return False
    return False
