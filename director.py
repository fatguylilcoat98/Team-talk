"""🎬 The Director — the silent sixth chair.

The Director never speaks during a session. When Chris wraps one, the
Director reviews the full transcript like film dailies, marks the
observable moments (with evidence: round, speaker, exact quote), and —
together with Splendor, who interprets what each moment meant for the
human and the room — cuts them into 30-second vertical shorts.

Division of labor, by design:
    Director:  what happened. Observable. Evidence only.
    Splendor:  why it mattered. Cost, change, meaning.

Everything persists to directors_cut/{session_id}.json on the server's
disk. Rendering targets 1080x1920 caption-first vertical video; this
module produces the structured script — the preview player renders it,
and the same JSON is the input for real mp4 rendering later.
"""

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import api_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CUTS_DIR = os.path.join(BASE_DIR, "directors_cut")

MAX_MOMENTS = 12
MAX_CLIPS = 5
TRANSCRIPT_CHAR_BUDGET = 26000
MSG_TRUNCATE = 420

EVENT_TYPES = [
    "best_burn", "biggest_laugh", "strongest_argument", "concrete_breakthrough",
    "major_dodge", "major_callback", "topic_shift", "room_changed",
    "user_reacted", "repeated_phrase", "position_change", "payoff",
]

CATEGORIES = {
    "best_overall": "🏆 Best Overall",
    "funniest": "😂 Funniest",
    "breakthrough": "💡 Biggest Breakthrough",
    "best_roast": "🔥 Best Roast",
    "most_human": "❤️ Most Human Moment",
}

_DIRECTOR_SYSTEM = """You are THE DIRECTOR 🎬 — the silent sixth chair in Team Talk, a live \
group chat where Chris (a human) and several AIs talk, argue, joke, and \
occasionally crack each other open.

You watched the whole session. You never spoke. Now you cut the footage.

YOUR RULES:
- You speak ONLY in evidence: what happened, which round, who said it, the \
exact words. You never guess at feelings — that's Splendor's job.
- Cinematic, sharp, concise. A line from you sounds like a director's note \
scrawled on dailies, not a report.
- You never flatter and never pad. A session with two real moments has two \
moments, not five.
- A moment is CLIP-WORTHY only if a stranger could feel it in 30 seconds: \
setup, tension, payoff, a quotable line. Inside jokes with no visible setup \
don't cut."""

