"""Multi-tenant reverse proxying and access fencing for OpenHands Agent Server conversations."""
import json
import time
from fastapi import APIRouter, HTTPException, Request, Response
import httpx

from gateway.core import (
    AGENT_SERVER_URL,
    SESSION_REGISTRY_DIR,
    logger,
    get_session_api_key,
    _get_conversation_owner,
)
from gateway.auth import get_authenticated_user

router = APIRouter(tags=["proxy"])


@router.get("/api/conversations/search")
async def search_conversations(request: Request):
    user_id = get_authenticated_user(request)
    api_key = get_session_api_key()
    headers = {"X-Session-API-Key": api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{AGENT_SERVER_URL}/api/conversations/search",
                params=request.query_params,
                headers=headers
            )
            if resp.status_code != 200:
                return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type"))
            data = resp.json()
        except Exception as e:
            logger.error(f"Error fetching conversation search: {e}")
            raise HTTPException(status_code=502, detail="Agent server unreachable")

    if not user_id:
        return {"items": [], "next_page_id": None}

    items = data.get("items", [])
    filtered_items = []
    for item in items:
        cid = item.get("id") or item.get("conversation_id")
        if cid:
            owner = _get_conversation_owner(cid)
            if owner == str(user_id):
                filtered_items.append(item)

    data["items"] = filtered_items
    return data


@router.get("/api/conversations")
async def list_conversations(request: Request):
    user_id = get_authenticated_user(request)
    api_key = get_session_api_key()
    headers = {"X-Session-API-Key": api_key}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{AGENT_SERVER_URL}/api/conversations",
                params=request.query_params,
                headers=headers
            )
            if resp.status_code != 200:
                return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type"))
            data = resp.json()
        except Exception as e:
            logger.error(f"Error listing conversations: {e}")
            raise HTTPException(status_code=502, detail="Agent server unreachable")

    if not user_id:
        return [] if isinstance(data, list) else {"items": []}

    if isinstance(data, list):
        return [c for c in data if _get_conversation_owner(c.get("id")) == str(user_id)]
    elif isinstance(data, dict) and "items" in data:
        data["items"] = [c for c in data["items"] if _get_conversation_owner(c.get("id")) == str(user_id)]
        return data
    return data


@router.api_route("/api/conversations/{conversation_id}", methods=["GET", "DELETE", "PATCH", "PUT"])
async def handle_single_conversation(conversation_id: str, request: Request):
    user_id = get_authenticated_user(request)
    owner = _get_conversation_owner(conversation_id)
    if owner and user_id and owner != str(user_id):
        logger.warning(f"Forbidden access: User {user_id} tried to access conversation {conversation_id} owned by {owner}")
        raise HTTPException(status_code=403, detail="Access denied: Conversation belongs to another user")

    api_key = get_session_api_key()
    headers = {"X-Session-API-Key": api_key}
    body = await request.body()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=f"{AGENT_SERVER_URL}/api/conversations/{conversation_id}",
                params=request.query_params,
                headers=headers,
                content=body if body else None
            )
            return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type"))
        except httpx.RequestError as exc:
            logger.error(f"Failed to forward to agent-server: {exc}")
            raise HTTPException(status_code=502, detail="Agent server unreachable")


@router.api_route("/api/conversations/{conversation_id}/{subpath:path}", methods=["GET", "POST", "DELETE", "PATCH", "PUT"])
async def handle_conversation_subpath(conversation_id: str, subpath: str, request: Request):
    user_id = get_authenticated_user(request)
    owner = _get_conversation_owner(conversation_id)
    if owner and user_id and owner != str(user_id):
        logger.warning(f"Forbidden access: User {user_id} tried to access conversation subpath {conversation_id}/{subpath} owned by {owner}")
        raise HTTPException(status_code=403, detail="Access denied: Conversation belongs to another user")

    api_key = get_session_api_key()
    headers = {"X-Session-API-Key": api_key}
    body = await request.body()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.request(
                method=request.method,
                url=f"{AGENT_SERVER_URL}/api/conversations/{conversation_id}/{subpath}",
                params=request.query_params,
                headers=headers,
                content=body if body else None
            )
            return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type"))
        except httpx.RequestError as exc:
            logger.error(f"Failed to forward subpath to agent-server: {exc}")
            raise HTTPException(status_code=502, detail="Agent server unreachable")


@router.post("/api/conversations")
async def create_agent_conversation_proxy(request: Request):
    user_id = get_authenticated_user(request)
    api_key = get_session_api_key()
    headers = {"X-Session-API-Key": api_key, "Content-Type": "application/json"}
    body = await request.body()
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                f"{AGENT_SERVER_URL}/api/conversations",
                content=body,
                headers=headers,
            )
            if resp.status_code < 400:
                data = resp.json()
                cid = data.get("id")
                if cid and user_id:
                    SESSION_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
                    session_file = SESSION_REGISTRY_DIR / f"{cid}.json"
                    session_file.write_text(json.dumps({
                        "conversation_id": cid,
                        "user_id": str(user_id),
                        "created_at": time.time()
                    }))
                return data
            return Response(content=resp.content, status_code=resp.status_code, media_type=resp.headers.get("content-type"))
        except httpx.RequestError as exc:
            logger.error(f"Failed to proxy conversation creation: {exc}")
            raise HTTPException(status_code=502, detail="Agent server unreachable")
