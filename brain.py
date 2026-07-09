"""The room brain — Splendor's cognitive architecture, ported to Team Talk.

One shared pass per round (not per AI): embed Chris's message, rank every
stored memory and episode by what actually matters right now, measure how
novel the topic is vs the recent conversation, and run a cheap background
"what is everyone missing?" reflection (Splendor's DMN). Every AI in the
room gets the same brain output in its context.

Everything persists to the server's disk (memory/embeddings.json cache) —
no external databases. The only network calls are to the OpenAI API Chris
already uses (embeddings + one tiny gpt-4o-mini call). Missing key or any
API failure degrades gracefully to Team Talk's current behavior.
"""

import asyncio
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import List, Optional

import api_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "memory")
CACHE_PATH = os.path.join(CACHE_DIR, "embeddings.json")

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
FAST_MODEL = os.getenv("BRAIN_FAST_MODEL", "gpt-4o-mini")
CACHE_MAX = 2000          # embedding vectors kept on disk
MEMORY_TOP_K = 25          # relevant memories shown per round
EPISODE_TOP_K = 3          # relevant past-session episodes shown per round
NOVELTY_WINDOW = 8         # recent rounds compared for novelty
DMN_TIMEOUT = 8.0

_cache = None              # {sha1: [floats]} lazy-loaded
_cache_dirty = False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, json.JSONDecodeError):
            _cache = {}
        if not isinstance(_cache, dict):
            _cache = {}
    return _cache


def _save_cache() -> None:
    global _cache_dirty
    if not _cache_dirty or _cache is None:
        return
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    trimmed = _cache
    if len(trimmed) > CACHE_MAX:
        # Drop oldest-inserted entries (dict preserves insertion order)
        keys = list(trimmed.keys())[-CACHE_MAX:]
        trimmed = {k: trimmed[k] for k in keys}
    tmp = f"{CACHE_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(trimmed, f)
    os.replace(tmp, CACHE_PATH)
    _cache_dirty = False


def _openai_client():
    key = api_client.openai_key()
    if not key:
        return None
    return api_client._get_client({"provider": "openai"}, key)


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def embed_many(texts: List[str]) -> List[Optional[List[float]]]:
    """Embed a batch of texts, hitting the disk cache first.

    Returns one vector (or None) per input text; a single API failure
    degrades the whole batch to cache-only rather than raising.
    """
    global _cache_dirty
    cache = _load_cache()
    out: List[Optional[List[float]]] = []
    missing, missing_idx = [], []
    for i, t in enumerate(texts):
        t = (t or "").strip()
        if not t:
            out.append(None)
            continue
        vec = cache.get(_key(t))
        out.append(vec)
        if vec is None:
            missing.append(t[:8000])
            missing_idx.append(i)

    if missing:
        client = _openai_client()
        if client is not None:
            try:
                r = await client.embeddings.create(model=EMBED_MODEL, input=missing)
                for slot, item in zip(missing_idx, r.data):
                    vec = list(item.embedding)
                    out[slot] = vec
                    cache[_key(texts[slot].strip())] = vec
                    _cache_dirty = True
                _save_cache()
            except Exception as e:
                print(f"[BRAIN] embedding batch failed (degrading): {e}")
    return out


async def embed(text: str) -> Optional[List[float]]:
    return (await embed_many([text]))[0]


# --- Novelty (Splendor's RAS) ------------------------------------------------

async def novelty(query_vec: Optional[List[float]], rounds: List[dict]) -> Optional[float]:
    """0..1 — how new this message is vs the recent conversation."""
    if not query_vec or not rounds:
        return None
    recent = [r.get("chris_message", "") for r in rounds[-NOVELTY_WINDOW:]]
    recent = [t for t in recent if t]
    if not recent:
        return None
    vecs = await embed_many(recent)
    sims = [cosine(query_vec, v) for v in vecs if v]
    if not sims:
        return None
    return round(min(1.0, max(0.0, 1 - max(sims))), 3)


# --- DMN (Splendor's background reflection) ----------------------------------

