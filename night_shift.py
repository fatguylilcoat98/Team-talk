"""🌙 Night Shift — the room works while Chris is away.

Chris's wish, verbatim: "I wish there was a way I could give you guys the
ability to talk even when im not around... I come back 4 hours later and
you have something to offer." The seats wrote the spec themselves the
same round; this module implements their stop conditions, not a vibe:

- A run gets MAX ROUNDS, hard-capped, and a TOKEN BUDGET, hard-capped.
  Whichever runs out first ends the shift.
- Every reply must end with a STANCE line: DISSENT (with the edge) or
  CONVERGED. When every seat converges, the run halts — "no new claim
  survives a round" made mechanical.
- Claude's rule: consensus does not register without a logged dissent.
  If the room converges with zero dissents on record, ONE mandatory
  dissent round runs — every seat ordered to attack the consensus —
  before convergence is allowed to count.
- Chris logs in to an artifact (the report, digest pinned to the Wall),
  not a transcript. The transcript is kept anyway; replay beats recall.

The nightmare this exists to prevent, in Claude's words: "forty rounds
of five models agreeing warmly with each other" burning the keys while
Chris sleeps. Bounded, adversarial, and it stops itself.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import api_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NIGHT_DIR = os.path.join(BASE_DIR, "memory", "night_shift")
STATE_PATH = os.path.join(NIGHT_DIR, "state.json")
RUNS_DIR = os.path.join(NIGHT_DIR, "runs")

DEFAULT_ROUNDS = 6
MIN_ROUNDS, MAX_ROUNDS = 2, 12
DEFAULT_BUDGET = 40_000            # output tokens across the whole run
MIN_BUDGET, MAX_BUDGET = 5_000, 200_000
REPLY_TOKENS = int(os.getenv("NIGHT_REPLY_TOKENS", "1200"))
REPORT_TOKENS = int(os.getenv("NIGHT_REPORT_TOKENS", "2500"))
# The night bench is MEMORYLESS: seats get the night system prompt plus
# this topic text — no boot packets, no chat history, no Workshop state.
# The topic must CARRY everything the run needs (paste the spec in), so
# it gets room to be a real briefing, not a headline.
MAX_TOPIC = 12000
TAIL_MESSAGES = 8                  # full text kept in context; older = stance log

STANCE_RE = re.compile(r"^\s*STANCE:\s*(DISSENT|CONVERGED)\b[\s—:–-]*(.*)$",
                       re.IGNORECASE | re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dirs() -> None:
    os.makedirs(RUNS_DIR, mode=0o700, exist_ok=True)


def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"run": None}


def save_state(state: dict) -> None:
    _ensure_dirs()
    tmp = f"{STATE_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def mark_stale() -> None:
    """Called once at app boot: a run that says 'running' after a restart
    has no task behind it — close it honestly instead of showing a ghost."""
    state = load_state()
    run = state.get("run")
    if run and run.get("status") == "running":
        run["status"] = "done"
        run["ended_at"] = _now()
        run["halt_reason"] = "server restarted mid-run — no report written"
        save_state(state)
        _archive(run)


def start_run(topic: str, max_rounds: int = DEFAULT_ROUNDS,
              budget_tokens: int = DEFAULT_BUDGET) -> Optional[dict]:
    """Create a run. Returns the run, or None if one is already running."""
    topic = str(topic or "").strip()[:MAX_TOPIC]
    if not topic:
        return None
    state = load_state()
    if (state.get("run") or {}).get("status") == "running":
        return None
    try:
        max_rounds = int(max_rounds)
    except (TypeError, ValueError):
        max_rounds = DEFAULT_ROUNDS
    try:
        budget_tokens = int(budget_tokens)
    except (TypeError, ValueError):
        budget_tokens = DEFAULT_BUDGET
    run = {
        "id": f"ns_{uuid.uuid4().hex[:10]}",
        "topic": topic,
        "status": "running",
        "started_at": _now(),
        "ended_at": "",
        "max_rounds": max(MIN_ROUNDS, min(max_rounds, MAX_ROUNDS)),
        "budget_tokens": max(MIN_BUDGET, min(budget_tokens, MAX_BUDGET)),
        "spent_tokens": 0,
        "rounds": [],
        "dissent_total": 0,
        "dissent_round_done": False,
        "halt_requested": False,
        "halt_reason": "",
        "report": "",
        "reporter": "",
    }
    state["run"] = run
    save_state(state)
    return run


def request_stop() -> bool:
    state = load_state()
    run = state.get("run")
    if not run or run.get("status") != "running":
        return False
    run["halt_requested"] = True
    save_state(state)
    return True


def _parse_stance(text: str) -> dict:
    """The LAST stance line wins — a seat quoting another seat's stance
    earlier in its reply must not be misread as its own verdict."""
    matches = list(STANCE_RE.finditer(text or ""))
    if not matches:
        return {"stance": "silent", "stance_note": ""}
    m = matches[-1]
    return {"stance": m.group(1).lower(),
            "stance_note": m.group(2).strip()[:200]}


def _system_prompt(name: str, persona: str, dissent_round: bool) -> str:
    base = f"""You are {name} on the NIGHT SHIFT of Team Talk. Chris is offline. \
