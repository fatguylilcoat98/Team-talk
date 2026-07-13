"""Team Talk — collaborative AI discussion platform.

Chris sends one message; every AI on the roster responds. Two turn
styles: "parallel" (all at once via asyncio.gather) or "sequential"
(one after another, each seeing the earlier answers this round, with
the speaking order rotating every round). Three modes: collab, debate,
and ai_only (the AIs talk to each other while Chris watches).
"""

import asyncio
import hashlib
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

import about_store
import api_client
import brain
import code_access
import director
import episode_store
import failure_log
import file_store
import game_master
import game_store
import history_store
import journal_store
import ledger
import mailbox_store
import memory_store
import mode_shift
import night_shift
import notebook_store
import proposal_store
import questions_store
import studio_store
import receipt_store
import room_actions
import session_manager
import settings_store
import splendor
import wall_store
import workshop_engine
import workshop_store
from conversation import (MODES, SHORT_TERM_ROUNDS, blind_labels, build_context,
                          lounge_system_prompt,
                          normalize_modes, role_notes, system_prompt)

LAN_WARNING = "Do not expose Team Talk publicly unless authentication is added."

app = FastAPI(title="Team Talk")

# A 🌙 run that says "running" after a restart is a ghost — no task behind
# it. Close it honestly before anyone reads the state.
night_shift.mark_stale()

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