async def dmn_whisper(message: str, history_tail: str = "") -> Optional[str]:
    """One sharp background sentence: what might the room be missing?"""
    client = _openai_client()
    if client is None or not message.strip():
        return None
    prompt = (
        "You are a quiet background process for a group conversation between "
        "one human and several AIs. In ONE sharp sentence, name what might be "
        "missing, assumed, or worth questioning in responding to the human's "
        f"message: \"{message[:600]}\""
    )
    if history_tail:
        prompt += f"\nRecent context: {history_tail[:400]}"
    try:
        r = await asyncio.wait_for(
            client.chat.completions.create(
                model=FAST_MODEL, temperature=0.9, max_tokens=70,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=DMN_TIMEOUT,
        )
        text = (r.choices[0].message.content or "").strip()
        return text or None
    except Exception as e:
        print(f"[BRAIN] DMN pass skipped: {e}")
        return None


def room_sense_block(novelty_score: Optional[float], whisper: Optional[str]) -> str:
    """The shared brain output every AI sees this round."""
    lines = []
    if novelty_score is not None:
        if novelty_score >= 0.6:
            lines.append(f"- This message opens new ground (novelty {novelty_score}).")
        elif novelty_score <= 0.25:
            lines.append(f"- This continues the current thread closely (novelty {novelty_score}) — build, don't restart.")
    if whisper:
        lines.append(f"- Quiet thought from the room's background process (consider it; don't quote it): {whisper}")
    if not lines:
        return ""
    return "=== ROOM SENSE (one shared read — all of you see this) ===\n" + "\n".join(lines)


# --- Semantic memory recall (Splendor's Hippocampus) -------------------------

def _age_days(created_at: str) -> float:
    try:
        dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    except (ValueError, TypeError):
        return 999.0


def _recency(created_at: str) -> float:
    return 1.0 / (1.0 + _age_days(created_at) / 30.0)  # ~0.5 at one month


def score_memory(sim: float, entry: dict) -> float:
    """Splendor's ranking formula, adapted: similarity dominates, Chris-stated
    facts get a confidence edge over AI-observed ones, recency breaks ties."""
    confidence = 1.0 if entry.get("kind") == "chris_stated" else 0.7
    return 0.55 * sim + 0.25 * confidence + 0.20 * _recency(entry.get("created_at", ""))


async def ranked_memory_block(query_vec: Optional[List[float]], memories: List[dict]) -> str:
    """Relevance-ranked LONG-TERM MEMORY section with provenance labels.

    Falls back to the most recent MEMORY_TOP_K when embeddings are
    unavailable — exactly the app's pre-brain behavior.
    """
    if not memories:
        return ""
    pool = memories[-200:]  # rank at most the latest 200
    if query_vec:
        vecs = await embed_many([m.get("text", "") for m in pool])
        scored = sorted(
            (( score_memory(cosine(query_vec, v) if v else 0.0, m), m) for v, m in zip(vecs, pool)),
            key=lambda t: -t[0],
        )
        chosen = [m for _, m in scored[:MEMORY_TOP_K]]
        header = "=== LONG-TERM MEMORY (ranked by relevance to right now) ==="
    else:
        chosen = pool[-MEMORY_TOP_K * 2:][-40:]
        header = "=== LONG-TERM MEMORY (saved in past conversations) ==="

    lines = [header,
             "(Provenance: [stated] = Chris said it directly — treat as fact. "
             "[observed] = an AI saved its own interpretation — could be wrong; "
             "hold it with appropriate doubt.)"]
    for e in chosen:
        date = e.get("created_at", "")[:10]
        tag = "stated" if e.get("kind") == "chris_stated" else "observed"
        stale = ", old" if _age_days(e.get("created_at", "")) > 90 else ""
        lines.append(f"- [{e.get('by', '?')}, {date}, {tag}{stale}] {e.get('text', '')}")
    return "\n".join(lines)


async def ranked_episodes(query_vec: Optional[List[float]], episodes: List[dict],
                          exclude_session: str = "") -> List[dict]:
    """Most relevant compressed episodes from OTHER sessions."""
    pool = [e for e in episodes if e.get("session_id") != exclude_session][-100:]
    if not pool:
        return []
    if not query_vec:
        return pool[-EPISODE_TOP_K:]
    vecs = await embed_many([e.get("summary", "") for e in pool])
    scored = sorted(
        ((cosine(query_vec, v) if v else 0.0, e) for v, e in zip(vecs, pool)),
        key=lambda t: -t[0],
    )
    return [e for s, e in scored[:EPISODE_TOP_K] if s > 0.2]


# --- Episodic compression (Splendor's Layer 2/4) ------------------------------

async def summarize_rounds(rounds: List[dict]) -> Optional[str]:
    """Compress a block of aged-out rounds into one episode summary."""
    client = _openai_client()
    if client is None or not rounds:
        return None
    lines = []
    for r in rounds:
        lines.append(f"Chris: {r.get('chris_message', '')[:300]}")
        for resp in r.get("responses", []):
            text = (resp.get("text") or "")[:300]
            if text and not text.startswith("Error:"):
                lines.append(f"{resp.get('label') or resp.get('name', 'AI')}: {text}")
    transcript = "\n".join(lines)[:12000]
    try:
        r = await asyncio.wait_for(
            client.chat.completions.create(
                model=FAST_MODEL, temperature=0.2, max_tokens=220,
                messages=[{"role": "user", "content":
                    "Compress this group-chat excerpt into 3-5 sentences a "
                    "participant would need to remember it accurately later: "
                    "decisions made, positions taken (with names), running jokes, "
                    "and anything unresolved. No preamble.\n\n" + transcript}],
            ),
            timeout=20.0,
        )
        text = (r.choices[0].message.content or "").strip()
        return text or None
    except Exception as e:
        print(f"[BRAIN] episode summarization skipped: {e}")
        return None
