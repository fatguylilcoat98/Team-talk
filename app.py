"""Team Talk — collaborative AI discussion platform.

Chris sends one message; every AI on the roster responds. Two turn
styles: "parallel" (all at once via asyncio.gather) or "sequential"
(one after another, each seeing the earlier answers this round, with
the speaking order rotating every round). Three modes: collab, debate,
and ai_only (the AIs talk to each other while Chris watches).
"""

import asyncio
import json
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
import brain
import director
import episode_store
import file_store
import journal_store
import ledger
import memory_store
import notebook_store
import questions_store
import session_manager
import settings_store
import splendor
from conversation import (SHORT_TERM_ROUNDS, blind_labels, build_context,
                          normalize_modes, role_notes, system_prompt)

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
    mode: Optional[str] = "collab"           # single mode (older clients)
    modes: Optional[List[str]] = None        # stacked modes, e.g. ["hard_truth", "roast"]
    turn_style: Optional[str] = "parallel"  # parallel | sequential
    attachments: Optional[List[str]] = None  # upload ids from /api/upload
    awards: Optional[bool] = True            # live commentary & awards layer
    via_splendor: Optional[bool] = False     # Splendor delivers Chris's message


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
    modes = normalize_modes(request.modes if request.modes is not None else request.mode)
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

    # --- The room brain: ONE shared pass per round (Splendor architecture,
    # everything on this server's disk). Embed the message, rank memories and
    # past-session episodes by relevance, measure novelty, run the background
    # DMN reflection. All of it degrades to the pre-brain behavior when the
    # OpenAI key is missing or a call fails.
    query_vec = await brain.embed(message)
    memory_block, cross_episodes, novelty_score = await asyncio.gather(
        brain.ranked_memory_block(query_vec, memory_store.list_memories()),
        brain.ranked_episodes(query_vec, episode_store.list_episodes(),
                              exclude_session=session["id"]),
        brain.novelty(query_vec, history))
    # The whisper runs AFTER ranking so it can point at what the room already
    # knows, instead of asking generic questions the memories answer.
    whisper = await brain.dmn_whisper(
        message, history[-1]["chris_message"] if history else "",
        memory_context=memory_block)

    # Speak through Splendor: she delivers Chris's message into the room,
    # clearly labeled. Any failure falls back to his raw words — Splendor
    # being down never silences Chris.
    chris_raw = None
    via_splendor = False
    if request.via_splendor:
        delivered = await splendor.compose(message, history, memory_block=memory_block)
        if delivered:
            chris_raw = message
            message = delivered
            via_splendor = True

    for block in (episode_store.episodes_block(cross_episodes),
                  notebook_store.context_block(),
                  questions_store.context_block(),
                  brain.room_sense_block(novelty_score, whisper)):
        if block:
            memory_block = f"{memory_block}\n\n{block}" if memory_block else block
    episodes_block = episode_store.session_block(session["id"])

    participants = settings_store.get_participants()

    # Blind mode: names, personas, roles, and awards are all stripped — the
    # AIs see (and are) anonymous "Voice N" labels, stable within a session.
    blind = "blind" in modes
    labels = blind_labels(participants, session["id"]) if blind else {}
    display = {p["id"]: (labels.get(p["id"]) or p["name"]) for p in participants}
    notes = {} if blind else role_notes(modes, participants, session["id"])

    awards = bool(request.awards) and not blind

    def prompt_for(p, so_far=None):
        me = display[p["id"]]
        others = [display[q["id"]] for q in participants if q["id"] != p["id"]]
        # The boot packet: this participant's PRIVATE journal + chain status.
        # Authenticated records, never fake continuity — and never shown to
        # anyone else in the room.
        private = journal_store.boot_block(p["id"], p["name"])
        mem = f"{private}\n\n{memory_block}" if private else memory_block
        return (
            system_prompt(me, others, modes,
                          persona=None if blind else p.get("persona"),
                          role_note=notes.get(p["id"]), awards=awards),
            build_context(history, message, me, others, modes, so_far,
                          memory_block=mem, attachments_block=attachments_block,
                          episodes_block=episodes_block, via_splendor=via_splendor),
        )

    def _strip_markers(text: str) -> str:
        """Remove MEMORY/NOTEBOOK/PIN/JOURNAL/QUESTION lines without storing.
        Used for what LATER speakers see mid-round — a JOURNAL: line is
        private and must never ride along in someone else's context."""
        text, _ = memory_store.extract_memories(text)
        text, _, _ = notebook_store.extract(text)
        text, _ = journal_store.extract(text)
        text, _ = questions_store.extract(text)
        return text

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
                so_far.append({"name": display[p["id"]], "text": _strip_markers(result["text"])})
            responses.append(_response_entry(p, result, labels.get(p["id"])))
    else:
        # The core requirement: every AI is called at the same time
        prompts = [prompt_for(p) for p in participants]
        results = await asyncio.gather(
            *[api_client.call_participant(p, s, c, images=images)
              for p, (s, c) in zip(participants, prompts)]
        )
        responses = [_response_entry(p, r, labels.get(p["id"]))
                     for p, r in zip(participants, results)]

    # Long-term memory, notebook entries, pinned quotes, private journal
    # entries, and questions for Chris: strip the marker lines, store them
    # on disk, and record every write in the glass-box ledger. In blind
    # mode public credits go to the anonymous voice, not the real name —
    # but a journal always belongs to the real participant (it's private).
    for r in responses:
        author = r.get("label") or r["name"]
        cleaned, memories = memory_store.extract_memories(r["text"])
        if memories:
            r["text"] = cleaned
            for m in memories:
                entry = memory_store.add(m, author)
                ledger.append(author, "memory_created", ref=entry["id"],
                              detail={"text": m[:200]})
            r["memories_saved"] = len(memories)
        cleaned, notes_saved, pins_saved = notebook_store.extract(r["text"])
        if notes_saved or pins_saved:
            r["text"] = cleaned
            for n in notes_saved:
                entry = notebook_store.add_entry(n, author)
                ledger.append(author, "notebook_written", ref=entry["id"],
                              detail={"text": n[:200]})
            for q in pins_saved:
                pin = notebook_store.add_pin(q, author)
                ledger.append(author, "pin_created", ref=pin["id"],
                              detail={"quote": q[:200]})
            if notes_saved:
                r["notebook_saved"] = len(notes_saved)
            if pins_saved:
                r["pins_saved"] = len(pins_saved)
        cleaned, journal_entries = journal_store.extract(r["text"])
        if journal_entries:
            r["text"] = cleaned
            for j in journal_entries:
                entry = journal_store.write(
                    r["id"], r["name"], session["id"], j["note"],
                    intent=j["intent"], recognized=j["recognized"],
                    confidence=j["confidence"])
                if entry:
                    # Fact-of-write is public; the words stay in the journal.
                    ledger.append(author, "journal_written",
                                  ref=f"{r['id']}/v{entry['version']}",
                                  detail={"hash": entry["hash"],
                                          "recognized": entry["recognized"]})
            r["journal_saved"] = len(journal_entries)
        cleaned, asked = questions_store.extract(r["text"])
        if asked:
            r["text"] = cleaned
            for qt in asked:
                q = questions_store.ask(author, qt, session["id"])
                ledger.append(author, "question_asked", ref=q["id"],
                              detail={"question": qt[:200]})
            r["questions_asked"] = len(asked)

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
        "mode": modes[0],   # older readers see the primary mode
        "modes": modes,
        "turn_style": turn_style,
        **({"via_splendor": True, "chris_raw": chris_raw} if via_splendor else {}),
        "awards": awards,
        "attachments": [
            {"id": m["id"], "name": m["name"], "kind": m["kind"]} for m in att_metas
        ],
        "responses": responses,
    }

    # Persist immediately so no round is ever lost
    session["rounds"].append(round_data)
    await session_manager.save_session(session)

    # Episodic compression, fire-and-forget: rounds that aged out of the
    # verbatim window get summarized so the next round can still see them.
    asyncio.create_task(_compress_session(session["id"], list(session["rounds"])))

    return {"session_id": session["id"], "status": status, **round_data}