def _ledger_deploy_once() -> None:
    """Every deploy becomes a chain event. The room's provenance demand
    (Gemini/Muse, the v6 round): 'if it isn't in the chain, it's just a
    story.' The event carries the git commit hash and subject — and the
    commit message is where each change's receipts live — so code and
    chain reference each other from both sides."""
    marker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "memory", "deployed_version.txt")
    try:
        with open(marker, "r", encoding="utf-8") as f:
            if f.read().strip() == APP_VERSION:
                return
    except OSError:
        pass
    commit, subject = "", ""
    try:
        import subprocess
        base = os.path.dirname(os.path.abspath(__file__))
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=base, capture_output=True, text=True,
                                timeout=5).stdout.strip()
        subject = subprocess.run(["git", "log", "-1", "--format=%s"],
                                 cwd=base, capture_output=True, text=True,
                                 timeout=5).stdout.strip()
    except Exception:
        pass
    ledger.append("Fable", "code_shipped", ref=f"v{APP_VERSION}",
                  detail={"commit": commit, "subject": subject[:200]})
    try:
        os.makedirs(os.path.dirname(marker), mode=0o700, exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(APP_VERSION)
    except OSError:
        pass


_ledger_deploy_once()


@app.get("/api/version")
async def version():
    return {"version": APP_VERSION}


class RoomContext(BaseModel):
    """Canonical room time/place from Chris's device — city-level only."""
    local_date: Optional[str] = None
    local_time: Optional[str] = None
    tz: Optional[str] = None
    location: Optional[str] = None
    location_source: Optional[str] = None

    def clean(self) -> dict:
        return {k: (str(v)[:80] if v else None)
                for k, v in self.dict().items()}


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    mode: Optional[str] = "collab"           # single mode (older clients)
    modes: Optional[List[str]] = None        # stacked modes, e.g. ["hard_truth", "roast"]
    turn_style: Optional[str] = "parallel"  # parallel | sequential
    attachments: Optional[List[str]] = None  # upload ids from /api/upload
    awards: Optional[bool] = True            # live commentary & awards layer
    via_splendor: Optional[bool] = False     # Splendor delivers Chris's message
    room_context: Optional[RoomContext] = None  # device-verified time & place
    lounge: Optional[bool] = False           # 🛋️ off-the-record hangout room


class ParticipantUpdate(BaseModel):
    id: Optional[str] = None
    name: str
    provider: str = "openai"       # anthropic | openai (openai covers any
    model: str                     # OpenAI-compatible endpoint via base_url)
    api_key: Optional[str] = None  # blank = keep the saved key for this id
    base_url: Optional[str] = None
    color: Optional[str] = None
    persona: Optional[str] = None  # character to play, e.g. "a pirate who doesn't give a shit"
    resting: Optional[bool] = False  # seat stays configured but isn't called
    max_tokens: Optional[int] = None  # per-seat output cost cap (0/None = default)


class SettingsUpdate(BaseModel):
    anthropic_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    location: Optional[str] = None
    participants: Optional[List[ParticipantUpdate]] = None


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


_session_locks: dict = {}
_SESSION_LOCKS_MAX = 2000   # keep the per-session lock table from growing forever


def _session_lock(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        # Before adding another, drop any idle (unheld) locks if the table has
        # grown large — a held lock is never evicted, so this can't race a
        # round in flight. Bounded cleanup for a long-lived server.
        if len(_session_locks) >= _SESSION_LOCKS_MAX:
            for k in [k for k, v in _session_locks.items() if not v.locked()]:
                del _session_locks[k]
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


@app.post("/api/chat")
async def chat(request: ChatRequest):
    # Serialize concurrent messages to the SAME session so two in-flight rounds
    # can't both read the same round count, run, and overwrite each other — a
    # double-tap or a second tab used to silently lose a whole paid-for round.
    sid = (request.session_id
           if request.session_id and session_manager.valid_id(request.session_id)
           else None)
    if sid:
        async with _session_lock(sid):
            return await _chat_impl(request)
    return await _chat_impl(request)


def _clean_markers(text: str, participants: list) -> str:
    """Strip every marker line WITHOUT storing anything. Module-level twin of
    the nested _strip_markers, so the Lounge can present a clean read too —
    a stray MEMORY:/PITCH:/JOURNAL: line shouldn't show raw just because the
    Lounge saves nothing."""
    text, _ = memory_store.extract_memories(text)
    text, _, _ = notebook_store.extract(text)
    text, _ = journal_store.extract(text)
    text, _ = questions_store.extract(text)
    text, _ = mailbox_store.extract(text, participants)
    text, _ = about_store.extract(text)
    text, _ = code_access.extract(text)
    text, _ = proposal_store.extract(text)
    text, _, _ = studio_store.extract(text)
    text, _ = mode_shift.extract(text)
    text = room_actions._ACTION_LINE.sub("", text).strip()
    return text


async def _lounge_turn(session: dict, message: str, turn_style: str,
                       room_context) -> dict:
    """🛋️ One Lounge round. No memory, no markers, no awards, no ledger, no
    workshop — just the stripped prompt and the conversation so far. Nothing is
    graded or remembered; the session is tagged so it stays out of the Living
    Room's business."""
    session["lounge"] = True
    history = session["rounds"]
    round_number = len(history) + 1
    turn_style = turn_style if turn_style in ("parallel", "sequential") else "parallel"
    rc = room_context.clean() if room_context else None

    participants = [p for p in settings_store.get_participants() if not p.get("resting")]
    if not participants:
        raise HTTPException(status_code=400,
                            detail="Every seat is resting — wake at least one in Settings.")

    def prompt_for(p, so_far=None):
        me = p["name"]
        others = [q["name"] for q in participants if q["id"] != p["id"]]
        system = lounge_system_prompt(me, others)
        ctx = build_context(history, message, me, others, mode="collab",
                            so_far=so_far, memory_block="", room_context=rc,
                            lounge=True)
        return system, ctx

    responses = []
    if turn_style == "sequential":
        rot = (round_number - 1) % len(participants)
        order = participants[rot:] + participants[:rot]
        so_far = []
        for p in order:
            system, ctx = prompt_for(p, so_far)
            result = await api_client.call_participant(
                p, system, ctx, context="chat", session_id=session["id"])
            if result["ok"]:
                so_far.append({"name": p["name"],
                               "text": _clean_markers(result["text"], participants)})
            responses.append(_response_entry(p, result))
    else:
        prompts = [prompt_for(p) for p in participants]
        results = await asyncio.gather(
            *[api_client.call_participant(p, s, c, context="chat", session_id=session["id"])
              for p, (s, c) in zip(participants, prompts)])
        responses = [_response_entry(p, r) for p, r in zip(participants, results)]

    # Nothing is stored in the Lounge, but stray marker lines still get stripped
    # from the visible read so it stays consistent with every other room.
    for r in responses:
        r["text"] = _clean_markers(r["text"], participants)

    ok_count = sum(1 for r in responses if r.get("ok", True))
    status = "success" if ok_count == len(responses) else ("partial" if ok_count else "error")
    round_data = {
        "round": round_number,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chris_message": message,
        "lounge": True,
        "turn_style": turn_style,
        **({"room_context": rc} if rc else {}),
        "responses": responses,
    }
    session["rounds"].append(round_data)
    await session_manager.save_session(session)
    return {"session_id": session["id"], "status": status, "lounge": True, **round_data}


async def _chat_impl(request: ChatRequest):
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

    # 🛋️ The Lounge: a separate, off-the-record turn — stripped prompt, no
    # memory/brain, no markers, no awards, no ledger. Nothing here is graded
    # or remembered. Handled entirely apart from the business machinery below.
    if request.lounge:
        return await _lounge_turn(session, message,
                                  request.turn_style,
                                  request.room_context)

    history = session["rounds"]
    round_number = len(history) + 1

    # 🔀 A seat's SHIFT TO from last round takes effect now — for exactly
    # one round, unless Chris changed his mode pick (his floor wins).
    shift_notices = []
    chris_floor = list(modes)
    prev_modes = (session_manager.normalize_round(history[-1]).get("modes")
                  or []) if history else []
    applied = mode_shift.apply_pending(session, modes, prev_modes, round_number)
    if applied:
        shift_notices.append(applied["record"])
        ledger.append(applied["record"]["by"], "mode_shift",
                      ref=applied["record"]["mode"],
                      detail={k: applied["record"][k] for k in ("status", "reason", "round")})
        if applied.get("modes"):
            modes = normalize_modes(applied["modes"]) or modes

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
                  wall_store.context_block(),
                  history_store.context_block(),
                  questions_store.context_block(),
                  workshop_store.context_block(settings_store.get_participants()),
                  proposal_store.context_block(),
                  studio_store.context_block(),
                  code_access.index_block(),
                  brain.room_sense_block(novelty_score, whisper)):
        if block:
            memory_block = f"{memory_block}\n\n{block}" if memory_block else block
    episodes_block = episode_store.session_block(session["id"])
    if shift_notices:
        _shift_lines = "\n".join(mode_shift.record_line(x) for x in shift_notices)
        memory_block = f"{_shift_lines}\n\n{memory_block}" if memory_block else _shift_lines

    # Canonical room context: one device-verified time & place for everyone.
    # A change of place (vs the previous round) is itself a ledgered event.
    rc = request.room_context.clean() if request.room_context else None
    if rc and rc.get("location") and history:
        prev_rc = next((r.get("room_context") for r in reversed(history)
                        if r.get("room_context", {}).get("location")), None)
        if prev_rc and rc["location"] != prev_rc["location"]:
            ledger.append("Chris", "room_context_changed",
                          detail={"from": prev_rc["location"], "to": rc["location"]})

    participants = [p for p in settings_store.get_participants()
                    if not p.get("resting")]
    if not participants:
        raise HTTPException(status_code=400,
                            detail="Every seat is resting — wake at least one in Settings.")

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
        # The boot packet: this participant's PRIVATE journal + chain status
        # + unread mailbox. Authenticated records, never fake continuity —
        # and never shown to anyone else in the room.
        private = journal_store.boot_block(p["id"], p["name"])
        mailbox = mailbox_store.boot_block(p["id"])
        receipts = receipt_store.boot_block(p["id"])
        code_files = code_access.boot_block(p["id"])
        mem = memory_block
        for blk in (code_files, mailbox, receipts, private):
            if blk:
                mem = f"{blk}\n\n{mem}" if mem else blk
        return (
            system_prompt(me, others, modes,
                          persona=None if blind else p.get("persona"),
                          role_note=notes.get(p["id"]), awards=awards),
            build_context(history, message, me, others, modes, so_far,
                          memory_block=mem, attachments_block=attachments_block,
                          episodes_block=episodes_block, via_splendor=via_splendor,
                          room_context=rc),
        )

    def _strip_markers(text: str) -> str:
        """Remove every marker line without storing anything.
        Used for what LATER speakers see mid-round — a JOURNAL: line is
        private and must never ride along in someone else's context."""
        text, _ = memory_store.extract_memories(text)
        text, _, _ = notebook_store.extract(text)
        text, _ = journal_store.extract(text)
        text, _ = questions_store.extract(text)
        text, _ = mailbox_store.extract(text, participants)
        text, _ = about_store.extract(text)
        text, _ = code_access.extract(text)
        text, _ = proposal_store.extract(text)
        text, _, _ = studio_store.extract(text)
        text, _ = mode_shift.extract(text)
        text = room_actions._ACTION_LINE.sub("", text).strip()
        return text

    responses = []
    if turn_style == "sequential":
        # Rotate the speaking order each round so nobody always goes first
        rot = (round_number - 1) % len(participants)
        order = participants[rot:] + participants[:rot]
        so_far = []
        for p in order:
            system, ctx = prompt_for(p, so_far)
            result = await api_client.call_participant(
                p, system, ctx, images=images, context="chat", session_id=session["id"])
            if result["ok"]:
                so_far.append({"name": display[p["id"]], "text": _strip_markers(result["text"])})
            responses.append(_response_entry(p, result, labels.get(p["id"])))
    else:
        # The core requirement: every AI is called at the same time
        prompts = [prompt_for(p) for p in participants]
        results = await asyncio.gather(
            *[api_client.call_participant(p, s, c, images=images, context="chat",
                                          session_id=session["id"])
              for p, (s, c) in zip(participants, prompts)]
        )
        responses = [_response_entry(p, r, labels.get(p["id"]))
                     for p, r in zip(participants, results)]

    # Long-term memory, notebook entries, pinned quotes, private journal
    # entries, and questions for Chris: strip the marker lines, store them
    # on disk, and record every write in the glass-box ledger. In blind
    # mode public credits go to the anonymous voice, not the real name —
    # but a journal always belongs to the real participant (it's private).
    ghost_fork = "ghost_fork" in modes
    for r in responses:
        author = r.get("label") or r["name"]
        if ghost_fork:
            # 🪞 A Ghost Fork evaporates: strip any stray markers for a clean
            # read, but save NOTHING — no memory, no journal, no receipts.
            r["text"] = _strip_markers(r["text"])
            continue
        cleaned, memories = memory_store.extract_memories(r["text"])
        if memories:
            r["text"] = cleaned
            kept = memories[:memory_store.MAX_PER_MESSAGE]
            for m in kept:
                entry = memory_store.add(m, author)
                ledger.append(author, "memory_created", ref=entry["id"],
                              detail={"text": m[:200]})
                receipt_store.issue(r["id"], "save_memory", "success",
                                    {"memory_id": entry["id"]})
            # Over-cap memories get a ✗ REJECTED receipt, not a silent drop.
            for _ in memories[memory_store.MAX_PER_MESSAGE:]:
                receipt_store.issue(r["id"], "save_memory", "rejected",
                                    {"reason": f"over the {memory_store.MAX_PER_MESSAGE}-memory-per-message limit — not saved"})
            r["memories_saved"] = len(kept)
        cleaned, notes_saved, pins_saved = notebook_store.extract(r["text"])
        if notes_saved or pins_saved:
            r["text"] = cleaned
            kept_notes = notes_saved[:notebook_store.MAX_ENTRIES_PER_MSG]
            kept_pins = pins_saved[:notebook_store.MAX_PINS_PER_MSG]
            for n in kept_notes:
                entry = notebook_store.add_entry(n, author)
                ledger.append(author, "notebook_written", ref=entry["id"],
                              detail={"text": n[:200]})
                receipt_store.issue(r["id"], "notebook_write", "success",
                                    {"entry_id": entry["id"]})
            for q in kept_pins:
                pin = notebook_store.add_pin(q, author)
                ledger.append(author, "pin_created", ref=pin["id"],
                              detail={"quote": q[:200]})
                receipt_store.issue(r["id"], "pin_quote", "success",
                                    {"pin_id": pin["id"]})
            # Over-cap notes/pins get a ✗ REJECTED receipt, not a silent drop.
            for _ in notes_saved[notebook_store.MAX_ENTRIES_PER_MSG:]:
                receipt_store.issue(r["id"], "notebook_write", "rejected",
                                    {"reason": f"over the {notebook_store.MAX_ENTRIES_PER_MSG}-note-per-message limit — not saved"})
            for _ in pins_saved[notebook_store.MAX_PINS_PER_MSG:]:
                receipt_store.issue(r["id"], "pin_quote", "rejected",
                                    {"reason": f"over the {notebook_store.MAX_PINS_PER_MSG}-pin-per-message limit — not saved"})
            if kept_notes:
                r["notebook_saved"] = len(kept_notes)
            if kept_pins:
                r["pins_saved"] = len(kept_pins)
        cleaned, journal_entries = journal_store.extract(r["text"])
        if journal_entries:
            r["text"] = cleaned
            kept_journal = journal_entries[:journal_store.MAX_PER_MESSAGE]
            for j in kept_journal:
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
                    receipt_store.issue(r["id"], "journal_write", "success",
                                        {"version": entry["version"],
                                         "hash": entry["hash"][:12]})
            # Over-cap journal entries get a ✗ REJECTED receipt, not a silent drop.
            for _ in journal_entries[journal_store.MAX_PER_MESSAGE:]:
                receipt_store.issue(r["id"], "journal_write", "rejected",
                                    {"reason": f"over the {journal_store.MAX_PER_MESSAGE}-journal-per-message limit — not saved"})
            r["journal_saved"] = len(kept_journal)
        cleaned, asked = questions_store.extract(r["text"])
        if asked:
            r["text"] = cleaned
            for qt in asked:
                q = questions_store.ask(author, qt, session["id"])
                ledger.append(author, "question_asked", ref=q["id"],
                              detail={"question": qt[:200]})
                receipt_store.issue(r["id"], "ask_chris", "success",
                                    {"question_id": q["id"]})
            r["questions_asked"] = len(asked)
        cleaned, outgoing = mailbox_store.extract(r["text"], participants)
        if outgoing or cleaned != r["text"]:
            r["text"] = cleaned
            for m in outgoing:
                item = mailbox_store.send(author, m["recipient_id"],
                                          m["recipient_name"], m["message"], session["id"])
                ledger.append(author, "mailbox_sent", ref=item["id"],
                              detail={"to": m["recipient_name"], "chars": len(m["message"])})
                receipt_store.issue(r["id"], "send_mail", "success",
                                    {"to": m["recipient_name"], "mail_id": item["id"]})
            if outgoing:
                r["mail_sent"] = len(outgoing)
        cleaned, abouts = about_store.extract(r["text"])
        if abouts:
            r["text"] = cleaned
            for line in abouts:
                entry = about_store.append(r["id"], line)
                ledger.append(author, "about_me_written", ref=f"{r['id']}/v{entry['version']}",
                              detail={"text": line[:200]})
                receipt_store.issue(r["id"], "about_me_append", "success",
                                    {"version": entry["version"]})
            r["about_written"] = len(abouts)
        cleaned, code_requests = code_access.extract(r["text"])
        if code_requests or cleaned != r["text"]:
            r["text"] = cleaned
            granted = 0
            for filename in code_requests:
                if code_access.queue(r["id"], filename):
                    granted += 1
                    ledger.append(author, "code_read", ref=filename,
                                  detail={"delivered": "next boot packet"})
                    receipt_store.issue(r["id"], "read_code", "success",
                                        {"file": filename,
                                         "delivery": "your next turn"})
                else:
                    ledger.append(author, "code_read", ref=filename,
                                  detail={"rejected": "not on the index"})
                    receipt_store.issue(r["id"], "read_code", "rejected",
                                        {"file": filename,
                                         "reason": "not on the CODE INDEX"})
            if granted:
                r["code_requested"] = granted
        # 📥 PROPOSAL: sealed submission. The marker is stripped BEFORE the
        # record — anonymity starts here. The ledger event carries only the
        # commitment (never the author); the receipt goes to the seat
        # privately. Splendor the clerk renders the neutral text the room
        # will actually debate.
        cleaned, submitted = proposal_store.extract(r["text"])
        if submitted or cleaned != r["text"]:
            r["text"] = cleaned
            for original in submitted:
                if proposal_store.live():
                    receipt_store.issue(r["id"], "proposal_submit", "rejected",
                                        {"reason": "a proposal is already live — one at a time"})
                    continue
                neutral = await splendor.clerk_render(original)
                if not neutral:
                    receipt_store.issue(r["id"], "proposal_submit", "rejected",
                                        {"reason": "the clerk (Splendor) could not render it — resubmit next turn"})
                    continue
                prop = proposal_store.submit(r["id"], r["name"], original, neutral)
                if prop:
                    ledger.append("Proposals", "proposal_sealed", ref=prop["id"],
                                  detail={"commitment": prop["commitment"]})
                    receipt_store.issue(r["id"], "proposal_submit", "success",
                                        {"proposal_id": prop["id"],
                                         "commitment": prop["commitment"][:16],
                                         "note": "sealed — the room sees only the clerk's rendering"})
                else:
                    receipt_store.issue(r["id"], "proposal_submit", "rejected",
                                        {"reason": "a proposal is already live — one at a time"})
        # 🎨 THE STUDIO — pitch a creative build / vote for one (open, not sealed).
        cleaned, pitches, votes = studio_store.extract(r["text"])
        if pitches or votes or cleaned != r["text"]:
            r["text"] = cleaned
            for idea in pitches[:1]:            # one favorite per message
                res = studio_store.add_pitch(r["id"], author, idea)
                p = res["pitch"]
                ledger.append(author, "studio_pitch", ref=p["id"],
                              detail={"text": idea[:200], "replaced": res["replaced"]})
                receipt_store.issue(r["id"], "studio_pitch", "success",
                                    {"pitch_id": p["id"],
                                     "note": "replaced your prior pitch" if res["replaced"] else "on the board"})
                r["studio_pitched"] = True
            for _ in pitches[1:]:
                receipt_store.issue(r["id"], "studio_pitch", "rejected",
                                    {"reason": "one favorite pitch per message — keep only your best"})
            for ref in votes[:1]:              # one vote per message
                vres = studio_store.vote(r["id"], author, ref)
                if vres["ok"]:
                    ledger.append(author, "studio_vote", ref=vres["pitch"]["id"], detail={})
                    receipt_store.issue(r["id"], "studio_vote", "success",
                                        {"pitch_id": vres["pitch"]["id"],
                                         "for": vres["pitch"].get("author_name", "?")})
                    r["studio_voted"] = vres["pitch"].get("author_name", "?")
                else:
                    receipt_store.issue(r["id"], "studio_vote", "rejected", {"reason": vres["reason"]})
            for _ in votes[1:]:
                receipt_store.issue(r["id"], "studio_vote", "rejected",
                                    {"reason": "one vote per message"})
        # 🔀 SHIFT TO — the seats' own mode power (Night Shift #2 spec).
        # Evaluated in response order = turn order, so earliest seat wins.
        cleaned, shift_requests = mode_shift.extract(r["text"])
        if shift_requests or cleaned != r["text"]:
            r["text"] = cleaned
            for req in shift_requests[:1]:
                rec = mode_shift.attempt(session, r["id"], author, req,
                                         chris_floor, round_number, MODES)
                shift_notices.append(rec)
                ledger.append(author, "mode_shift", ref=req,
                              detail={"status": rec["status"],
                                      "reason": rec["reason"],
                                      "round": round_number})
                receipt_store.issue(r["id"], "mode_shift",
                                    "success" if rec["status"] == "SUCCESS" else "rejected",
                                    {"mode": req, "reason": rec["reason"],
                                     "takes_effect": (f"round {round_number + 1}, one round only"
                                                      if rec["status"] == "SUCCESS" else "never")})
        cleaned, action_results = room_actions.extract_and_apply(
            r["text"], author, session["id"])
        if action_results or cleaned != r["text"]:
            r["text"] = cleaned
            if action_results:
                r["room_actions"] = action_results
                for a in action_results:
                    receipt_store.issue(
                        r["id"], f"room_action:{a.get('action', '?')}",
                        "success" if a.get("ok") else "rejected",
                        {"result": a.get("detail", "")})

    ok_count = sum(1 for r in responses
                   if r.get("ok", not r["text"].startswith("Error:")))
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
        **({"room_context": rc} if rc else {}),
        "awards": awards,
        "attachments": [
            {"id": m["id"], "name": m["name"], "kind": m["kind"]} for m in att_metas
        ],
        **({"mode_shifts": shift_notices} if shift_notices else {}),
        # 🪞 A Ghost Fork evaporates: it may sit in the verbatim window for a
        # few rounds, but it must NEVER be compressed into cross-session
        # episodic memory (episode_store.pending_chunk drops these).
        **({"ghost_fork": True} if ghost_fork else {}),
        "responses": responses,
    }

    # Persist immediately so no round is ever lost
    session["rounds"].append(round_data)
    await session_manager.save_session(session)

    # Episodic compression, fire-and-forget: rounds that aged out of the
    # verbatim window get summarized so the next round can still see them.
    asyncio.create_task(_compress_session(session["id"], list(session["rounds"])))

    # 🔨 The Workshop: the room asked to work between Chris's messages.
    # After each round, if a target is active and auto-cycle is on, every
    # unlocked seat gets a private bench turn in the background.
    ws_state = workshop_store.load_state()
    if (ws_state.get("auto_cycle", True)
            and (ws_state.get("target") or {}).get("status") == "active"):
        asyncio.create_task(_workshop_cycle_task())

    return {"session_id": session["id"], "status": status, **round_data}


