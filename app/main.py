import json
import os
import time
import uuid

from typing import Any, Dict, List, Optional

import redis
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from pydantic import BaseModel, Field

APP_NAME = "mpc-coordinator"

# Environment
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALG = "HS256"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
MAX_PAYLOAD_BYTES = int(os.getenv("MAX_PAYLOAD_BYTES", "65536"))
XREAD_BLOCK_MS = int(os.getenv("XREAD_BLOCK_MS", "20000"))
STREAM_MAXLEN = int(os.getenv("STREAM_MAXLEN", "5000"))

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

app = FastAPI(title=APP_NAME)


# -------------------------
# Models
# -------------------------


class CreateSessionRequest(BaseModel):
    parties: int = Field(ge=2, le=100)
    meta: Optional[dict] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    parties: int
    created_at: int
    admin_token: str


class JoinRequest(BaseModel):
    party_id: str = Field(min_length=1, max_length=64)


class JoinResponse(BaseModel):
    session_id: str
    party_id: str
    party_token: str
    expected_parties: int


class SendMessageRequest(BaseModel):
    channel: str = Field(default="default", min_length=1, max_length=64)
    payload: dict
    to_party: Optional[str] = None


class PollResponse(BaseModel):
    session_id: str
    channel: str
    last_id: str
    messages: List[dict]


# -------------------------
# Helpers
# -------------------------


def now() -> int:
    return int(time.time())


def session_key(session_id: str) -> str:
    return f"session:{session_id}"


def joined_key(session_id: str) -> str:
    return f"session:{session_id}:joined"


def stream_key(session_id: str, channel: str) -> str:
    return f"session:{session_id}:chan:{channel}"


def ensure_session_exists(session_id: str) -> Dict[str, Any]:
    session = r.hgetall(session_key(session_id))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    r.expire(session_key(session_id), SESSION_TTL_SECONDS)
    r.expire(joined_key(session_id), SESSION_TTL_SECONDS)
    return session


def sign_token(claims: Dict[str, Any], exp_seconds: int) -> str:
    payload = dict(claims)
    payload["iat"] = now()
    payload["exp"] = now() + exp_seconds
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def verify_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def auth_token(auth_header: str) -> Dict[str, Any]:
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing authorization token")
    token = auth_header.strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1]
    return verify_token(token)


def encode_payload(payload: dict) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")
    return encoded


def decode_payload(raw_payload: str) -> Dict[str, Any]:
    try:
        return json.loads(raw_payload)
    except json.JSONDecodeError:
        return {"raw": raw_payload}


def build_message(mid: str, fields: Dict[str, Any], party_id: str) -> Optional[Dict[str, Any]]:
    to_party = fields.get("to")
    if to_party and to_party != party_id:
        return None
    payload = decode_payload(fields.get("payload", "{}"))
    return {"id": mid, **fields, "payload": payload}


# -------------------------
# Health
# -------------------------


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    try:
        r.ping()
        return {"ok": True, "redis": True}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc


# -------------------------
# Sessions
# -------------------------


@app.post("/sessions", response_model=CreateSessionResponse)
def create_session(req: CreateSessionRequest):
    sid = str(uuid.uuid4())
    created = now()

    r.hset(
        session_key(sid),
        mapping={
            "created_at": created,
            "parties": req.parties,
            "locked": 0,
            "meta": json.dumps(req.meta or {}),
        },
    )
    r.expire(session_key(sid), SESSION_TTL_SECONDS)
    r.expire(joined_key(sid), SESSION_TTL_SECONDS)

    admin_token = sign_token(
        {"typ": "admin", "session_id": sid},
        SESSION_TTL_SECONDS,
    )

    return CreateSessionResponse(
        session_id=sid,
        parties=req.parties,
        created_at=created,
        admin_token=admin_token,
    )


@app.post("/sessions/{session_id}/join", response_model=JoinResponse)
def join_session(session_id: str, req: JoinRequest):
    session = ensure_session_exists(session_id)

    if session.get("locked") == "1":
        raise HTTPException(status_code=409, detail="Session locked")

    added = r.sadd(joined_key(session_id), req.party_id)
    if added == 0:
        raise HTTPException(status_code=409, detail="Party already joined")

    party_token = sign_token(
        {
            "typ": "party",
            "session_id": session_id,
            "party_id": req.party_id,
        },
        SESSION_TTL_SECONDS,
    )

    joined = r.scard(joined_key(session_id))
    if joined >= int(session["parties"]):
        r.hset(session_key(session_id), "locked", 1)

    return JoinResponse(
        session_id=session_id,
        party_id=req.party_id,
        party_token=party_token,
        expected_parties=int(session["parties"]),
    )


# -------------------------
# Messaging
# -------------------------


@app.post("/sessions/{session_id}/send")
def send_message(
    session_id: str,
    req: SendMessageRequest,
    authorization: str = Header(""),
):
    claims = auth_token(authorization)
    if claims.get("typ") != "party" or claims.get("session_id") != session_id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    ensure_session_exists(session_id)

    if not r.sismember(joined_key(session_id), claims["party_id"]):
        raise HTTPException(status_code=403, detail="Party not joined")

    key = stream_key(session_id, req.channel)
    msg_id = r.xadd(
        key,
        {
            "ts": now(),
            "from": claims["party_id"],
            "to": req.to_party or "",
            "payload": encode_payload(req.payload),
        },
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )
    r.expire(key, SESSION_TTL_SECONDS)

    return {"ok": True, "message_id": msg_id}


@app.get("/sessions/{session_id}/poll", response_model=PollResponse)
def poll(
    session_id: str,
    channel: str = "default",
    last_id: str = "0-0",
    authorization: str = Header(""),
):
    claims = auth_token(authorization)
    if claims.get("typ") != "party" or claims.get("session_id") != session_id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    ensure_session_exists(session_id)

    key = stream_key(session_id, channel)
    records = r.xread({key: last_id}, count=100, block=XREAD_BLOCK_MS)

    messages: List[dict] = []
    new_last = last_id

    if records:
        _, entries = records[0]
        for mid, fields in entries:
            new_last = mid
            message = build_message(mid, fields, claims["party_id"])
            if message:
                messages.append(message)

    return PollResponse(
        session_id=session_id,
        channel=channel,
        last_id=new_last,
        messages=messages,
    )


# -------------------------
# WebSocket
# -------------------------


@app.websocket("/ws/sessions/{session_id}")
async def ws_session(websocket: WebSocket, session_id: str):
    await websocket.accept()

    try:
        init = await websocket.receive_json()
        token = init.get("token")
        channel = init.get("channel", "default")
        last_id = init.get("last_id", "0-0")

        claims = auth_token(token)
        if claims.get("typ") != "party" or claims.get("session_id") != session_id:
            await websocket.close(code=4403)
            return

        ensure_session_exists(session_id)

        key = stream_key(session_id, channel)

        while True:
            entries = r.xread({key: last_id}, block=XREAD_BLOCK_MS)
            if not entries:
                await websocket.send_json({"type": "keepalive", "ts": now()})
                continue

            _, records = entries[0]
            for mid, fields in records:
                last_id = mid
                message = build_message(mid, fields, claims["party_id"])
                if message:
                    await websocket.send_json({"type": "message", **message})

    except WebSocketDisconnect:
        pass
    except Exception:
        await websocket.close(code=1011)
