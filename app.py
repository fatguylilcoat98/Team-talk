"""Team Talk — collaborative AI discussion platform.

Chris sends one message; every AI on the roster responds. Two turn
styles: "parallel" (all at once via asyncio.gather) or "sequential"
(one after another, each seeing the earlier answers this round, with
the speaking order rotating every round). Three modes: collab, debate,
and ai_only (the AIs talk to each other while Chris watches).
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

import base64

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import api_client
import file_store
import memory_store
import session_manager
import settings_store
from conversation import MODES, build_context, system_prompt

LAN_WARNING = "Do not expose Team Talk publicly unless authentication is added."

app = FastAPI(title="Team Talk")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Read once at startup. Static files are served fresh from disk, so after a
# `git pull` without a restart the page's copy of version.txt is newer than
# this in-memory value — the frontend compares the two and shows a banner.
try:
    with open(os.path.join(STATIC_DIR, "version.txt"), "r", encoding="utf-8") as _vf:
        APP_VERSION = _vf.read().strip()
except OSError:
    APP_VERSION = "0"


@app.get("/api/version")
async def version():
    return {"version": APP_VERSION}


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    mode: Optional[str] = "collab"          # collab | debate | ai_only
    turn_style: Optional[str] = "parallel"  # parallel | sequential
    attachments: Optional[List[str]] = None  # upload ids from /api/upload


class ParticipantUpdate(BaseModel):
    id: Optional[str] = None
    name: str
    provider: str = "openai"       # anthropic | openai (openai covers any
    model: str                     # OpenAI-compatible endpoint via base_url)
    api_key: Optional[str] = None  # blank = keep the saved key for this id
    base_url: Optional[str] = None
    color: Optional[str] = None
    persona: Optional[str] = None  # character to play, e.g. "a pirate who doesn't give a shit"


class SettingsUpdate(BaseModel):
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    participants: Optional[List[ParticipantUpdate]] = None


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.post("/api/chat")
async def chat(request: ChatRequest):
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty")
    mode = request.mode if request.mode in MODES else "collab"
    turn_style = request.turn_style if request.turn_style in ("parallel", "sequential") else "parallel"

    session = None
    if request.session_id:
        if not session_manager.valid_id(request.session_id):
            raise HTTPException(status_code=400, detail="Invalid session_id")
        session = await session_manager.load_session(request.session_id)
        if session is None:
            session = session_manager.new_session(request.session_id)
    else:
        session = session_manager.new_session()

    history = session["rounds"]
    round_number = len(history) + 1

    # Attachments: images go to the APIs natively; text/PDF content is
    # inlined into the round's context
    att_metas = []
    for att_id in (request.attachments or [])[:8]:
        meta = file_store.get_meta(att_id)
        if meta and file_store.get_path(att_id):
            att_metas.append(meta)
    images = []
    for meta in att_metas:
        if meta["kind"] == "image":
            raw = file_store.load_bytes(meta["id"])
            if raw:
                images.append({
                    "media_type": meta["mime"],
                    "data": base64.standard_b64encode(raw).decode("ascii"),
                })
    attachments_block = file_store.attachments_context(att_metas)
    memory_block = memory_store.context_block()

    participants = settings_store.get_participants()
    names = [p["name"] for p in participants]

    def prompt_for(p, so_far=None):
        others = [n for n in names if n != p["name"]]
        return (
            system_prompt(p["name"], others, mode, persona=p.get("persona")),
            build_context(history, message, p["name"], others, mode, so_far,
                          memory_block=memory_block, attachments_block=attachments_block),
        )

    responses = []
    if turn_style == "sequential":
        # Rotate the speaking order each round so nobody always goes first
        rot = (round_number - 1) % len(participants)
        order = participants[rot:] + participants[:rot]
        so_far = []
        for p in order:
            system, ctx = prompt_for(p, so_far)
            result = await api_client.call_participant(p, system, ctx, images=images)
            if result["ok"]:
                so_far.append({"name": p["name"], "text": result["text"]})
            responses.append(_response_entry(p, result))
    else:
        # The core requirement: every AI is called at the same time
        prompts = [prompt_for(p) for p in participants]
        results = await asyncio.gather(
            *[api_client.call_participant(p, s, c, images=images)
              for p, (s, c) in zip(participants, prompts)]
        )
        responses = [_response_entry(p, r) for p, r in zip(participants, results)]

    # Long-term memory: strip MEMORY: lines and store them on disk
    for r in responses:
        cleaned, memories = memory_store.extract_memories(r["text"])
        if memories:
            r["text"] = cleaned
            for m in memories:
                memory_store.add(m, r["name"])
            r["memories_saved"] = len(memories)

    ok_count = sum(1 for r in responses if not r["text"].startswith("Error:"))
    if ok_count == len(responses):
        status = "success"
    elif ok_count:
        status = "partial"
    else:
        status = "error"

    round_data = {
        "round": round_number,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chris_message": message,
        "mode": mode,
        "turn_style": turn_style,
        "attachments": [
            {"id": m["id"], "name": m["name"], "kind": m["kind"]} for m in att_metas
        ],
        "responses": responses,
    }

    # Persist immediately so no round is ever lost
    session["rounds"].append(round_data)
    await session_manager.save_session(session)

    return {"session_id": session["id"], "status": status, **round_data}


def _response_entry(p: dict, result: dict) -> dict:
    entry = {
        "id": p["id"],
        "name": p["name"],
        "text": result["text"],
        "tokens": result["tokens"],
        "color": p.get("color", "#93a0b8"),
    }
    if p.get("persona"):
        entry["persona"] = p["persona"]
    return entry


# --- Settings --------------------------------------------------------------

def _public_participants() -> List[dict]:
    """Roster for the browser — per-AI keys masked, never returned in full."""
    out = []
    for p in settings_store.get_participants():
        out.append({
            "id": p["id"],
            "name": p["name"],
            "provider": p.get("provider", "openai"),
            "model": p.get("model", ""),
            "base_url": p.get("base_url", ""),
            "color": p.get("color", "#93a0b8"),
            "persona": p.get("persona", ""),
            "api_key_masked": settings_store.mask_key(p.get("api_key")),
            "uses_shared_key": not p.get("api_key"),
        })
    return out


def _settings_snapshot() -> dict:
    return {
        "anthropic_api_key_masked": settings_store.mask_key(api_client.anthropic_key()),
        "anthropic_key_source": settings_store.source("anthropic_api_key", "ANTHROPIC_API_KEY"),
        "openai_api_key_masked": settings_store.mask_key(api_client.openai_key()),
        "openai_key_source": settings_store.source("openai_api_key", "OPENAI_API_KEY"),
        "participants": _public_participants(),
        "host": settings_store.resolve("host", "HOST", "127.0.0.1"),
        "port": int(settings_store.resolve("port", "PORT", "5000")),
        "warning": LAN_WARNING,
    }


@app.get("/api/settings")
async def get_settings():
    return _settings_snapshot()


@app.post("/api/settings")
async def save_settings(update: SettingsUpdate):
    updates = {}
    for field in ("anthropic_api_key", "openai_api_key", "host"):
        value = getattr(update, field)
        if value not in (None, ""):
            updates[field] = value
    if update.port is not None:
        if not 1 <= update.port <= 65535:
            raise HTTPException(status_code=400, detail="Port must be between 1 and 65535")
        updates["port"] = str(update.port)
    if update.participants is not None:
        roster = settings_store.sanitize_participants([p.dict() for p in update.participants])
        if not roster:
            raise HTTPException(status_code=400, detail="At least one AI with a name and model is required")
        updates["participants"] = roster
    try:
        settings_store.save(updates)
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not write settings file ({e}). Check that the service user owns the app folder.",
        )
    snapshot = _settings_snapshot()
    if "host" in updates or "port" in updates:
        snapshot["note"] = "Host/port changes take effect after the server restarts."
    return snapshot


@app.delete("/api/settings")
async def reset_settings():
    removed = settings_store.reset()
    snapshot = _settings_snapshot()
    snapshot["reset"] = removed
    return snapshot


@app.post("/api/settings/test")
async def test_keys():
    participants = settings_store.get_participants()
    results = await asyncio.gather(*[api_client.test_participant(p) for p in participants])
    return {"results": list(results)}


# --- Uploads ----------------------------------------------------------------

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    try:
        meta = file_store.save_upload(file.filename or "file", content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": meta["id"], "name": meta["name"], "kind": meta["kind"], "size": meta["size"]}


@app.get("/api/uploads/{file_id}")
async def serve_upload(file_id: str):
    meta = file_store.get_meta(file_id)
    path = file_store.get_path(file_id) if meta else None
    if not path:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type=meta["mime"], filename=meta["name"])


# --- Memory -------------------------------------------------------------------

@app.get("/api/memory")
async def get_memory():
    return {"memories": memory_store.list_memories()}


@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str):
    if not memory_store.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted"}


@app.delete("/api/memory")
async def clear_memory():
    removed = memory_store.clear()
    return {"status": "cleared", "removed": removed}


# --- Sessions ---------------------------------------------------------------

@app.get("/api/sessions")
async def sessions():
    return {"sessions": await session_manager.list_sessions()}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    session = await session_manager.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session["id"],
        "created_at": session.get("created_at", ""),
        "rounds": session.get("rounds", []),
    }


@app.post("/api/sessions/{session_id}/export")
async def export_session(session_id: str):
    session = await session_manager.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    markdown = session_manager.export_markdown(session)
    return Response(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{session_id}.md"'},
    )


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    if not session_manager.delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}


if __name__ == "__main__":
    host = settings_store.resolve("host", "HOST", "127.0.0.1")
    port = int(settings_store.resolve("port", "PORT", "5000"))
    print(f"Team Talk running at http://localhost:{port}")
    uvicorn.run(app, host=host, port=port)