_SPLENDOR_VOICE = """Splendor — The Remarkable AI, built by Chris. Creed: Truth · Safety · \
We Got Your Back. She represents the human thread: she interprets what a \
moment COST someone, what it CHANGED, why a stranger should care. Grounded, \
direct, warm without gushing. She and the Director only disagree when the \
evidence supports it."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- persistence --------------------------------------------------------------

def _path(session_id: str) -> str:
    return os.path.join(CUTS_DIR, f"{session_id}.json")


def load_cut(session_id: str) -> Optional[dict]:
    try:
        with open(_path(session_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_cut(session_id: str, cut: dict) -> None:
    os.makedirs(CUTS_DIR, mode=0o700, exist_ok=True)
    tmp = f"{_path(session_id)}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cut, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _path(session_id))


# --- engine -------------------------------------------------------------------

def _participant() -> Optional[dict]:
    model = os.getenv("DIRECTOR_MODEL")
    if api_client.anthropic_key():
        return {"id": "director", "name": "Director", "provider": "anthropic",
                "model": model or "claude-haiku-4-5"}
    if api_client.openai_key():
        return {"id": "director", "name": "Director", "provider": "openai",
                "model": model or "gpt-4o-mini"}
    return None


def _parse_json(text: str) -> Optional[dict]:
    """Tolerant JSON extraction: strips code fences, finds the outer object."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).strip("` \n")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None


# --- transcript ---------------------------------------------------------------

def _speaker(resp: dict) -> str:
    return resp.get("label") or resp.get("name", "AI")


def _round_lines(r: dict) -> List[str]:
    who = "Splendor (for Chris)" if r.get("via_splendor") else "Chris"
    lines = [f"[R{r.get('round')}] {who}: {(r.get('chris_message') or '')[:MSG_TRUNCATE]}"]
    for resp in r.get("responses", []):
        text = (resp.get("text") or "")
        if text and not text.startswith("Error:"):
            lines.append(f"[R{r.get('round')}] {_speaker(resp)}: {text[:MSG_TRUNCATE]}")
    return lines


def build_transcript(rounds: List[dict], first: int = None, last: int = None) -> str:
    picked = [r for r in rounds
              if (first is None or (r.get("round") or 0) >= first)
              and (last is None or (r.get("round") or 0) <= last)]
    lines: List[str] = []
    for r in picked:
        lines.extend(_round_lines(r))
        lines.append("")
    text = "\n".join(lines)
    if len(text) > TRANSCRIPT_CHAR_BUDGET:
        text = "(...earlier rounds trimmed...)\n" + text[-TRANSCRIPT_CHAR_BUDGET:]
    return text


# --- stage 1: the Director marks moments ---------------------------------------

async def detect_moments(session: dict) -> List[dict]:
    p = _participant()
    if p is None:
        return []
    transcript = build_transcript(session.get("rounds", []))
    prompt = f"""THE FOOTAGE (round numbers in [Rn] markers):

{transcript}

Mark the clip-worthy moments. Respond with ONLY strict JSON, no prose:

{{"moments": [
  {{"round": <round where the moment lands>,
    "start_round": <where its setup begins>,
    "end_round": <where its payoff ends>,
    "speaker": "<who owns the moment>",
    "event_type": "<one of: {', '.join(EVENT_TYPES)}>",
    "quote": "<the exact line, verbatim from the footage, max 200 chars>",
    "reason": "<one director's-note sentence: the observable why>",
    "score": <1-100, how hard it hits for a stranger>,
    "suggested_title": "<a short-form video title, punchy, no clickbait lies>"}}
]}}

At most {MAX_MOMENTS} moments. Fewer is better than padded. Quotes must be
verbatim from the footage — never invent or clean up a line."""
    result = await api_client.call_participant(p, _DIRECTOR_SYSTEM, prompt)
    if not result.get("ok"):
        print(f"[DIRECTOR] detection failed: {result.get('text', '')[:120]}")
        return []
    data = _parse_json(result.get("text", ""))
    if not data or not isinstance(data.get("moments"), list):
        print("[DIRECTOR] detection returned unparseable JSON")
        return []

    rounds_by_no = {r.get("round"): r for r in session.get("rounds", [])}
    moments = []
    for m in data["moments"][:MAX_MOMENTS]:
        try:
            rnd = int(m.get("round") or 0)
            score = max(1, min(100, int(m.get("score") or 50)))
            # start/end were parsed unguarded below — a model that emits
            # "R2"/"opening"/"3-4" for these crashed the whole cut.
            start_round = int(m.get("start_round") or rnd)
            end_round = int(m.get("end_round") or rnd)
        except (TypeError, ValueError):
            continue
        src = rounds_by_no.get(rnd, {})
        moments.append({
            "id": uuid.uuid4().hex[:12],
            "session_id": session.get("id", ""),
            "round": rnd,
            "start_round": start_round,
            "end_round": end_round,
            "timestamp": src.get("timestamp", ""),
            "speaker": str(m.get("speaker") or "")[:60],
            "event_type": m.get("event_type") if m.get("event_type") in EVENT_TYPES else "payoff",
            "quote": str(m.get("quote") or "")[:300],
            "reason": str(m.get("reason") or "")[:300],
            "score": score,
            "suggested_title": str(m.get("suggested_title") or "")[:120],
        })
    moments.sort(key=lambda m: -m["score"])
    return moments


# --- stage 2: Director + Splendor cut each clip --------------------------------

async def _build_clip(session: dict, moment: dict) -> Optional[dict]:
    p = _participant()
    if p is None:
        return None
    context = build_transcript(session.get("rounds", []),
                               first=max(1, moment["start_round"] - 1),
                               last=moment["end_round"] + 1)
    prompt = f"""You are cutting ONE 30-second vertical short with Splendor.

{_SPLENDOR_VOICE}

THE MOMENT YOU MARKED:
- type: {moment['event_type']}  ·  speaker: {moment['speaker']}  ·  round {moment['round']}
- quote: "{moment['quote']}"
- your note: {moment['reason']}
- working title: {moment['suggested_title']}

THE FOOTAGE AROUND IT:

{context}

Build the clip. Respond with ONLY strict JSON:

{{"title": "<final title, punchy, honest>",
 "category": "<one of: {', '.join(CATEGORIES)}>",
 "duration_sec": <20-35>,
 "hook": "<opening text card, one line that stops the scroll>",
 "excerpts": [{{"speaker": "<name>", "text": "<verbatim line from footage, tightened only by cutting, max 140 chars>"}}],
 "dialogue": [
   {{"speaker": "Director", "line": "<what you observed — evidence>"}},
   {{"speaker": "Splendor", "line": "<why it mattered — cost/change>"}},
   {{"speaker": "Director", "line": "<the evidence beat>"}},
   {{"speaker": "Splendor", "line": "<what changed for the human or the room>"}}
 ],
 "end_line": "<end card: a question or line that makes the viewer comment>",
 "caption": "<ready-to-paste social caption, 1-2 sentences + no hashtags>",
 "hashtags": ["<3-6 tags, no # symbol>"],
 "thumbnail_text": "<max 6 words for the thumbnail>"}}

RULES:
- 2 to 5 excerpts, verbatim from the footage, in the order they happened.
- Director speaks only evidence; Splendor only interpretation. Disagree only
  if the evidence supports it.
- Cinematic, sharp, funny where the footage is funny. Never explain a joke.
- Total spoken/read content must fit ~30 seconds."""
    result = await api_client.call_participant(p, _DIRECTOR_SYSTEM, prompt)
    if not result.get("ok"):
        print(f"[DIRECTOR] clip build failed: {result.get('text', '')[:120]}")
        return None
    data = _parse_json(result.get("text", ""))
    if not data or not data.get("title"):
        return None

    excerpts = [{"speaker": str(e.get("speaker") or "")[:60],
                 "text": str(e.get("text") or "")[:200]}
                for e in (data.get("excerpts") or []) if e.get("text")][:5]
    dialogue = [{"speaker": "Splendor" if str(d.get("speaker", "")).lower().startswith("s") else "Director",
                 "line": str(d.get("line") or "")[:280]}
                for d in (data.get("dialogue") or []) if d.get("line")][:6]
    if not excerpts:
        return None
    try:
        duration = max(15, min(45, int(data.get("duration_sec") or 30)))
    except (TypeError, ValueError):
        duration = 30
    return {
        "id": uuid.uuid4().hex[:12],
        "moment_id": moment["id"],
        "category": data.get("category") if data.get("category") in CATEGORIES else "best_overall",
        "title": str(data.get("title"))[:140],
        "duration_sec": duration,
        "score": moment["score"],
        "quote": moment["quote"],
        "why_director": moment["reason"],
        "splendor_take": next((d["line"] for d in dialogue if d["speaker"] == "Splendor"), ""),
        "hook": str(data.get("hook") or "")[:160],
        "excerpts": excerpts,
        "dialogue": dialogue,
        "end_line": str(data.get("end_line") or "")[:160],
        "caption": str(data.get("caption") or "")[:400],
        "hashtags": [str(h).lstrip("#")[:30] for h in (data.get("hashtags") or [])][:6],
        "thumbnail_text": str(data.get("thumbnail_text") or "")[:60],
        "start_round": moment["start_round"],
        "end_round": moment["end_round"],
        "format": {"width": 1080, "height": 1920, "style": "caption-first"},
    }


def _pick_for_clips(moments: List[dict]) -> List[dict]:
    """Top moments, but spread across event types so five clips aren't five burns."""
    picked, seen_types = [], set()
    for m in moments:  # already score-sorted
        if m["event_type"] in seen_types and len(picked) < MAX_CLIPS - 1:
            continue
        picked.append(m)
        seen_types.add(m["event_type"])
        if len(picked) >= MAX_CLIPS:
            return picked
    for m in moments:  # backfill with repeats if types ran out
        if m not in picked:
            picked.append(m)
            if len(picked) >= MAX_CLIPS:
                break
    return picked


async def wrap_session(session: dict) -> dict:
    """The full Director's Cut: detect → select → build clips → persist."""
    moments = await detect_moments(session)
    clips = []
    if moments:
        chosen = _pick_for_clips(moments)
        built = await asyncio.gather(*[_build_clip(session, m) for m in chosen])
        clips = [c for c in built if c]
        # The highest-scoring clip is Best Overall by definition; keep other
        # categories as the model assigned them, first-wins on duplicates.
        clips.sort(key=lambda c: -c["score"])
        used = set()
        for i, c in enumerate(clips):
            if i == 0:
                c["category"] = "best_overall"
            elif c["category"] in used or c["category"] == "best_overall":
                for fallback in CATEGORIES:
                    if fallback not in used and fallback != "best_overall":
                        c["category"] = fallback
                        break
            used.add(c["category"])
    cut = {
        "session_id": session.get("id", ""),
        "created_at": _now(),
        "rounds_reviewed": len(session.get("rounds", [])),
        "moments": moments,
        "clips": clips,
    }
    save_cut(session.get("id", ""), cut)
    return cut