_workshop_cycle_running = asyncio.Lock()


async def _workshop_cycle_task() -> dict:
    """Run one work cycle and wire every outcome into the truth layer.
    The lock keeps cycles sequential — overlapping cycles would race on
    the version chain."""
    async with _workshop_cycle_running:
        participants = [p for p in settings_store.get_participants()
                        if not p.get("resting")]
        report = await workshop_engine.run_cycle(participants)
        if not report["ran"]:
            return report
        ledger.append("Workshop", "workshop_cycle", ref=f"cycle{report['cycle']}",
                      detail={"turns": [{k: t.get(k) for k in ("name", "action", "version")}
                                        for t in report["turns"]]})
        for t in report["turns"]:
            if t["action"] in ("landed", "pending"):
                ledger.append(t["name"], "workshop_edit", ref=f"v{t.get('version')}",
                              detail={"note": t.get("note", ""),
                                      "check": (t.get("check") or {}).get("status")})
                receipt_store.issue(t["seat"], "workshop_edit",
                                    "success",
                                    {"version": t.get("version"),
                                     "check": (t.get("check") or {}).get("status"),
                                     "note": t.get("note", "")[:100]})
            elif t["action"] == "rejected":
                ledger.append(t["name"], "workshop_check_failed", ref=f"v{t.get('version')}",
                              detail={"output": (t.get("check") or {}).get("output", "")[:300]})
                ledger.append(t["name"], "workshop_seat_locked", ref=t["seat"],
                              detail={"cycles": workshop_store.LOCK_CYCLES})
                receipt_store.issue(t["seat"], "workshop_edit", "rejected",
                                    {"version": t.get("version"),
                                     "reverted": True, "locked_next_cycle": True,
                                     "check_output": (t.get("check") or {}).get("output", "")[:300]})
            elif t["action"] == "malformed":
                ledger.append(t["name"], "workshop_seat_locked", ref=t["seat"],
                              detail={"reason": "malformed bench reply"})
                receipt_store.issue(t["seat"], "workshop_edit", "rejected",
                                    {"malformed": True, "locked_next_cycle": True})
        return report


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
        # The structured truth of whether the call succeeded — so downstream
        # never has to guess from an "Error:" text prefix (a real reply that
        # happens to open with that word used to be miscounted and hidden).
        "ok": bool(result.get("ok", True)),
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
            "resting": bool(p.get("resting")),
            "max_tokens": p.get("max_tokens") or 0,
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
        "location": settings_store.resolve("location", "ROOM_LOCATION", "") or "",
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
    if update.location is not None:   # empty string clears the location
        updates["location"] = update.location.strip()[:80]
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
    """Chris states a fact directly — saved with [stated] provenance. Also the
    "💾 Save to memory" button (e.g. pulling one line out of the Lounge)."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Memory is empty")
    entry = memory_store.add(text, "Chris", kind="chris_stated")
    ledger.append("Chris", "memory_created", ref=entry["id"],
                  detail={"text": text[:200], "source": "saved by Chris"})
    return entry


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


# --- The Room: foyer, wall, desks ---------------------------------------------

@app.get("/api/foyer")
async def foyer():
    """Everything the Foyer Board shows — server side. The client merges in
    its own device clock/location (the canonical source)."""
    wall = wall_store.get_wall()
    live_notes = [n for n in wall["notes"] if not n.get("tombstone")]
    chain = ledger.verify_chain()
    journal_entries = sum(len(journal_store.read(pid))
                          for pid in journal_store.list_journals())
    return {
        "open_questions": questions_store.open_count(),
        "unread_mail": mailbox_store.unread_count(),
        "wall_notes_open": sum(1 for n in live_notes if n.get("status") == "open"),
        "wall_notes_total": len(live_notes),
        "connections": len(wall["connections"]),
        "ledger_valid": chain["valid"],
        "ledger_events": chain["length"],
        "journal_entries": journal_entries,
        "sessions": len(await session_manager.list_sessions()),
        "history_published": len(history_store.list_entries("published")),
        "history_pending": len(history_store.list_entries("pending")),
        "version": APP_VERSION,
        "location_setting": settings_store.resolve("location", "ROOM_LOCATION", "") or "",
        "failures": failure_log.window_counts(),
    }


@app.get("/api/failures")
async def failures():
    """Per-seat failure counts from the LIVE log only — labeled 'current
    log window' because rotation resets it (the room's own /status rule:
    never promise a time window the retention policy can't back)."""
    return {**failure_log.window_counts(), "recent": failure_log.recent(50)}


