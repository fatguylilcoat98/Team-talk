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

import session_manager
from api_client import call_chatgpt, call_claude
from conversation import build_context

app = FastAPI(title="Team Talk")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


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
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    print(f"Team Talk running at http://localhost:{port}")
    uvicorn.run(app, host=host, port=port)
