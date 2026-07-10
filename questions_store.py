"""Questions for Chris — a persistent queue the AIs can put things in.

An AI asks with a QUESTION FOR CHRIS: line; the question sits in the
queue until Chris answers it in the Truth panel. Open questions never
expire. Answers flow back into every AI's context on the next round.

Storage: memory/questions.json on the server's disk.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_DIR = os.path.join(BASE_DIR, "memory")
QUESTIONS_PATH = os.path.join(QUESTIONS_DIR, "questions.json")

MAX_QUESTION_CHARS = 400
MAX_ANSWER_CHARS = 1000
MAX_PER_MESSAGE = 1
CONTEXT_OPEN = 10
CONTEXT_ANSWERED = 3

_QUESTION_LINE = re.compile(
    r"^[ \t]*QUESTION FOR CHRIS:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> List[dict]:
    try:
        with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(questions: List[dict]) -> None:
    os.makedirs(QUESTIONS_DIR, mode=0o700, exist_ok=True)
    tmp = f"{QUESTIONS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(questions, f, indent=2, ensure_ascii=False)
    os.replace(tmp, QUESTIONS_PATH)


def list_questions() -> List[dict]:
    return _load()


def ask(asker: str, question: str, session_id: str = "") -> dict:
    q = {
        "id": uuid.uuid4().hex[:12],
        "ts": _now(),
        "session": str(session_id)[:64],
        "asker": str(asker)[:60],
        "question": question.strip()[:MAX_QUESTION_CHARS],
        "status": "open",
        "answer": None,
        "answered_at": None,
    }
    questions = _load()
    questions.append(q)
    _save(questions)
    return q


def answer(question_id: str, answer_text: str) -> Optional[dict]:
    questions = _load()
    for q in questions:
        if q.get("id") == question_id and q.get("status") == "open":
            q["status"] = "answered"
            q["answer"] = answer_text.strip()[:MAX_ANSWER_CHARS]
            q["answered_at"] = _now()
            _save(questions)
            return q
    return None


def open_count() -> int:
    return sum(1 for q in _load() if q.get("status") == "open")


def extract(text: str) -> Tuple[str, List[str]]:
    """Pull QUESTION FOR CHRIS: lines out of a response."""
    found = [m.strip() for m in _QUESTION_LINE.findall(text) if m.strip()]
    if not found:
        return text, []
    cleaned = _QUESTION_LINE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, found[:MAX_PER_MESSAGE]


def context_block() -> str:
    """Open questions (and fresh answers) shown to every AI each round."""
    questions = _load()
    open_qs = [q for q in questions if q.get("status") == "open"][-CONTEXT_OPEN:]
    answered = [q for q in questions if q.get("status") == "answered"][-CONTEXT_ANSWERED:]
    if not open_qs and not answered:
        return ""
    lines = ["=== QUESTIONS FOR CHRIS (persistent queue — open until he answers) ==="]
    if open_qs:
        for q in open_qs:
            lines.append(f"- OPEN [{q['asker']}, {q['ts'][:10]}] {q['question']}")
    else:
        lines.append("- (no open questions)")
    if answered:
        lines.append("Recently answered by Chris:")
        for q in answered:
            lines.append(f"- [{q['asker']} asked] {q['question']}")
            lines.append(f"  Chris answered ({(q.get('answered_at') or '')[:10]}): {q['answer']}")
    return "\n".join(lines)