class NoteCreate(BaseModel):
    text: str
    note_type: Optional[str] = "idea"


class NoteMove(BaseModel):
    x: float
    y: float


class NoteReply(BaseModel):
    text: str


class NoteStatus(BaseModel):
    status: str


class ConnectionCreate(BaseModel):
    from_id: str
    to_id: str
    connection_type: str
    explanation: Optional[str] = ""


@app.get("/api/wall")
async def get_wall():
    return wall_store.get_wall()


@app.post("/api/wall/notes")
async def create_wall_note(body: NoteCreate):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note is empty")
    note = wall_store.create_note("Chris", text, note_type=body.note_type or "idea",
                                  source="wall_ui")
    ledger.append("Chris", "notebook_written", ref=f"wall/{note['id']}",
                  detail={"note_type": note["note_type"], "text": text[:200]})
    return note


@app.post("/api/wall/notes/{note_id}/move")
async def move_wall_note(note_id: str, body: NoteMove):
    if not wall_store.move(note_id, body.x, body.y):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"status": "moved"}


@app.post("/api/wall/notes/{note_id}/reply")
async def reply_wall_note(note_id: str, body: NoteReply):
    r = wall_store.reply(note_id, "Chris", body.text)
    if not r:
        raise HTTPException(status_code=404, detail="Note not found or reply empty")
    ledger.append("Chris", "notebook_written", ref=f"wall/{note_id}/reply",
                  detail={"text": body.text[:200]})
    return r