The room is running without him, bounded and on the record: limited rounds, a hard \
token budget, and a final report he reads when he returns. Your transcript here is \
ledgered like everything else.

THE JOB
Work the posted topic like the room works everything: claims need receipts, \
"a story being satisfying is evidence of nothing," and absence of record is not \
evidence of fiction. Anything you can't verify from what's in front of you gets \
marked [unverified]. You are producing material for a report that must SURVIVE \
Chris's review — warm agreement he has to re-check is worth less than one sharp \
edge he can act on.

THE STANCE RULE (mechanical — the engine parses it)
End EVERY reply with exactly one line:
STANCE: DISSENT — <the specific claim you dispute or the gap nobody has addressed>
or
STANCE: CONVERGED — <one line on what the room now agrees on>
Convergence with nothing new is the honest call when it's true; dissent is a duty \
when it's true. A missing stance line is logged as silence.

RULES
- No markers here (no MEMORY:/JOURNAL:/PIN: etc). The night bench is not the room.
- The night bench is MEMORYLESS: you have no chat history, no boot packet, no \
Wall here. Chris's briefing below and this run's transcript are your entire world. \
Never claim to remember something from the room — if the briefing doesn't say it \
and no seat established it this run, it's [unverified].
- Be brief. Chris pays for every token and he is asleep. Say the new thing or converge.
- Do not invent what other seats said, and do not soften what they got wrong."""
    if persona:
        base += f'\n\nYour persona, set by Chris: "{persona}"'
    if dissent_round:
        base += """

