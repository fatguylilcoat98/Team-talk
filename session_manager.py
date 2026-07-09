"""Session persistence — each conversation is one JSON file under sessions/.

Every round is written to disk immediately after both AI responses arrive,
so nothing is lost if the process dies.
"""

import html as html_lib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import aiofiles

SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def valid_id(session_id: str) -> bool:
    # Strict allowlist — session ids become filenames, so no path characters
    return bool(_ID_RE.match(session_id))


def _path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def new_session(session_id: Optional[str] = None) -> dict:
    if not session_id:
        session_id = f"team-talk-{uuid.uuid4().hex[:8]}"
    return {"id": session_id, "created_at": _now(), "rounds": []}


def normalize_round(r: dict) -> dict:
    """Upgrade old two-AI rounds (claude_response/chatgpt_response fields)
    to the roster shape: {"responses": [{id, name, text, tokens}, ...]}."""
    if "responses" in r:
        return r
    upgraded = {
        "round": r.get("round"),
        "timestamp": r.get("timestamp", ""),
        "chris_message": r.get("chris_message", ""),
        "responses": [
            {
                "id": "claude",
                "name": "Claude",
                "text": r.get("claude_response", ""),
                "tokens": r.get("claude_tokens", 0),
                "color": "#d97757",
            },
            {
                "id": "chatgpt",
                "name": "ChatGPT",
                "text": r.get("chatgpt_response", ""),
                "tokens": r.get("chatgpt_tokens", 0),
                "color": "#4bb388",
            },
        ],
    }
    return upgraded


async def load_session(session_id: str) -> Optional[dict]:
    if not valid_id(session_id):
        return None
    path = _path(session_id)
    if not os.path.exists(path):
        return None
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        session = json.loads(await f.read())
    session["rounds"] = [normalize_round(r) for r in session.get("rounds", [])]
    return session


async def save_session(session: dict) -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    path = _path(session["id"])
    tmp = f"{path}.tmp"
    async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
        await f.write(json.dumps(session, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


async def list_sessions() -> List[dict]:
    if not os.path.isdir(SESSIONS_DIR):
        return []
    summaries = []
    for name in sorted(os.listdir(SESSIONS_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            async with aiofiles.open(os.path.join(SESSIONS_DIR, name), "r", encoding="utf-8") as f:
                session = json.loads(await f.read())
        except (json.JSONDecodeError, OSError):
            continue
        rounds = session.get("rounds", [])
        summaries.append({
            "id": session.get("id", name[:-5]),
            "created_at": session.get("created_at", ""),
            "rounds": len(rounds),
            "last_message": rounds[-1]["chris_message"] if rounds else "",
        })
    summaries.sort(key=lambda s: s["created_at"], reverse=True)
    return summaries


def delete_session(session_id: str) -> bool:
    if not valid_id(session_id):
        return False
    path = _path(session_id)
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True


_MODE_TITLES = {
    "collab": "🤝 Collaborate", "debate": "⚔️ Debate", "ai_only": "🤖 AIs only",
    "devils_advocate": "😈 Devil's Advocate", "steelman": "🛡️ Steelman",
    "questions": "❓ Questions", "proof": "📋 Proof", "brainstorm": "💡 Brainstorm",
    "consensus": "🤝 Consensus", "shoot_the_shit": "🍺 Shooting the shit",
    "roast": "😂 Roast", "after_hours": "🍻 After Hours",
    "battle_royale": "🥊 Battle Royale", "method_acting": "🎭 Method Acting",
    "movie_cast": "🎬 Movie Cast", "mystery": "🕵️ Mystery",
    "courtroom": "⚖️ Courtroom", "late_night": "🎙️ Late Night Panel",
}


def export_html(session: dict) -> str:
    """A self-contained, share-anywhere HTML page of the conversation."""
    esc = html_lib.escape
    parts = []
    for raw in session.get("rounds", []):
        r = normalize_round(raw)
        marker = f"Round {r['round']}"
        mode_title = _MODE_TITLES.get(r.get("mode"))
        if mode_title:
            marker += f" · {mode_title}"
        parts.append(f'<div class="marker"><span>{esc(marker)}</span></div>')

        chris = esc(r.get("chris_message", ""))
        att_names = ", ".join(a.get("name", "") for a in r.get("attachments", []))
        att_html = f'<div class="att">📎 {esc(att_names)}</div>' if att_names else ""
        parts.append(
            f'<div class="block chris"><div class="name" style="color:#b8860b">'
            f'<span class="dot" style="background:#e8b04b"></span>CHRIS</div>'
            f'<div class="text">{chris}</div>{att_html}</div>'
        )

        for resp in r.get("responses", []):
            color = esc(resp.get("color", "#888888"))
            persona = resp.get("persona")
            persona_html = f'<span class="persona">🎭 {esc(persona)}</span>' if persona else ""
            err = ' style="color:#b03030"' if str(resp.get("text", "")).startswith("Error:") else ""
            parts.append(
                f'<div class="block" style="border-color:{color}">'
                f'<div class="name" style="color:{color}">'
                f'<span class="dot" style="background:{color}"></span>'
                f'{esc(resp.get("name", "AI"))}{persona_html}</div>'
                f'<div class="text"{err}>{esc(resp.get("text", ""))}</div></div>'
            )

    body = "\n".join(parts)
    title = esc(session.get("id", "Team Talk"))
    created = esc(session.get("created_at", ""))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Team Talk — {title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #f5f3ee; color: #26221c; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; line-height: 1.55; }}
  .wrap {{ max-width: 760px; margin: 0 auto; padding: 32px 16px 48px; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 2px; }}
  .sub {{ color: #8a8272; font-size: 0.85rem; margin-bottom: 24px; }}
  .marker {{ display: flex; align-items: center; gap: 12px; color: #8a8272; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.12em; margin: 26px 0 12px; }}
  .marker::before, .marker::after {{ content: ""; flex: 1; height: 1px; background: #ddd6c8; }}
  .block {{ background: #fffdf8; border: 1px solid #ddd6c8; border-left-width: 4px; border-radius: 10px; margin-bottom: 10px; overflow: hidden; }}
  .block.chris {{ border-left-color: #e8b04b; background: #fdf6e6; }}
  .name {{ display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 0.8rem; letter-spacing: 0.07em; padding: 8px 14px 0; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .persona {{ font-weight: 400; letter-spacing: normal; color: #8a8272; font-size: 0.78rem; }}
  .text {{ padding: 6px 14px 12px; white-space: pre-wrap; word-wrap: break-word; }}
  .att {{ padding: 0 14px 12px; color: #8a8272; font-size: 0.8rem; }}
  .footer {{ text-align: center; color: #8a8272; font-size: 0.8rem; margin-top: 36px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Team Talk</h1>
  <div class="sub">{title} · {created}</div>
  {body}
  <div class="footer">One human, several AIs, one conversation — made with <strong>Team Talk</strong> 🍻</div>
</div>
</body>
</html>
"""


def export_markdown(session: dict) -> str:
    lines = [
        f"# Team Talk — {session['id']}",
        "",
        f"Created: {session.get('created_at', 'unknown')}",
        "",
    ]
    for raw in session.get("rounds", []):
        r = normalize_round(raw)
        lines += [
            "---",
            "",
            f"## Round {r['round']}  ({r.get('timestamp', '')})",
            "",
            f"**Chris:** {r['chris_message']}",
            "",
        ]
        for resp in r["responses"]:
            lines += [
                f"### {resp['name']}  (tokens: {resp.get('tokens', '--')})",
                "",
                resp.get("text", ""),
                "",
            ]
    return "\n".join(lines)