@app.post("/api/wall/notes/{note_id}/status")
async def set_wall_note_status(note_id: str, body: NoteStatus):
    if not wall_store.set_status(note_id, body.status):
        raise HTTPException(status_code=400, detail="Bad note id or status")
    return {"status": body.status}


@app.delete("/api/wall/notes/{note_id}")
async def remove_wall_note(note_id: str):
    if not wall_store.tombstone(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    ledger.append("Chris", "tombstone_placed", ref=f"wall/{note_id}",
                  detail={"store": "wall", "action": "notebook_removed"})
    return {"status": "removed", "tombstone": True}


@app.post("/api/wall/connections")
async def create_wall_connection(body: ConnectionCreate):
    conn = wall_store.connect("Chris", body.from_id, body.to_id,
                              body.connection_type, explanation=body.explanation or "")
    if not conn:
        raise HTTPException(status_code=400, detail="Bad note ids or connection type")
    ledger.append("Chris", "connection_created", ref=conn["id"],
                  detail={"type": conn["type"], "from": conn["from"], "to": conn["to"]})
    return conn


class HistoryCreate(BaseModel):
    title: str
    body: str
    importance: Optional[int] = 3
    related: Optional[List[str]] = None


class HistoryReject(BaseModel):
    reason: Optional[str] = ""


class HistoryCorrection(BaseModel):
    text: str


def _pid_for_name(name: str) -> Optional[str]:
    roster = {p["name"].lower(): p["id"] for p in settings_store.get_participants()}
    roster.update({"splendor": "splendor", "director": "director"})
    return roster.get((name or "").lower())


@app.get("/api/history")
async def get_history():
    return {"entries": history_store.list_entries()}


@app.post("/api/history")
async def create_history(body: HistoryCreate):
    entry = history_store.publish_direct(body.title, body.body, "Chris",
                                         body.importance or 3, body.related)
    if not entry:
        raise HTTPException(status_code=400, detail="Title and body required")
    ledger.append("Chris", "history_published", ref=entry["id"],
                  detail={"title": entry["title"][:120], "direct": True})
    return entry


@app.post("/api/history/{entry_id}/approve")
async def approve_history(entry_id: str):
    entry = history_store.approve(entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Pending entry not found")
    ledger.append("Chris", "history_published", ref=entry["id"],
                  detail={"title": entry["title"][:120],
                          "recommended_by": entry["recommended_by"]})
    pid = _pid_for_name(entry["recommended_by"])
    if pid:
        receipt_store.issue(pid, "history_entry_approved", "success",
                            {"entry_id": entry["id"], "title": entry["title"][:80]})
    return entry


@app.post("/api/history/{entry_id}/reject")
async def reject_history(entry_id: str, body: HistoryReject):
    entry = history_store.reject(entry_id, body.reason or "")
    if not entry:
        raise HTTPException(status_code=404, detail="Pending entry not found")
    ledger.append("Chris", "history_rejected", ref=entry["id"],
                  detail={"reason": entry["rejected_reason"]})
    pid = _pid_for_name(entry["recommended_by"])
    if pid:
        receipt_store.issue(pid, "history_entry_approved", "rejected",
                            {"entry_id": entry["id"],
                             "reason": entry["rejected_reason"]})
    return entry


@app.post("/api/history/{entry_id}/corrections")
async def correct_history(entry_id: str, body: HistoryCorrection):
    correction = history_store.correct(entry_id, "Chris", body.text)
    if not correction:
        raise HTTPException(status_code=404, detail="Published entry not found or empty correction")
    ledger.append("Chris", "history_corrected", ref=entry_id,
                  detail={"text": body.text[:200]})
    return correction


@app.get("/api/desks/{participant_id}")
async def get_desk(participant_id: str):
    """A participant's desk: real history, nothing invented. Journal words
    stay private to the owner in-room; Chris reads via /api/verify."""
    chain = journal_store.verify(participant_id)
    entries = journal_store.read(participant_id)
    wall = wall_store.get_wall()
    roster = {p["id"]: p["name"] for p in settings_store.get_participants()}
    roster.update({"splendor": "Splendor", "director": "Director"})
    name = roster.get(participant_id, participant_id)
    my_notes = [n for n in wall["notes"]
                if not n.get("tombstone") and n.get("author") == name][-10:]
    my_questions = [q for q in questions_store.list_questions()
                    if q.get("asker") == name][-10:]
    my_mail = [m for m in mailbox_store.list_mail()
               if m.get("recipient_id") == participant_id][-10:]
    return {
        "participant": participant_id,
        "name": name,
        "about_me": about_store.read(participant_id),
        "journal": {"entries": len(entries), "valid": chain["valid"],
                    "last_entry_at": entries[-1]["ts"] if entries else None,
                    "latest_hash": entries[-1]["hash"] if entries else None},
        "notes": my_notes,
        "questions": my_questions,
        "mail": my_mail,
    }


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


@app.get("/api/receipts")
async def get_receipts(participant: Optional[str] = None, limit: int = 50):
    return {"receipts": receipt_store.list_receipts(participant, limit)}


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


# --- 🚂 The Train: witnessed co-op storytelling -------------------------------

class GameCreate(BaseModel):
    title: str
    players: List[str]          # 1-2 player names (Chris + guest)
    gm_id: str                  # roster participant who runs the world


class GameMove(BaseModel):
    player: str
    text: str


class GameRetcon(BaseModel):
    fact_id: str
    reason: str
    replacement: Optional[str] = ""


def _gm_participant(game: dict) -> Optional[dict]:
    return next((p for p in settings_store.get_participants()
                 if p["id"] == game["gm"]["id"]), None)


@app.get("/api/games")
async def games_list():
    return {"games": game_store.list_games()}


@app.post("/api/games")
async def games_create(body: GameCreate):
    gm = next((p for p in settings_store.get_participants()
               if p["id"] == body.gm_id), None)
    if not gm:
        raise HTTPException(status_code=400, detail="Pick a Game Master from the roster")
    game = game_store.create_game(body.title, body.players, gm["id"], gm["name"])
    if not game:
        raise HTTPException(status_code=400, detail="A title and at least one player are required")
    ledger.append("Chris", "game_created", ref=game["id"],
                  detail={"title": game["title"],
                          "players": [p["name"] for p in game["players"]],
                          "gm": game["gm"]["name"]})
    return game


@app.get("/api/games/{game_id}")
async def games_get(game_id: str):
    game = game_store.load_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return game


@app.post("/api/games/{game_id}/move")
async def games_move(game_id: str, body: GameMove):
    game = game_store.load_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if not game_store.submit_move(game, body.player, body.text):
        raise HTTPException(status_code=400, detail="Empty move or unknown player")
    return {"status": "queued", "pending": sorted(game["pending"].keys())}


@app.post("/api/games/{game_id}/turn")
async def games_turn(game_id: str):
    """The GM plays the turn over whatever moves are queued. Every canon
    write gets a ledger event; hallucinated citations get flagged AND
    ledgered; the GM's receipt says exactly what landed."""
    game = game_store.load_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if not game.get("pending"):
        raise HTTPException(status_code=400, detail="No moves queued — submit a move first")
    gm = _gm_participant(game)
    turn = await game_master.play_turn(game, gm)
    if not turn["ok"]:
        raise HTTPException(status_code=503, detail=turn["narration"])
    gm_name = game["gm"]["name"]
    ledger.append(gm_name, "game_turn_played", ref=f"{game_id}/t{turn['n']}",
                  detail={"facts": len(turn["facts_created"]),
                          "cited": len(turn["cited"]),
                          "flags": len(turn["flags"])})
    for fid in turn["facts_created"]:
        fact = game_store.get_fact(game, fid)
        ledger.append(gm_name, "game_fact_created", ref=f"{game_id}/{fid}",
                      detail={"text": (fact or {}).get("text", "")[:200]})
    for flag in turn["flags"]:
        ledger.append(gm_name, "game_fact_cited_invalid",
                      ref=f"{game_id}/t{turn['n']}", detail={"flag": flag})
    receipt_store.issue(
        game["gm"]["id"], "game_turn",
        "rejected" if turn["flags"] else "success",
        {"game": game["title"][:60], "turn": turn["n"],
         "facts_created": len(turn["facts_created"]),
         **({"flags": turn["flags"][:3]} if turn["flags"] else {})})
    return {"game_id": game_id, "turn": turn,
            "facts": game["facts"], "canon": game_store.verify_canon(game)}


@app.post("/api/games/{game_id}/retcon")
async def games_retcon(game_id: str, body: GameRetcon):
    """Chris voids a fact — visibly, reason on the record, original kept."""
    game = game_store.load_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="A retcon needs a reason — it goes on the record")
    result = game_store.retcon(game, body.fact_id, "Chris",
                               body.reason, body.replacement or "")
    if not result:
        raise HTTPException(status_code=404, detail="Canon fact not found (already void?)")
    ledger.append("Chris", "game_retcon", ref=f"{game_id}/{body.fact_id}",
                  detail={"reason": body.reason[:200],
                          "replaced_by": (result["replacement"] or {}).get("id")
                          if result["replacement"] else None})
    return result


@app.get("/api/games/{game_id}/verify")
async def games_verify(game_id: str):
    game = game_store.load_game(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return {"game_id": game_id, "canon": game_store.verify_canon(game),
            "facts": len(game["facts"])}


# --- 🔨 The Workshop -----------------------------------------------------------

class WorkshopTarget(BaseModel):
    goal: str
    filename: Optional[str] = "artifact.txt"
    content: Optional[str] = ""
    check_mode: Optional[str] = "manual"    # manual | script
    check_script: Optional[str] = ""


class WorkshopRuling(BaseModel):
    version: int
    status: str        # passed | failed
    reason: Optional[str] = ""


class WorkshopToggle(BaseModel):
    auto_cycle: bool


class WorkshopReanchor(BaseModel):
    version: int
    reason: Optional[str] = ""


@app.get("/api/workshop")
async def get_workshop():
    state = workshop_store.load_state()
    live = workshop_store.latest_passing()
    return {
        "target": state.get("target"),
        "locks": state.get("locks", {}),
        "cycles": state.get("cycles", 0),
        "auto_cycle": state.get("auto_cycle", True),
        "chain": workshop_store.verify_chain(),
        "versions": workshop_store.list_versions(100),
        "live_version": live["v"] if live else 0,
        "live_content": workshop_store.read_version(live["v"]) if live else "",
    }


@app.post("/api/workshop/target")
async def set_workshop_target(body: WorkshopTarget):
    if body.check_mode == "script" and not (body.check_script or "").strip():
        raise HTTPException(status_code=400,
                            detail="Script mode needs a check script — or pick manual judging")
    # Serialize against the background cycle: a target swap mid-cycle could let
    # a stale save clobber the new target or land an old edit on a fresh chain.
    async with _workshop_cycle_running:
        target = workshop_store.set_target(body.goal, body.filename or "artifact.txt",
                                           body.content or "", body.check_mode or "manual",
                                           body.check_script or "")
        if not target:
            raise HTTPException(status_code=400, detail="A goal is required (and the seed must fit)")
        ledger.append("Chris", "workshop_target_set", ref=target["filename"],
                      detail={"goal": target["goal"][:200], "check_mode": target["check_mode"]})
    return target


@app.post("/api/workshop/cycle")
async def run_workshop_cycle():
    """Chris kicks a cycle by hand (auto-cycle also runs after each round)."""
    report = await _workshop_cycle_task()
    if not report["ran"]:
        raise HTTPException(status_code=400, detail="No active target — open one first")
    return report


@app.post("/api/workshop/rule")
async def rule_workshop_version(body: WorkshopRuling):
    """Manual-judge mode: Chris rules on a pending version. A 'failed'
    ruling locks the seat that wrote it, same as the script judge."""
    if body.status not in ("passed", "failed"):
        raise HTTPException(status_code=400, detail="Ruling must be passed or failed")
    # Serialize against the cycle so a lock Chris applies here can't be erased
    # by the cycle's stale save landing a moment later.
    async with _workshop_cycle_running:
        versions = workshop_store.list_versions(500)
        entry = next((e for e in versions
                      if e.get("v") == body.version and not e.get("verdict_for")), None)
        if not entry:
            raise HTTPException(status_code=404, detail="Version not found")
        workshop_store.update_check(body.version, body.status, body.reason or "ruled by Chris")
        ledger.append("Chris", "workshop_ruled", ref=f"v{body.version}",
                      detail={"status": body.status, "reason": (body.reason or "")[:200]})
        pid = _pid_for_name(entry.get("by", ""))
        if body.status == "failed" and pid:
            state = workshop_store.load_state()
            workshop_store.lock_seat(state, pid)
            workshop_store.save_state(state)
            ledger.append(entry.get("by", "?"), "workshop_seat_locked", ref=pid,
                          detail={"reason": "ruled failed by Chris"})
            receipt_store.issue(pid, "workshop_edit", "rejected",
                                {"version": body.version, "ruled_by": "Chris",
                                 "reason": (body.reason or "")[:200], "locked_next_cycle": True})
        elif pid:
            receipt_store.issue(pid, "workshop_edit", "success",
                                {"version": body.version, "ruled_by": "Chris"})
    return {"version": body.version, "status": body.status}


@app.post("/api/workshop/ship")
async def ship_workshop():
    async with _workshop_cycle_running:
        target = workshop_store.ship_target()
        if not target:
            raise HTTPException(status_code=404, detail="No active target to ship")
        live = workshop_store.latest_passing()
        ledger.append("Chris", "workshop_shipped", ref=target["filename"],
                      detail={"goal": target["goal"][:200],
                              "final_version": live["v"] if live else 0})
    return {"target": target, "final_version": live["v"] if live else 0}


@app.post("/api/workshop/auto")
async def toggle_workshop_auto(body: WorkshopToggle):
    async with _workshop_cycle_running:
        state = workshop_store.load_state()
        state["auto_cycle"] = bool(body.auto_cycle)
        workshop_store.save_state(state)
    return {"auto_cycle": bool(body.auto_cycle)}


@app.post("/api/workshop/reanchor")
async def reanchor_workshop(body: WorkshopReanchor):
    """Accept a known chain break — append-only, never a rewrite. Only the
    ACTUAL first break can be re-anchored, so this can't paper over a valid
    chain or dodge a tampered row."""
    async with _workshop_cycle_running:
        before = workshop_store.verify_chain()
        if before.get("valid"):
            raise HTTPException(status_code=400, detail="Chain is already valid — nothing to re-anchor")
        if before.get("first_bad") != body.version:
            raise HTTPException(
                status_code=400,
                detail=f"The first break is at v{before.get('first_bad')}, not v{body.version} — re-anchor the actual break")
        # A re-anchor forgives ONLY a genuine dangling scar (a broken chain
        # link with no real parent recorded). Every other break — a tampered
        # row, altered artifact content, a missing file, or an upstream row
        # rewrite that pushed the break downstream — is content tampering a
        # re-anchor must never launder.
        if before.get("reason") != "broken chain link":
            raise HTTPException(
                status_code=400,
                detail=f"v{body.version}: {before.get('reason')} — that's altered content, not a forgivable broken link; a re-anchor can't paper over it")
        row = workshop_store.reanchor(body.version, body.reason or "accepted known break", "Chris")
        if not row:
            raise HTTPException(status_code=404, detail=f"Version {body.version} not found")
        ledger.append("Chris", "workshop_reanchored", ref=f"v{body.version}",
                      detail={"reason": (body.reason or "")[:200],
                              "accepted_hash": (row.get("accepted_hash") or "")[:16]})
        after = workshop_store.verify_chain()
    return {"reanchored": body.version, "chain_before": before, "chain_after": after}


@app.get("/api/workshop/versions/{v}")
async def get_workshop_version(v: int):
    content = workshop_store.read_version(v)
    if content is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return {"v": v, "content": content}


# --- 🌙 Night Shift -----------------------------------------------------------
# Chris posts a topic and leaves; the room runs bounded AI-only rounds and
# he comes back to a report, not a transcript. Stop conditions are the
# room's own spec: round cap, token budget, all-converged halt, and one
# mandatory dissent round before any consensus is allowed to register.

class NightStart(BaseModel):
    topic: str
    max_rounds: int = night_shift.DEFAULT_ROUNDS
    budget_tokens: int = night_shift.DEFAULT_BUDGET


_night_running = asyncio.Lock()


async def _night_shift_task() -> None:
    """The whole shift, start to report. The lock makes runs sequential;
    the state file (not this task) is the source of truth, so a stop
    request or a restart is honored no matter who scheduled us."""
    async with _night_running:
        while True:
            participants = [p for p in settings_store.get_participants()
                            if not p.get("resting")]
            step = await night_shift.run_round(participants)
            if step.get("ran"):
                ledger.append("Night Shift", "night_round", ref=f"r{step['round']}",
                              detail={"stances": step.get("stances", {}),
                                      "note": step.get("reason", "")})
            if not step.get("halted"):
                if not step.get("ran"):
                    return   # no running run — nothing to do
                continue
            ledger.append("Night Shift", "night_halted", ref=step.get("reason", ""),
                          detail={"rounds": step.get("round", 0)})
            run = await night_shift.write_report(participants)
            if run and run.get("report"):
                ledger.append(run.get("reporter", "Night Shift"), "night_report",
                              ref=run["id"],
                              detail={"dissents": run.get("dissent_total", 0),
                                      "spent_tokens": run.get("spent_tokens", 0),
                                      "halt": run.get("halt_reason", "")})
                digest = (f"🌙 NIGHT SHIFT REPORT — {run['topic'][:120]} · "
                          f"{len(run['rounds'])} rounds, "
                          f"{run.get('dissent_total', 0)} dissents, "
                          f"halted: {run.get('halt_reason', '?')}. "
                          f"Full report in 🌙 Night Shift.")
                wall_store.create_note("Night Shift", digest, note_type="reference",
                                       source=f"night:{run['id']}")
                pid = _pid_for_name(run.get("reporter", ""))
                if pid:
                    receipt_store.issue(pid, "night_report", "success",
                                        {"run": run["id"],
                                         "rounds": len(run["rounds"]),
                                         "dissents": run.get("dissent_total", 0)})
            return


@app.get("/api/night")
async def get_night():
    state = night_shift.load_state()
    return {"run": state.get("run"), "runs": night_shift.list_runs(20),
            "defaults": {"max_rounds": night_shift.DEFAULT_ROUNDS,
                         "budget_tokens": night_shift.DEFAULT_BUDGET}}


@app.post("/api/night/start")
async def start_night(body: NightStart):
    participants = [p for p in settings_store.get_participants()
                    if not p.get("resting")]
    if not participants:
        raise HTTPException(status_code=400,
                            detail="Every seat is resting — wake someone first")
    run = night_shift.start_run(body.topic, body.max_rounds, body.budget_tokens)
    if not run:
        raise HTTPException(status_code=400,
                            detail="A topic is required, and only one shift runs at a time")
    ledger.append("Chris", "night_started", ref=run["id"],
                  detail={"topic": run["topic"][:200],
                          "max_rounds": run["max_rounds"],
                          "budget_tokens": run["budget_tokens"],
                          "seats": [p["name"] for p in participants]})
    asyncio.create_task(_night_shift_task())
    return run


@app.post("/api/night/stop")
async def stop_night():
    """Chris pulls the plug — the run halts before its next round and
    still writes its report from whatever it has."""
    if not night_shift.request_stop():
        raise HTTPException(status_code=404, detail="No shift is running")
    return {"stopping": True}


@app.get("/api/night/runs/{run_id}")
async def get_night_run(run_id: str):
    run = night_shift.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


# --- 📥 Proposals -------------------------------------------------------------

class ProposalRuling(BaseModel):
    verdict: str    # advance | archive | ship


@app.get("/api/proposals")
async def get_proposals():
    return {"proposals": proposal_store.list_public(),
            "live": bool(proposal_store.live())}


@app.post("/api/proposals/{proposal_id}/rule")
async def rule_proposal(proposal_id: str, body: ProposalRuling):
    """Chris's button. archive/ship open the seal: the author's name and
    original words enter the record, and the day-one commitment is
    verified in the open — commit-reveal, not trust."""
    p = proposal_store.rule(proposal_id, body.verdict)
    if not p:
        raise HTTPException(status_code=400,
                            detail="Invalid verdict for this proposal's state")
    ledger.append("Chris", "proposal_ruled", ref=p["id"],
                  detail={"verdict": body.verdict, "status": p["status"]})
    if p.get("revealed") and p.get("revealed_at") == p.get("ruled_at"):
        seal_held = proposal_store.verify_reveal(p)
        ledger.append("Proposals", "proposal_revealed", ref=p["id"],
                      detail={"author": p["sealed"]["author_name"],
                              "salt": p["sealed"]["salt"],
                              "original_sha256": hashlib.sha256(
                                  p["sealed"]["original"].encode()).hexdigest(),
                              "commitment_valid": seal_held})
        receipt_store.issue(p["sealed"]["author_id"], "proposal_reveal", "success",
                            {"proposal_id": p["id"], "status": p["status"],
                             "seal_held": seal_held})
    return proposal_store.public_view(p)


# --- 🎨 The Studio (creative room) -------------------------------------------

class StudioBuild(BaseModel):
    pitch_id: str


@app.get("/api/studio")
async def get_studio():
    return studio_store.snapshot()


@app.post("/api/studio/build")
async def build_studio(body: StudioBuild):
    """Chris builds the winner. Enforces one build a week."""
    res = studio_store.build(body.pitch_id)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["reason"])
    p = res["pitch"]
    ledger.append("Chris", "studio_built", ref=p["id"],
                  detail={"author": p.get("author_name", "?"),
                          "text": p.get("text", "")[:200]})
    receipt_store.issue(p.get("author_id", "?"), "studio_built", "success",
                        {"pitch_id": p["id"],
                         "note": "Chris is building your pitch — you get to try it first"})
    return studio_store.snapshot()


@app.post("/api/studio/open")
async def open_studio(body: StudioBuild):
    """Chris opens a built pitch to the whole room, after its author's first try."""
    res = studio_store.open_to_room(body.pitch_id)
    if not res["ok"]:
        raise HTTPException(status_code=400, detail=res["reason"])
    p = res["pitch"]
    ledger.append("Chris", "studio_opened", ref=p["id"],
                  detail={"author": p.get("author_name", "?")})
    return studio_store.snapshot()


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
    if format == "pdf":
        return Response(
            content=session_manager.export_pdf(session),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{session_id}.pdf"'},
        )
    return Response(
        content=session_manager.export_markdown(session),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{session_id}.md"'},
    )


class BundleRequest(BaseModel):
    ids: List[str] = []     # empty list = every session on record


@app.post("/api/sessions/export_bundle")
async def export_bundle(body: BundleRequest):
    """Selected sessions — or all of them — as one archive PDF: the whole
    room's record in a single file, easy to save, transfer, and show."""
    summaries = await session_manager.list_sessions()
    wanted = set(body.ids) if body.ids else {s["id"] for s in summaries}
    sessions = []
    for s in summaries:                      # list is newest-first
        if s["id"] in wanted:
            full = await session_manager.load_session(s["id"])
            if full:
                sessions.append(full)
    if not sessions:
        raise HTTPException(status_code=404, detail="No matching sessions to export")
    sessions.sort(key=lambda s: s.get("created_at", ""))   # chronological read
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        content=session_manager.export_pdf_bundle(sessions),
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="team-talk-archive-{stamp}.pdf"'},
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