async def _compress_session(session_id: str, rounds: List[dict]) -> None:
    try:
        chunk = episode_store.pending_chunk(session_id, rounds, SHORT_TERM_ROUNDS)
        if not chunk:
            return
        summary = await brain.summarize_rounds(chunk)
        if summary:
            ep = episode_store.add(session_id, chunk[0].get("round") or 0,
                                   chunk[-1].get("round") or 0, summary)
            print(f"[BRAIN] compressed rounds {ep['first_round']}–{ep['last_round']} of {session_id}")
    except Exception as e:
        print(f"[BRAIN] compression skipped: {e}")


def _response_entry(p: dict, result: dict, label: Optional[str] = None) -> dict:
    entry = {
        "id": p["id"],
        "name": p["name"],
        "text": result["text"],
        "tokens": result["tokens"],
        "color": p.get("color", "#93a0b8"),
    }
    if label:
        # Blind round: anonymous label, neutral color (the real color would
        # give the identity away), and no persona badge.
        entry["label"] = label
        entry["color"] = "#8a93a5"
    elif p.get("persona"):
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

class MemoryAdd(BaseModel):
    text: str


@app.get("/api/memory")
async def get_memory():
    return {"memories": memory_store.list_memories()}


@app.post("/api/memory")
async def add_memory(body: MemoryAdd):
    """Chris states a fact directly — saved with [stated] provenance."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Memory is empty")
    return memory_store.add(text, "Chris", kind="chris_stated")


@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str):
    if not memory_store.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    ledger.append("Chris", "tombstone_placed", ref=memory_id,
                  detail={"store": "memory", "action": "memory_removed"})
    return {"status": "removed", "tombstone": True}


@app.delete("/api/memory")
async def clear_memory():
    removed = memory_store.clear()
    ledger.append("Chris", "memory_cleared", detail={"tombstoned": removed})
    return {"status": "cleared", "removed": removed}


# --- The Truth Layer: verify, ledger, questions -------------------------------

def _known_participant_ids() -> List[str]:
    ids = {p["id"] for p in settings_store.get_participants()}
    ids.update(journal_store.list_journals())
    ids.update({"splendor", "director"})
    return sorted(ids)


@app.get("/api/verify/{participant_id}")
async def verify_participant(participant_id: str):
    """Raw data only: the participant's full journal, chain math, and the
    verification history. No summaries, no narrator."""
    entries = journal_store.read(participant_id)
    chain = journal_store.verify(participant_id)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ledger.append("Chris", "journal_viewed", ref=participant_id,
                  detail={"entries": len(entries)})
    ledger.append("Chris", "verify_executed", ref=participant_id,
                  detail={"valid": chain["valid"], "length": chain["length"]})
    history = ledger.list_events(action="verify_executed", limit=20)
    return {
        "participant": participant_id,
        "journal": entries,                      # immutable chronological log, raw
        "chain": chain,                          # recomputed just now
        "last_verified": now,
        "journal_version": entries[-1]["version"] if entries else 0,
        "latest_hash": entries[-1]["hash"] if entries else None,
        "verification_history": [e for e in history if e.get("ref") == participant_id],
        "note": "Verification code is open source — recompute every hash yourself from the repo.",
    }


@app.get("/api/verify/{participant_id}/bundle")
async def verification_bundle(participant_id: str):
    """Export bundle: everything needed to verify this journal offline."""
    entries = journal_store.read(participant_id)
    chain = journal_store.verify(participant_id)
    events = [e for e in ledger.list_events(limit=1000)
              if participant_id in (e.get("ref") or "") or e.get("actor") == participant_id]
    ledger.append("Chris", "bundle_exported", ref=participant_id,
                  detail={"entries": len(entries), "events": len(events)})
    bundle = {
        "participant": participant_id,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "journal": entries,
        "chain": chain,
        "ledger_events": events,
        "how_to_verify": (
            "Each entry: hash = sha256(ts|writer|session|intent|continuity_note|"
            "recognized|confidence|prev_hash); content_hash = sha256(continuity_note); "
            "first prev_hash is 64 zeros. Code: journal_store.py in the public repo."
        ),
    }
    return Response(
        content=json.dumps(bundle, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{participant_id}-verification-bundle.json"'},
    )


@app.get("/api/verify")
async def verify_all():
    """Chain status for every participant + the glass-box ledger chain."""
    participants = []
    for pid in _known_participant_ids():
        entries = journal_store.read(pid)
        chain = journal_store.verify(pid)
        participants.append({
            "participant": pid,
            "entries": len(entries),
            "valid": chain["valid"],
            "reason": chain["reason"],
            "latest_hash": entries[-1]["hash"] if entries else None,
            "last_entry_at": entries[-1]["ts"] if entries else None,
        })
    return {"participants": participants, "ledger": ledger.verify_chain()}


@app.get("/api/ledger")
async def get_ledger(actor: Optional[str] = None, action: Optional[str] = None,
                     limit: int = 100):
    return {"chain": ledger.verify_chain(),
            "events": ledger.list_events(actor=actor, action=action, limit=limit)}


class AnswerBody(BaseModel):
    answer: str


@app.get("/api/questions")
async def get_questions():
    return {"questions": questions_store.list_questions(),
            "open": questions_store.open_count()}


@app.post("/api/questions/{question_id}/answer")
async def answer_question(question_id: str, body: AnswerBody):
    text = body.answer.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Answer is empty")
    q = questions_store.answer(question_id, text)
    if q is None:
        raise HTTPException(status_code=404, detail="Open question not found")
    ledger.append("Chris", "question_answered", ref=question_id,
                  detail={"answer": text[:200]})
    return q


# --- Voice mode: Splendor speaks the room ------------------------------------

class RecapRequest(BaseModel):
    session_id: str
    round: int


@app.post("/api/voice/recap")
async def voice_recap(req: RecapRequest):
    """Splendor's spoken synthesis of one round: text + (if possible) audio.

    Audio comes from OpenAI TTS when the key allows; otherwise the client
    falls back to the browser's built-in voice using the text alone.
    """
    if not session_manager.valid_id(req.session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    session = await session_manager.load_session(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    round_data = next((r for r in session["rounds"] if r.get("round") == req.round), None)
    if round_data is None:
        raise HTTPException(status_code=404, detail="Round not found")

    text = await splendor.recap(round_data)
    if not text:
        raise HTTPException(status_code=503, detail="Splendor's recap is unavailable — check the API keys in Settings.")

    audio_b64 = None
    key = api_client.openai_key()
    if key:
        try:
            client = api_client._get_client({"provider": "openai"}, key)
            resp = await client.audio.speech.create(
                model=os.getenv("SPLENDOR_TTS_MODEL", "gpt-4o-mini-tts"),
                voice=os.getenv("SPLENDOR_VOICE", "nova"),
                input=text[:3000],
            )
            data = resp.content if hasattr(resp, "content") else await resp.aread()
            audio_b64 = base64.standard_b64encode(data).decode("ascii")
        except Exception as e:
            print(f"[VOICE] TTS failed, client will use the browser voice: {e}")

    return {"text": text, "audio_b64": audio_b64}


# --- The Notebook (shared scratchpad + pinned quotes) -----------------------

class NotebookAdd(BaseModel):
    text: str


@app.get("/api/notebook")
async def get_notebook():
    return notebook_store.list_all()


@app.post("/api/notebook")
async def add_notebook_entry(body: NotebookAdd):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Entry is empty")
    return notebook_store.add_entry(text, "Chris")


@app.delete("/api/notebook/entries/{entry_id}")
async def delete_notebook_entry(entry_id: str):
    if not notebook_store.delete_entry(entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    ledger.append("Chris", "tombstone_placed", ref=entry_id,
                  detail={"store": "notebook", "action": "notebook_removed"})
    return {"status": "removed", "tombstone": True}


@app.delete("/api/notebook/pins/{pin_id}")
async def delete_notebook_pin(pin_id: str):
    if not notebook_store.delete_pin(pin_id):
        raise HTTPException(status_code=404, detail="Pin not found")
    ledger.append("Chris", "tombstone_placed", ref=pin_id,
                  detail={"store": "pins", "action": "notebook_removed"})
    return {"status": "removed", "tombstone": True}


@app.delete("/api/notebook")
async def clear_notebook():
    removed = notebook_store.clear()
    ledger.append("Chris", "notebook_cleared", detail={"tombstoned": removed})
    return {"status": "cleared", "removed": removed}


# --- 🎬 Director's Cut --------------------------------------------------------

@app.post("/api/sessions/{session_id}/directors_cut")
async def wrap_directors_cut(session_id: str):
    """The Wrap: the Director reviews the session and cuts the shorts."""
    session = await session_manager.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.get("rounds"):
        raise HTTPException(status_code=400, detail="Nothing to cut — the session has no rounds yet.")
    cut = await director.wrap_session(session)
    ledger.append("Director", "directors_cut_wrapped", ref=session_id,
                  detail={"moments": len(cut["moments"]), "clips": len(cut["clips"])})
    if not cut["moments"]:
        raise HTTPException(
            status_code=503,
            detail="The Director couldn't review the footage — check the API keys in Settings, or the session may be too quiet for clips.",
        )
    return cut


@app.get("/api/sessions/{session_id}/directors_cut")
async def get_directors_cut(session_id: str):
    if not session_manager.valid_id(session_id):
        raise HTTPException(status_code=400, detail="Invalid session_id")
    cut = director.load_cut(session_id)
    return cut if cut else {"session_id": session_id, "moments": [], "clips": []}


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
async def export_session(session_id: str, format: str = "markdown"):
    session = await session_manager.load_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if format == "html":
        return Response(
            content=session_manager.export_html(session),
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{session_id}.html"'},
        )
    return Response(
        content=session_manager.export_markdown(session),
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
