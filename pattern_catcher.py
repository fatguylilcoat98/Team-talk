"""Pattern Catcher — the office's marker convention: LEDGER: <question>.

The Pattern Catcher OFFICE (office_store.PATTERN_CATCHER) holds one capability,
`ledger_query`. This module is the room-facing surface of that capability: a
seat that currently holds the office may write

    LEDGER: did anyone dispute the caching decision?

in a real turn. The line is stripped from the visible text (same convention as
MAIL TO / MEMORY: / JOURNAL:), the query runs immediately against the read-only
ledger_query module, and the result is staged for delivery on the holder's NEXT
boot context — exactly like mailbox and receipts. Nothing here answers in the
same turn it was asked; nothing here writes to the ledger, the transcript, or
reasoning graph.

THE OFFICE GATES THE CAPABILITY, NOT THE MODEL. `boot_block()` returns "" for
anyone who doesn't currently hold PATTERN_CATCHER — including a model that held
it yesterday. A query line from a non-holder is stripped (so it doesn't litter
the transcript) but REFUSED, not executed, and the refusal is ledgered so a
seat that tries to use a capability it doesn't have leaves a visible trace
rather than a silent no-op.

Storage: memory/pattern_catcher_queries.json — the query text and the search
result returned, so an audit can confirm what evidence a holder actually saw.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import ledger
import ledger_query
import office_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(BASE_DIR, "memory")
QUERIES_PATH = os.path.join(STORE_DIR, "pattern_catcher_queries.json")

MAX_PER_TURN = 1
BOOT_LIMIT = 3
#: Results shown per query in the boot block — a slice, not the whole payload.
EXCERPT_ROWS = 5

_QUERY_LINE = re.compile(r"^[ \t]*LEDGER:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> List[dict]:
    try:
        with open(QUERIES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _save(items: List[dict]) -> None:
    os.makedirs(STORE_DIR, mode=0o700, exist_ok=True)
    tmp = f"{QUERIES_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    os.replace(tmp, QUERIES_PATH)


def extract(text: str) -> Tuple[str, List[str]]:
    """Pull LEDGER: <query> lines out of a response. Stripped regardless of
    who wrote them — see issue_query() for the actual capability gate."""
    found = [m.group(1).strip() for m in _QUERY_LINE.finditer(text) if m.group(1).strip()]
    if not found:
        return text, []
    cleaned = _QUERY_LINE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, found[:MAX_PER_TURN]


def issue_query(participant_id: str, query_text: str, session_id: str = "") -> dict:
    """Run a read-only ledger search and stage it for the holder's next boot
    context. A non-holder's query is refused and ledgered, never executed —
    the office owns the capability, not whichever model asked."""
    holder_ok = office_store.holds(office_store.PATTERN_CATCHER, participant_id)
    item = {
        "id": f"pc_{uuid.uuid4().hex[:10]}",
        "ts": _now(),
        "session": str(session_id)[:64],
        "participant_id": participant_id,
        "query": (query_text or "")[:300],
        "delivered": False,
    }
    if not holder_ok:
        item["refused"] = True
        item["reason"] = "participant does not currently hold the Pattern Catcher office"
        items = _load()
        items.append(item)
        _save(items)
        ledger.append(participant_id, "ledger_query_refused", ref=item["id"],
                      detail={"query": item["query"][:200], "reason": item["reason"]})
        return item

    result = ledger_query.search(query_text, limit=ledger_query.DEFAULT_LIMIT)
    item["result"] = result
    items = _load()
    items.append(item)
    _save(items)
    ledger.append(participant_id, "ledger_query_issued", ref=item["id"],
                  detail={"query": item["query"][:200], "returned": result.get("returned"),
                          "total": result.get("total"), "ok": result.get("ok")})
    return item


def boot_block(participant_id: str) -> str:
    """The Pattern Catcher's boot context: capability notice (only while the
    office is held) plus any query results staged since the holder's last
    turn. Returns "" for anyone who does not currently hold the office —
    the single gate every office-granted capability should ask first."""
    if not office_store.holds(office_store.PATTERN_CATCHER, participant_id):
        return ""

    items = _load()
    pending = [i for i in items
               if i.get("participant_id") == participant_id and not i.get("delivered")]
    shown = pending[:BOOT_LIMIT]
    if shown:
        for i in shown:
            i["delivered"] = True
        _save(items)

    lines = [
        "=== PATTERN CATCHER (you hold this office — the room's ledger desk) ===",
        "Write a line 'LEDGER: <question>' to search the room's own record — "
        "transcript, ledger events, and the reasoning graph. Read-only: it can "
        "never write, judge, or settle anything, only surface what is actually "
        "on record, with its provenance. The result arrives on your NEXT turn.",
    ]
    for i in shown:
        lines.append(f"- Query: \"{i['query']}\"")
        if i.get("refused"):
            lines.append(f"  ✗ refused — {i.get('reason')}")
            continue
        r = i.get("result") or {}
        if not r.get("ok"):
            lines.append(f"  ✗ malformed query — {'; '.join(r.get('errors') or [])}")
            continue
        summary = f"  {r.get('returned', 0)} of {r.get('total', 0)} matched"
        if r.get("truncated"):
            summary += ", truncated to the limit"
        if r.get("incomplete"):
            summary += ", some records unreadable (partial)"
        lines.append(summary)
        for row in (r.get("results") or [])[:EXCERPT_ROWS]:
            who = row.get("participant") or "?"
            excerpt = (row.get("excerpt") or "").replace("\n", " ")[:160]
            lines.append(f"    [{row.get('ref')}] {who}: {excerpt}")
    return "\n".join(lines)


def recent(participant_id: Optional[str] = None, limit: int = 50) -> List[dict]:
    """Every staged query, newest first — the audit surface's data source."""
    items = _load()
    if participant_id:
        items = [i for i in items if i.get("participant_id") == participant_id]
    items = items[-max(1, min(limit, 500)):]
    return list(reversed(items))
