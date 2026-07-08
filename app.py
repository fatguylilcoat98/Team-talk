"""Team Talk — three-way collaborative discussion platform.

Chris sends one message; Claude and ChatGPT are called simultaneously
(asyncio.gather) and each sees the full history plus the other's
previous response.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import api_client
import session_manager
import settings_store
from api_client import call_chatgpt, call_claude
from conversation import build_context

LAN_WARNING = "Do not expose Team Talk publicly unless authentication is added."

app = FastAPI(title="Team Talk")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class SettingsUpdate(BaseModel):
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    claude_model: Optional[str] = None
    chatgpt_model: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None


class TestKeysRequest(BaseModel):
    # Optional: test keys typed into the form before saving them.
    # When omitted, the currently configured keys are tested.
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.post("/api/chat")
async def chat(request: ChatRequest):
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty")

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

    claude_context = build_context(history, message, ai="claude")
    chatgpt_context = build_context(history, message, ai="chatgpt")

    # The core requirement: both AIs are called at the same time
    claude_result, chatgpt_result = await asyncio.gather(
        call_claude(claude_context),
        call_chatgpt(chatgpt_context),
    )

    if claude_result["ok"] and chatgpt_result["ok"]:
        status = "success"
    elif claude_result["ok"] or chatgpt_result["ok"]:
        status = "partial"
    else:
        status = "error"

    round_data = {
        "round": round_number,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chris_message": message,
        "claude_response": claude_result["text"],
        "claude_tokens": claude_result["tokens"],
        "chatgpt_response": chatgpt_result["text"],
        "chatgpt_tokens": chatgpt_result["tokens"],
    }

    # Persist immediately so no round is ever lost
    session["rounds"].append(round_data)
    await session_manager.save_session(session)

    return {"session_id": session["id"], "status": status, **round_data}


def _settings_snapshot() -> dict:
    """Current effective settings — API keys are always masked."""
    return {
        "anthropic_api_key_masked": settings_store.mask_key(api_client.anthropic_key()),
        "anthropic_key_source": settings_store.source("anthropic_api_key", "ANTHROPIC_API_KEY"),
        "openai_api_key_masked": settings_store.mask_key(api_client.openai_key()),
        "openai_key_source": settings_store.source("openai_api_key", "OPENAI_API_KEY"),
        "claude_model": api_client.claude_model(),
        "chatgpt_model": api_client.chatgpt_model(),
        "host": settings_store.resolve("host", "HOST", "127.0.0.1"),
        "port": int(settings_store.resolve("port", "PORT", "5000")),
        "warning": LAN_WARNING,
    }


@app.get("/api/settings")
async def get_settings():
    return _settings_snapshot()


@app.post("/api/settings")
async def save_settings(update: SettingsUpdate):
    updates = {k: v for k, v in update.dict().items() if v not in (None, "")}
    if updates.get("port") is not None:
        port = int(updates["port"])
        if not 1 <= port <= 65535:
            raise HTTPException(status_code=400, detail="Port must be between 1 and 65535")
        updates["port"] = str(port)
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
async def test_keys(request: TestKeysRequest):
    claude_result, chatgpt_result = await asyncio.gather(
        api_client.test_anthropic_key(request.anthropic_api_key or None),
        api_client.test_openai_key(request.openai_api_key or None),
    )
    return {"claude": claude_result, "chatgpt": chatgpt_result}


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
