from __future__ import annotations

import json
import os
import secrets
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt
from pydantic import BaseModel

from app.config import SECRET_KEY
from app.database import get_db
from app.deps import get_current_user

router = APIRouter(prefix="/api/google", tags=["google"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPES = "openid email https://www.googleapis.com/auth/calendar.events"
ROOT = Path(__file__).resolve().parents[2]


class MeetInviteBody(BaseModel):
    target_user_id: str


class CallDecisionBody(BaseModel):
    decision: str


def _oauth_config(request: Optional[Request] = None) -> dict:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        path = Path(os.getenv("GOOGLE_CLIENT_SECRETS_FILE", ROOT / ".secrets" / "google-oauth.json"))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            web = payload["web"]
            client_id = web["client_id"]
            client_secret = web["client_secret"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail="Google calling is not configured on this server.") from exc
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "").strip()
    if not redirect_uri and request is not None:
        redirect_uri = str(request.base_url).rstrip("/") + "/api/google/callback"
    return {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}


def _state_for(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "purpose": "google-oauth", "nonce": secrets.token_urlsafe(16), "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        SECRET_KEY,
        algorithm="HS256",
    )


def _token_cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt_token(token: str) -> str:
    return _token_cipher().encrypt(token.encode("utf-8")).decode("ascii")


def _decrypt_token(token: str) -> str:
    try:
        return _token_cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Reconnect your Google account before calling.") from exc


def _state_user(state: str) -> str:
    try:
        payload = jwt.decode(state, SECRET_KEY, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired Google connection request.") from exc
    if payload.get("purpose") != "google-oauth" or not payload.get("sub"):
        raise HTTPException(status_code=400, detail="Invalid Google connection request.")
    return str(payload["sub"])


@router.get("/status")
async def google_status(user=Depends(get_current_user)):
    configured = True
    try:
        _oauth_config()
    except HTTPException:
        configured = False
    return {
        "configured": configured,
        "connected": bool(user.get("google_oauth", {}).get("refresh_token")),
        "email": user.get("google_oauth", {}).get("email"),
    }


@router.post("/connect")
async def google_connect(request: Request, user=Depends(get_current_user)):
    config = _oauth_config(request)
    params = {
        "client_id": config["client_id"], "redirect_uri": config["redirect_uri"], "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
        "state": _state_for(str(user["_id"])),
    }
    return {"authorization_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}


@router.get("/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse(url="/?google=denied")
    user_id = _state_user(state)
    config = _oauth_config(request)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code, "client_id": config["client_id"], "client_secret": config["client_secret"],
            "redirect_uri": config["redirect_uri"], "grant_type": "authorization_code",
        })
    if response.status_code >= 400:
        return RedirectResponse(url="/?google=failed")
    token = response.json()
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        return RedirectResponse(url="/?google=failed")
    claims = jwt.get_unverified_claims(token.get("id_token", "")) if token.get("id_token") else {}
    db = get_db()
    await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"google_oauth": {
        "refresh_token": _encrypt_token(refresh_token), "email": claims.get("email"), "connected_at": datetime.now(timezone.utc)
    }}})
    return RedirectResponse(url="/?google=connected")


async def _access_token(refresh_token: str, config: dict) -> str:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id": config["client_id"], "client_secret": config["client_secret"],
            "refresh_token": refresh_token, "grant_type": "refresh_token",
        })
    if response.status_code >= 400 or not response.json().get("access_token"):
        raise HTTPException(status_code=502, detail="Google authorization expired. Reconnect your Google account.")
    return response.json()["access_token"]


@router.post("/meet-invite")
async def create_meet_invite(body: MeetInviteBody, request: Request, user=Depends(get_current_user)):
    try:
        target_oid = ObjectId(body.target_user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid team member.")
    db = get_db()
    target = await db.users.find_one({"_id": target_oid})
    if not target or not target.get("gmail"):
        raise HTTPException(status_code=404, detail="That team member has no Gmail address.")
    if user.get("role") != "admin" and target.get("department_id") != user.get("department_id"):
        raise HTTPException(status_code=403, detail="You can only call members of your department.")
    oauth = user.get("google_oauth") or {}
    if not oauth.get("refresh_token"):
        raise HTTPException(status_code=409, detail="Connect your Google account before calling.")
    config = _oauth_config(request)
    access_token = await _access_token(_decrypt_token(oauth["refresh_token"]), config)
    start = datetime.now(timezone.utc) + timedelta(minutes=1)
    event = {
        "summary": f"RotaShift call with {target.get('full_name', target['gmail'])}",
        "description": "Google Meet call created from RotaShift.",
        "start": {"dateTime": start.isoformat()}, "end": {"dateTime": (start + timedelta(minutes=30)).isoformat()},
        "attendees": [{"email": target["gmail"]}],
        "conferenceData": {
            "createRequest": {
                "requestId": secrets.token_hex(16),
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            CALENDAR_EVENTS_URL, params={"conferenceDataVersion": "1", "sendUpdates": "all"},
            headers={"Authorization": f"Bearer {access_token}"}, json=event,
        )
    if response.status_code >= 400:
        try:
            google_message = response.json().get("error", {}).get("message")
        except (ValueError, AttributeError):
            google_message = None
        detail = google_message or "Google could not create the Meet invitation."
        raise HTTPException(status_code=502, detail=f"Google Calendar: {detail}")
    created = response.json()
    meet_url = created.get("hangoutLink")
    if not meet_url:
        raise HTTPException(status_code=502, detail="Google created the event but did not return a Meet link.")
    await db.call_notifications.insert_one({
        "caller_id": ObjectId(user["_id"]),
        "target_id": target_oid,
        "meet_url": meet_url,
        "status": "ringing",
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
    })
    return {"ok": True, "meet_url": meet_url, "recipient": target["gmail"]}


@router.get("/incoming-call")
async def incoming_call(user=Depends(get_current_user)):
    db = get_db()
    now = datetime.now(timezone.utc)
    call = await db.call_notifications.find_one(
        {"target_id": ObjectId(user["_id"]), "status": "ringing", "expires_at": {"$gt": now}},
        sort=[("created_at", -1)],
    )
    if not call:
        return {"call": None}
    caller = await db.users.find_one({"_id": call["caller_id"]}, {"full_name": 1, "employee_id": 1})
    return {"call": {
        "id": str(call["_id"]),
        "caller_name": (caller or {}).get("full_name", "Team member"),
        "caller_employee_id": (caller or {}).get("employee_id", ""),
        "created_at": call["created_at"],
    }}


@router.patch("/incoming-call/{call_id}")
async def answer_incoming_call(call_id: str, body: CallDecisionBody, user=Depends(get_current_user)):
    if body.decision not in {"accepted", "declined"}:
        raise HTTPException(status_code=400, detail="Decision must be accepted or declined.")
    try:
        call_oid = ObjectId(call_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid call.")
    db = get_db()
    call = await db.call_notifications.find_one({
        "_id": call_oid, "target_id": ObjectId(user["_id"]), "status": "ringing"
    })
    if not call:
        raise HTTPException(status_code=404, detail="This call is no longer available.")
    await db.call_notifications.update_one(
        {"_id": call_oid}, {"$set": {"status": body.decision, "answered_at": datetime.now(timezone.utc)}}
    )
    return {"ok": True, "decision": body.decision, "meet_url": call["meet_url"] if body.decision == "accepted" else None}
