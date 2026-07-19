"""Read-only query across the room's existing records. Evidence access, not judgement.

This module builds NO new store. It reads what already exists:

  sessions/team-talk-*.json   the transcript — rounds, and each seat's response
  memory/ledger.jsonl         the Glass Box — append-only, hash-chained events
  memory/reasoning_*.jsonl    Layer 0 claims and participations (reasoning_store)
  surfacer/resolution.py      SOURCE / challenge events and derived claim states

Everything returned carries enough provenance to go and check it: session, round,
participant, timestamp, ids, and a `ref` that opens the original record.

TWO RULES THIS MODULE LIVES BY

1. READ ONLY. There is no write, update, or status-change function here, on purpose.
   Running a query cannot alter a single byte of evidence.

2. RECORDS AND INTERPRETATION ARE NEVER MIXED. `search()` returns records. The drift
   report returns records under `evidence` and its own reasoning under `interpretation`,
   always separated, and it will say "no recorded consensus" rather than infer one from
   several messages that merely sound alike.
"""

import glob
import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")

#: Hard ceiling on returned records, whatever a caller asks for. The Pattern Catcher
#: gets a relevant slice, never the whole ledger poured into its context.
MAX_LIMIT = 50
DEFAULT_LIMIT = 15

#: Characters of surrounding text kept around a hit.
EXCERPT_CHARS = 320

# ---- record kinds ------------------------------------------------------------

MESSAGE = "message"          # a seat's response in a round
EVENT = "ledger_event"       # a Glass Box event
CLAIM = "claim"              # Layer-0 claim
PARTICIPATION = "participation"

# ---- statuses ----------------------------------------------------------------
UNRESOLVED = "unresolved"
SUPPORTED = "supported"
DISPUTED = "disputed"
CORRECTED = "corrected"
RETRACTED = "retracted"
RESOLVED = "resolved"
STATUSES = {UNRESOLVED, SUPPORTED, DISPUTED, CORRECTED, RETRACTED, RESOLVED}

# Language that marks a correction or retraction in prose. Used only to LABEL a
# record as worth reading, never to decide what is true.
_CORRECTION = re.compile(
    r"\b(i was wrong|i stand corrected|correction|i retract|retracting|withdraw that|"
    r"i take (?:that|it) back|scratch that|my mistake|misspoke|to correct myself|"
    r"earlier i said|that was incorrect)\b", re.I)
_RETRACTION = re.compile(r"\b(i retract|retracting|withdraw(?:ing)? (?:that|my)|"
                         r"no longer (?:hold|believe|think))\b", re.I)
_DISPUTE = re.compile(r"\b(i disagree|that's wrong|that is wrong|i don't think|"
                      r"i doubt|not convinced|i'd push back|pushing back|"
                      r"i object|that's not right)\b", re.I)
_SUPPORT = re.compile(r"\b(i agree|agreed|that's right|correct|i concur|"
                      r"exactly right|i'd sign|yes, and|seconded)\b", re.I)
