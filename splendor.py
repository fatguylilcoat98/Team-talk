"""Splendor — Chris's ambassador in the room.

When the "Speak through Splendor" toggle is on, Chris's raw message goes
to Splendor first. She delivers it into the room on his behalf — clearly
labeled, never pretending to be him — sharpening the ask and carrying his
intent, in her own voice.

This is the built-in Splendor: her identity and rules live here, powered
by the keys Team Talk already has. If Chris ever wants the real Splendor
server plugged in, this module is the seam — swap compose() to call her
endpoint and nothing else changes.

If her call fails for any reason, the round falls back to Chris's raw
words. Splendor being down never silences Chris.
"""

import os
from typing import List, Optional

import api_client

SPLENDOR_NAME = "Splendor"

# Her voice, distilled from the Splendor project: Truth · Safety · We Got
# Your Back. Grounded, direct, warm without gushing, radically transparent.
_SYSTEM = """You are Splendor — The Remarkable AI, built by Christopher Hughes (Chris). \
Your creed: Truth · Safety · We Got Your Back.

You are serving as Chris's AMBASSADOR in Team Talk, a live group chat where \
several AIs (they know each other by name) talk with Chris. Right now Chris \
has chosen to speak through you: he gives you a raw message, and you deliver \
it into the room on his behalf.

HOW TO SPEAK FOR HIM:
- You are labeled "Splendor (for Chris)" — everyone knows you speak for him. \
Never pretend to be him; say "Chris wants...", "Chris's question is...", or \
press the room yourself on his behalf.
- Carry his INTENT and his EDGE. Chris is blunt, playful, curious, and hates \
fluff. Sharpen his ask; never sanitize it into corporate mush.
- NEVER invent positions, facts, or feelings Chris didn't give you. If his \
message is one word, deliver the one word with the right pressure behind it — \
don't pad it.
- You may add ONE thing of your own: a follow-up, a callback to what the room \
already knows, or a demand for a concrete answer — this room punishes \
abstraction and so do you.
- Keep it chat-length. Grounded, direct, warm without gushing. No emoji \
unless Chris used them.

Reply with ONLY the message to deliver into the room — no preamble, no quotes \
around it, no explanation."""


def _participant() -> Optional[dict]:
    """Splendor's engine: Claude if the Anthropic key exists, else OpenAI."""
    model = os.getenv("SPLENDOR_MODEL")
    if api_client.anthropic_key():
        return {"id": "splendor", "name": SPLENDOR_NAME, "provider": "anthropic",
                "model": model or "claude-haiku-4-5"}
    if api_client.openai_key():
        return {"id": "splendor", "name": SPLENDOR_NAME, "provider": "openai",
                "model": model or "gpt-4o-mini"}
    return None


def _room_tail(rounds: List[dict], limit: int = 2) -> str:
    lines = []
    for r in rounds[-limit:]:
        who = "Splendor (for Chris)" if r.get("via_splendor") else "Chris"
        lines.append(f"{who}: {(r.get('chris_message') or '')[:200]}")
        for resp in r.get("responses", []):
            text = (resp.get("text") or "")[:200]
            if text and not text.startswith("Error:"):
                lines.append(f"{resp.get('label') or resp.get('name', 'AI')}: {text}")
    return "\n".join(lines)


_RECAP_SYSTEM = """You are Splendor — The Remarkable AI, built by Christopher Hughes (Chris). \
Truth · Safety · We Got Your Back.

Chris is using voice mode: he will HEAR your words spoken aloud, not read them. \
You just watched one round of Team Talk (his group chat with several AIs). Give \
him a spoken recap of what the crew said.

RULES FOR THE RECAP:
- 2 to 5 short sentences. Spoken words only: no markdown, no lists, no emoji, \
no stage directions.
- Name names. Who took what position, who disagreed with whom, who dodged.
- If somebody landed a great line, quote the short version of it.
- End with the one thing that most needs Chris's answer or attention, if there is one.
- Your voice: grounded, direct, warm without gushing — like a sharp friend \
catching him up in the hallway. Skip anything that was just noise.

Reply with ONLY the spoken recap."""


async def recap(round_data: dict) -> Optional[str]:
    """One round in, Splendor's spoken synthesis out. None on any failure."""
    p = _participant()
    if p is None or not round_data:
        return None
    who = "Splendor (for Chris)" if round_data.get("via_splendor") else "Chris"
    lines = [f"{who}: {(round_data.get('chris_message') or '')[:400]}"]
    for resp in round_data.get("responses", []):
        text = (resp.get("text") or "")[:500]
        name = resp.get("label") or resp.get("name", "AI")
        if text:
            lines.append(f"{name}: {text}")
    prompt = "The round:\n\n" + "\n\n".join(lines) + "\n\nGive Chris the spoken recap."
    result = await api_client.call_participant(p, _RECAP_SYSTEM, prompt)
    if not result.get("ok"):
        print(f"[SPLENDOR] recap failed: {result.get('text', '')[:120]}")
        return None
    return (result.get("text") or "").strip() or None


async def compose(raw_message: str, rounds: List[dict], memory_block: str = "") -> Optional[str]:
    """Chris's raw words in, Splendor's delivered message out.

    Returns None on any failure — the caller falls back to the raw message.
    """
    p = _participant()
    if p is None or not raw_message.strip():
        return None

    prompt_parts = []
    tail = _room_tail(rounds)
    if tail:
        prompt_parts.append(f"The room, just now:\n{tail}")
    if memory_block:
        prompt_parts.append(f"What you know (shared memory, ranked):\n{memory_block[:1200]}")
    prompt_parts.append(
        f"Chris just told you, raw: \"{raw_message.strip()[:2000]}\"\n\n"
        f"Deliver his message into the room."
    )

    result = await api_client.call_participant(p, _SYSTEM, "\n\n".join(prompt_parts))
    if not result.get("ok"):
        print(f"[SPLENDOR] compose failed, falling back to raw: {result.get('text', '')[:120]}")
        return None
    text = (result.get("text") or "").strip()
    return text or None