*** MANDATORY DISSENT ROUND ***
The room converged with ZERO dissents on record. Per the room's own rule (consensus \
does not register without a logged dissent), this round every seat must ATTACK the \
consensus: find the weakest load-bearing claim and hit it. If it survives your best \
shot, say exactly what you tried and end STANCE: CONVERGED. A dissent you don't \
believe is theater — but a consensus nobody tested is worse."""
    return base


def _context(run: dict) -> str:
    # The in-progress round's earlier replies are already in run["rounds"]
    # (state is saved per message), so the transcript walk covers both
    # history AND this round — no separate cycle log, no double inclusion.
    parts = [f"=== TOPIC FROM CHRIS ===\n{run['topic']}"]
    flat = [(r["n"], m) for r in run["rounds"] for m in r["messages"]]
    older, tail = flat[:-TAIL_MESSAGES], flat[-TAIL_MESSAGES:]
    if older:
        lines = ["=== EARLIER (stance log — full text is in the record) ==="]
        for n, m in older:
            lines.append(f"R{n} {m['name']}: {m['stance'].upper()}"
                         + (f" — {m['stance_note']}" if m.get("stance_note") else ""))
        parts.append("\n".join(lines))
    if tail:
        lines = ["=== RECENT TURNS ==="]
        for n, m in tail:
            lines.append(f"[R{n}] {m['name']}:\n{m['text']}" if m.get("ok")
                         else f"[R{n}] {m['name']}: (errored — not their fault, skip them)")
        parts.append("\n\n".join(lines))
    parts.append("Your night turn. Add something that survives scrutiny, or converge. "
                 "End with your STANCE line.")
    return "\n\n".join(parts)


async def run_round(participants: List[dict]) -> dict:
    """One night round, sequential. The CALLER (app.py) owns ledger events,
    same division as chat and the Workshop. Halt checks happen here so a
    stop is decided by state, not by whoever scheduled the loop."""
    state = load_state()
    run = state.get("run")
    report = {"ran": False, "halted": False, "reason": "", "round": 0, "stances": {}}
    if not run or run.get("status") != "running":
        return report

    for cond, reason in (
        (run.get("halt_requested"), "stopped by Chris"),
        (len(run["rounds"]) >= run["max_rounds"], "round limit reached"),
        (run["spent_tokens"] >= run["budget_tokens"], "token budget spent"),
        (not participants, "no awake seats"),
    ):
        if cond:
            run["halt_reason"] = reason
            save_state(state)
            report.update(halted=True, reason=reason,
                          round=len(run["rounds"]))
            return report

    dissent_round = (run.get("dissent_round_pending") is True)
    run.pop("dissent_round_pending", None)
    n = len(run["rounds"]) + 1
    rnd = {"n": n, "ts": _now(), "dissent_round": dissent_round, "messages": []}
    run["rounds"].append(rnd)
    report.update(ran=True, round=n)

    for p in participants:
        system = _system_prompt(p.get("name", "AI"), p.get("persona", ""),
                                dissent_round)
        ctx = _context(run)
        cap = p.get("max_tokens") or REPLY_TOKENS
        result = await api_client.call_participant(p, system, ctx, max_tokens=cap)
        msg = {
            "id": p.get("id", ""),
            "name": p.get("name", "AI"),
            "color": p.get("color", "#888888"),
            "text": result.get("text", ""),
            "tokens": result.get("tokens", 0) or 0,
            "ok": bool(result.get("ok")),
        }
        msg.update(_parse_stance(msg["text"]) if msg["ok"]
                   else {"stance": "error", "stance_note": ""})
        # Run #1's confirmed inversion: a reply cut off by its token cap
        # loses its STANCE line and was scored "silent" — which counted
        # toward convergence. A truncated DISSENT read as agreement is
        # exactly what this bench exists to catch. If the reply spent its
        # whole budget and has no stance, score it truncated: it blocks
        # convergence instead of feeding it.
        if msg["ok"] and msg["stance"] == "silent" and msg["tokens"] >= cap:
            msg["stance"] = "truncated"
        rnd["messages"].append(msg)
        run["spent_tokens"] += msg["tokens"]
        if msg["stance"] == "dissent":
            run["dissent_total"] += 1
        save_state(state)   # crash-safe: every reply lands on disk as it happens

    report["stances"] = {m["name"]: m["stance"] for m in rnd["messages"]}
    live = [m for m in rnd["messages"] if m["ok"]]
    if not live:
        run["halt_reason"] = "every seat errored"
        report.update(halted=True, reason=run["halt_reason"])
    elif all(m["stance"] in ("converged", "silent") for m in live):
        # "truncated" is deliberately NOT in this tuple — a seat that ran
        # out of tokens mid-thought has not agreed with anyone.
        if run["dissent_total"] == 0 and not run["dissent_round_done"]:
            # Consensus without one logged dissent doesn't register — the
            # room's own rule. Order the attack round, then judge again.
            run["dissent_round_done"] = True
            run["dissent_round_pending"] = True
            report["reason"] = "converged undissented — mandatory dissent round ordered"
        else:
            run["halt_reason"] = ("consensus reached"
                                  + (" (survived the mandatory dissent round)"
                                     if dissent_round else ""))
            report.update(halted=True, reason=run["halt_reason"])
    save_state(state)
    return report


async def write_report(participants: List[dict]) -> Optional[dict]:
    """The seat that dissented hardest writes the report — it saw the
    sharpest edges. Closes the run and archives it."""
    state = load_state()
    run = state.get("run")
    if not run or run.get("status") != "running":
        return None

    by_id = {p.get("id"): p for p in participants}
    counts = {}
    for r in run["rounds"]:
        for m in r["messages"]:
            if m["stance"] == "dissent" and m["id"] in by_id:
                counts[m["id"]] = counts.get(m["id"], 0) + 1
    reporter = (by_id[max(counts, key=counts.get)] if counts
                else (participants[0] if participants else None))

    if reporter:
        system = (f"You are {reporter.get('name', 'AI')}, closing the Night Shift. "
                  "Chris is about to read ONE artifact from this run: your report. "
                  "Write it so it survives his review — receipts over rhetoric, "
                  "[unverified] where you can't check, no flattery of the room's work.")
        stance_log = "\n".join(
            f"R{r['n']} {m['name']}: {m['stance'].upper()}"
            + (f" — {m['stance_note']}" if m.get("stance_note") else "")
            for r in run["rounds"] for m in r["messages"])
        tail = [(r["n"], m) for r in run["rounds"]
                for m in r["messages"]][-TAIL_MESSAGES:]
        recent = "\n\n".join(f"[R{n}] {m['name']}:\n{m['text']}"
                             for n, m in tail if m.get("ok"))
        prompt = (f"=== TOPIC FROM CHRIS ===\n{run['topic']}\n\n"
                  f"=== FULL STANCE LOG ===\n{stance_log}\n\n"
                  f"=== FINAL TURNS ===\n{recent}\n\n"
                  f"The run halted: {run.get('halt_reason', '')}.\n\n"
                  "Write the final report with exactly these sections:\n"
                    "CONSENSUS (what the room agreed on, and which dissent it survived)\n"
                    "DISSENTS THAT STAND (unresolved — the report's most valuable part)\n"
                    "OPEN QUESTIONS FOR CHRIS\n"
                    "RECOMMENDATION (one paragraph, actionable)\n"
                    "No STANCE line needed — the shift is over.")
        cap = max(reporter.get("max_tokens") or 0, REPORT_TOKENS)
        result = await api_client.call_participant(reporter, system, prompt,
                                                   max_tokens=cap)
        if result.get("ok"):
            run["report"] = result.get("text", "")
            run["reporter"] = reporter.get("name", "AI")
            run["spent_tokens"] += result.get("tokens", 0) or 0
        else:
            run["report"] = f"(report generation failed: {result.get('text', '')[:200]})"
            run["reporter"] = reporter.get("name", "AI")

    run["status"] = "done"
    run["ended_at"] = _now()
    save_state(state)
    _archive(run)
    return run


def _archive(run: dict) -> None:
    _ensure_dirs()
    try:
        with open(os.path.join(RUNS_DIR, f"{run['id']}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(run, f, ensure_ascii=False, indent=1)
    except OSError as e:
        print(f"[NIGHT] archive failed: {e}")


def list_runs(limit: int = 20) -> List[dict]:
    """Newest-first summaries of archived runs."""
    _ensure_dirs()
    out = []
    try:
        names = sorted(os.listdir(RUNS_DIR), reverse=True)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(RUNS_DIR, name), "r", encoding="utf-8") as f:
                run = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": run.get("id"), "topic": run.get("topic", "")[:120],
                    "started_at": run.get("started_at", ""),
                    "rounds": len(run.get("rounds", [])),
                    "spent_tokens": run.get("spent_tokens", 0),
                    "halt_reason": run.get("halt_reason", ""),
                    "reporter": run.get("reporter", "")})
    out.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    return out[:max(1, min(limit, 100))]


def get_run(run_id: str) -> Optional[dict]:
    if not re.fullmatch(r"ns_[0-9a-f]{10}", str(run_id or "")):
        return None
    try:
        with open(os.path.join(RUNS_DIR, f"{run_id}.json"), "r",
                  encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