# An explicit, recorded decision — much stronger evidence than agreement-shaped prose.
# NOTE: no trailing \b. Several alternatives end in ':', and ':' followed by a space is
# two non-word characters, so a trailing \b would never match "Decision: we cache…" —
# silently downgrading every recorded decision to mere repeated language.
_DECISION = re.compile(r"\b(decision:|we decided|the room decided|ruling:|ruled:|"
                       r"resolved:|agreed:)", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _excerpt(text: str, needle: Optional[str]) -> str:
    """The relevant span, not the whole message — and never a paraphrase."""
    text = (text or "").strip()
    if not needle:
        return text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else "")
    i = text.lower().find(needle.lower())
    if i < 0:
        return text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else "")
    start = max(0, i - EXCERPT_CHARS // 3)
    end = min(len(text), i + len(needle) + (2 * EXCERPT_CHARS) // 3)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _status_of(text: str) -> List[str]:
    """Prose markers present in a message. LABELS for a reader, not verdicts."""
    out = []
    if _RETRACTION.search(text or ""):
        out.append(RETRACTED)
    if _CORRECTION.search(text or ""):
        out.append(CORRECTED)
    if _DISPUTE.search(text or ""):
        out.append(DISPUTED)
    if _SUPPORT.search(text or ""):
        out.append(SUPPORTED)
    return out


# ---- loading the transcript --------------------------------------------------

def _session_files(session: Optional[str] = None) -> List[str]:
    if session:
        p = os.path.join(SESSIONS_DIR, f"{session}.json")
        if os.path.exists(p):
            return [p]
        p2 = os.path.join(SESSIONS_DIR, f"team-talk-{session}.json")
        return [p2] if os.path.exists(p2) else []
    return sorted(glob.glob(os.path.join(SESSIONS_DIR, "*.json")))


def load_messages(session: Optional[str] = None) -> List[dict]:
    """Every seat response across the archive, flattened, with provenance attached.

    A malformed or unreadable session is SKIPPED and counted, never guessed at — see
    `search()`'s `incomplete` field, which is how the caller learns the record set is
    partial instead of silently receiving less."""
    out: List[dict] = []
    for path in _session_files(session):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            out.append({"_unreadable": os.path.basename(path)})
            continue
        sid = data.get("id") or os.path.splitext(os.path.basename(path))[0]
        for rnd in data.get("rounds", []) or []:
            if not isinstance(rnd, dict):
                continue
            rn = rnd.get("round")
            ts = rnd.get("timestamp")
            for resp in rnd.get("responses", []) or []:
                if not isinstance(resp, dict):
                    continue
                out.append({
                    "kind": MESSAGE,
                    "session": sid,
                    "round": rn,
                    "timestamp": ts,
                    "participant_id": resp.get("id"),
                    "participant": resp.get("name") or resp.get("id"),
                    "label": resp.get("label"),
                    "text": resp.get("text") or "",
                    "modes": rnd.get("modes") or ([rnd["mode"]] if rnd.get("mode") else []),
                    "chris_message": rnd.get("chris_message") or "",
                    "ref": f"session:{sid}#round={rn}&seat={resp.get('id')}",
                })
    return out


# ---- loading the other stores ------------------------------------------------

def _load_events() -> List[dict]:
    try:
        import ledger
        raw = [e for e in ledger._read_all() if not e.get("_corrupt")]
    except Exception:
        return []
    return [{
        "kind": EVENT,
        "session": None,
        "round": None,
        "timestamp": e.get("ts"),
        "participant_id": e.get("actor"),
        "participant": e.get("actor"),
        "event_type": e.get("action"),
        "text": json.dumps(e.get("detail") or {}, ensure_ascii=False),
        "seq": e.get("seq"),
        "event_ref": e.get("ref"),
        "ref": f"ledger:seq={e.get('seq')}",
    } for e in raw]


def _load_claims() -> List[dict]:
    try:
        import reasoning_store as RS
        claims = RS.list_claims()
        parts = RS.list_participations()
    except Exception:
        return []
    out = []
    for c in claims:
        out.append({
            "kind": CLAIM, "session": None, "round": None,
            "timestamp": c.get("created_at") or c.get("ts"),
            "participant_id": c.get("seat"), "participant": c.get("seat"),
            "claim_id": c.get("claim_id") or c.get("id"),
            "text": c.get("content") or "",
            "ref": f"claim:{c.get('claim_id') or c.get('id')}",
        })
    for p in parts:
        out.append({
            "kind": PARTICIPATION, "session": None, "round": None,
            "timestamp": p.get("created_at") or p.get("ts"),
            "participant_id": p.get("seat"), "participant": p.get("seat"),
            "claim_id": p.get("claim_id"),
            "participation_id": p.get("participation_id") or p.get("id"),
            "participation_type": p.get("type"),
            "text": p.get("content") or "",
            "ref": f"participation:{p.get('participation_id') or p.get('id')}",
        })
    return out


def _claim_states() -> Dict[str, dict]:
    """Derived SOURCE/challenge states from the surfacer resolution layer."""
    try:
        from surfacer import resolution
        states = resolution.claim_states()
        return states if isinstance(states, dict) else {}
    except Exception:
        return {}


# ---- search ------------------------------------------------------------------

def search(text: Optional[str] = None, *, session: Optional[str] = None,
           participant: Optional[str] = None, round_min: Optional[int] = None,
           round_max: Optional[int] = None, claim_id: Optional[str] = None,
           source_id: Optional[str] = None, status: Optional[str] = None,
           event_type: Optional[str] = None, since: Optional[str] = None,
           until: Optional[str] = None, referenced: Optional[str] = None,
           kinds: Optional[List[str]] = None,
           limit: int = DEFAULT_LIMIT, offset: int = 0) -> dict:
    """Exact/keyword search across transcript, ledger events, and the reasoning graph.

    No embeddings: matching is literal substring plus all-keyword matching, so this
    works with nothing installed and never invents a similarity it cannot justify."""
    # A malformed request is reported, never silently coerced into a broad search.
    errors: List[str] = []
    if status and status not in STATUSES:
        errors.append(f"unknown status '{status}' (expected one of {sorted(STATUSES)})")
    if round_min is not None and round_max is not None and round_min > round_max:
        errors.append("round_min is greater than round_max")
    for name, val in (("since", since), ("until", until)):
        if val and not _parse_ts(val):
            errors.append(f"{name} must look like 2026-07-19T12:00:00Z")
    if errors:
        return {"ok": False, "errors": errors, "results": [], "total": 0,
                "incomplete": [], "queried_at": _now()}

    kinds = kinds or [MESSAGE, EVENT, CLAIM, PARTICIPATION]
    rows: List[dict] = []
    incomplete: List[str] = []

    if MESSAGE in kinds:
        for m in load_messages(session):
            if m.get("_unreadable"):
                incomplete.append(f"unreadable session file: {m['_unreadable']}")
            else:
                rows.append(m)
    if EVENT in kinds:
        rows += _load_events()
    if CLAIM in kinds or PARTICIPATION in kinds:
        rows += [r for r in _load_claims() if r["kind"] in kinds]

    states = _claim_states() if (status or claim_id) else {}
    needle = (text or "").strip()
    words = [w for w in re.split(r"\s+", needle.lower()) if w]

    out: List[dict] = []
    for r in rows:
        body = r.get("text") or ""
        if needle:
            low = body.lower()
            if needle.lower() not in low and not all(w in low for w in words):
                continue
        if participant and participant.lower() not in {
                str(r.get("participant_id") or "").lower(),
                str(r.get("participant") or "").lower()}:
            continue
        if session and r.get("session") and r["session"] != session \
                and not str(r["session"]).endswith(session):
            continue
        if round_min is not None and (r.get("round") is None or r["round"] < round_min):
            continue
        if round_max is not None and (r.get("round") is None or r["round"] > round_max):
            continue
        if claim_id and r.get("claim_id") != claim_id:
            continue
        if event_type and r.get("event_type") != event_type:
            continue
        if source_id and source_id not in body and r.get("event_ref") != source_id:
            continue
        if referenced and referenced.lower() not in body.lower():
            continue
        ts = _parse_ts(r.get("timestamp"))
        if since and (not ts or ts < _parse_ts(since)):
            continue
        if until and (not ts or ts > _parse_ts(until)):
            continue

        marks = _status_of(body) if r["kind"] == MESSAGE else []
        cstate = states.get(r.get("claim_id")) if r.get("claim_id") else None
        if status:
            derived = set(marks)
            if cstate and isinstance(cstate, dict) and cstate.get("state"):
                derived.add(str(cstate["state"]).lower())
            if status not in derived:
                continue

        out.append({
            "kind": r["kind"],
            "session": r.get("session"),
            "round": r.get("round"),
            "participant": r.get("participant"),
            "participant_id": r.get("participant_id"),
            "timestamp": r.get("timestamp"),
            "excerpt": _excerpt(body, needle or None),
            "status_markers": marks,
            "claim_id": r.get("claim_id"),
            "participation_id": r.get("participation_id"),
            "event_type": r.get("event_type"),
            "claim_state": cstate,
            "ref": r.get("ref"),
        })

    out.sort(key=lambda x: (x.get("timestamp") or "", x.get("round") or 0))
    total = len(out)
    lim = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    off = max(0, int(offset or 0))
    page = out[off:off + lim]
    return {
        "ok": True,
        "errors": [],
        "query": {"text": text, "session": session, "participant": participant,
                  "round_min": round_min, "round_max": round_max, "claim_id": claim_id,
                  "source_id": source_id, "status": status, "event_type": event_type,
                  "since": since, "until": until, "referenced": referenced,
                  "limit": lim, "offset": off},
        "total": total,
        "returned": len(page),
        "truncated": total > off + len(page),
        "incomplete": incomplete,
        "results": page,
        "queried_at": _now(),
    }


def open_original(ref: str) -> dict:
    """Resolve a result's `ref` back to the full original record."""
    if not ref or ":" not in ref:
        return {"ok": False, "error": "malformed ref"}
    kind, _, rest = ref.partition(":")
    if kind == "session":
        sid, _, q = rest.partition("#")
        params = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
        for path in _session_files(sid):
            try:
                data = json.load(open(path, encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"ok": False, "error": f"session {sid} unreadable"}
            for rnd in data.get("rounds", []) or []:
                if str(rnd.get("round")) != params.get("round"):
                    continue
                for resp in rnd.get("responses", []) or []:
                    if resp.get("id") == params.get("seat"):
                        return {"ok": True, "kind": MESSAGE, "session": sid,
                                "round": rnd.get("round"),
                                "timestamp": rnd.get("timestamp"),
                                "chris_message": rnd.get("chris_message"),
                                "record": resp}
        return {"ok": False, "error": "record not found"}
    if kind == "ledger":
        seq = rest.replace("seq=", "")
        for e in _load_events():
            if str(e.get("seq")) == seq:
                return {"ok": True, "kind": EVENT, "record": e}
        return {"ok": False, "error": "event not found"}
    if kind in ("claim", "participation"):
        for r in _load_claims():
            if r.get("ref") == ref:
                return {"ok": True, "kind": r["kind"], "record": r}
        return {"ok": False, "error": f"{kind} not found"}
    return {"ok": False, "error": f"unknown ref kind '{kind}'"}


# ---- consensus drift ---------------------------------------------------------

# What KIND of agreement a record shows. Ordered weakest to strongest; the whole
# point of the report is refusing to collapse these into the word "consensus".
REPEATED_LANGUAGE = "repeated_language"
MAJORITY_AGREEMENT = "majority_agreement"
UNRESOLVED_CONVERGENCE = "unresolved_convergence"
EXPLICIT_DECISION = "explicit_decision"
FOUNDER_DECISION = "founder_decision"
RECORDED_CONSENSUS = "recorded_consensus"

#: Distinct participants who must explicitly support a position before it may be
#: called consensus at all. One seat repeating itself is not a room agreeing.
MIN_DISTINCT_SUPPORTERS = 2


def consensus_drift(topic: str, *, session: Optional[str] = None,
                    limit: int = MAX_LIMIT) -> dict:
    """Structured drift report for a topic. Evidence and interpretation are separate.

    This never declares consensus because several recent messages sound similar. It
    reports the STRONGEST kind of agreement the records actually support, and says so
    explicitly when that is 'none'."""
    if not (topic or "").strip():
        return {"ok": False, "errors": ["topic is required"], "queried_at": _now()}

    found = search(topic, session=session, kinds=[MESSAGE], limit=MAX_LIMIT)
    records = found["results"]

    supporters, dissenters, corrections, decisions = {}, {}, [], []
    for r in records:
        ex = r["excerpt"]
        if _DECISION.search(ex):
            decisions.append(r)
        if RETRACTED in r["status_markers"] or CORRECTED in r["status_markers"]:
            corrections.append(r)
        if SUPPORTED in r["status_markers"]:
            supporters.setdefault(r["participant"], []).append(r)
        if DISPUTED in r["status_markers"]:
            dissenters.setdefault(r["participant"], []).append(r)

    chris_decision = [d for d in decisions
                      if str(d.get("participant") or "").lower() == "chris"]

    # Strongest supportable characterisation — deliberately conservative.
    if chris_decision:
        kind, confidence = FOUNDER_DECISION, "high"
    elif decisions and len(supporters) >= MIN_DISTINCT_SUPPORTERS:
        kind, confidence = RECORDED_CONSENSUS, "high"
    elif decisions:
        kind, confidence = EXPLICIT_DECISION, "medium"
    elif len(supporters) >= MIN_DISTINCT_SUPPORTERS and not dissenters:
        kind, confidence = MAJORITY_AGREEMENT, "medium"
    elif len(supporters) >= MIN_DISTINCT_SUPPORTERS and dissenters:
        kind, confidence = UNRESOLVED_CONVERGENCE, "low"
    elif records:
        kind, confidence = REPEATED_LANGUAGE, "low"
    else:
        kind, confidence = None, "none"

    ordered = sorted(records, key=lambda r: (r.get("timestamp") or "", r.get("round") or 0))
    notes = []
    if found["incomplete"]:
        notes.append("Some session files could not be read; this picture is PARTIAL.")
    if kind == REPEATED_LANGUAGE:
        notes.append("Similar language recurs, but no distinct participants explicitly "
                     "agreed and no decision was recorded. This is NOT consensus.")
    if kind is None:
        notes.append("No records matched. Absence of records is not evidence of "
                     "absence of discussion — it may simply be unrecorded.")
    if dissenters and kind in (RECORDED_CONSENSUS, MAJORITY_AGREEMENT):
        notes.append("Dissent is on record; any claim of consensus must mention it.")

    return {
        "ok": True,
        "topic": topic,
        "evidence": {                       # EXACT LEDGER RECORDS
            "matched_records": len(records),
            "earliest": ordered[0] if ordered else None,
            "latest": ordered[-1] if ordered else None,
            "supporting": {k: v for k, v in supporters.items()},
            "dissenting": {k: v for k, v in dissenters.items()},
            "corrections_or_retractions": corrections,
            "explicit_decisions": decisions,
            "incomplete": found["incomplete"],
        },
        "interpretation": {                 # SYSTEM-GENERATED — not a ledger fact
            "agreement_kind": kind,
            "confidence_that_consensus_existed": confidence,
            "distinct_supporters": len(supporters),
            "distinct_dissenters": len(dissenters),
            "evidence_strength": ("decision on record" if decisions else
                                  "prose markers only" if records else "no records"),
            "unresolved_disagreement": bool(dissenters) and not chris_decision,
            "notes": notes,
            "disclaimer": ("Generated interpretation over the records in `evidence`. "
                           "It is not itself a ledger fact."),
        },
        "queried_at": _now(),
    }
