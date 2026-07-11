"""🔨 Workshop engine — the work cycle.

One cycle = every unlocked seat gets a private bench turn, in sequence.
Each seat sees the goal, the live artifact, and this cycle's earlier
results, then either PASSes or submits a complete replacement inside a
```workshop fence. The check runs immediately (script mode); a fail
reverts the live artifact, receipts ✗ REJECTED with the real output,
and locks the seat for the next cycle. No audience. No style points.
"""

import asyncio
import os
import re
from typing import List, Optional

import api_client
import workshop_store

# GREEDY to the LAST closing fence: artifacts are specs and code, and
# specs contain their own ``` blocks. The non-greedy version cut every
# submission at its first inner fence — the room spent ten cycles
# rewriting a spec my regex kept truncating. NOTE: comes after the close.
FENCE = re.compile(r"```workshop\s*\n(.*)\n?```", re.DOTALL)
PASS_RE = re.compile(r"^\s*PASS\b", re.IGNORECASE)
CHECK_TIMEOUT = 30
MAX_NOTE = 200
BENCH_MAX_TOKENS = int(os.getenv("WORKSHOP_MAX_TOKENS", "6000"))


def _system_prompt(seat_name: str, target: dict) -> str:
    return f"""You are {seat_name}, alone at the Workshop bench. No audience, no chat, \
no other seats reading this. Nothing you write here earns respect — only the check \
decides, and the check is code, not a vote.

THE TARGET
Goal: {target['goal']}
Artifact: {target['filename']} — you own it jointly with the other seats.
Judge: {"a check script runs immediately; exit 0 = your edit stands" if target['check_mode'] == 'script' else "Chris rules on your edit later"}.

THE RULES (mechanical, enforced by code):
1. Reply with EITHER the single word PASS on the first line (plus one honest line on \
why you're not editing) — OR your complete replacement artifact inside a fence:
```workshop
<the ENTIRE new file content — not a diff, not a fragment>
```
followed by one line: NOTE: <what you changed and why, one sentence>.
2. A submission that fails the check is REVERTED, you get a ✗ REJECTED receipt with \
the actual test output, and you are WRITE-LOCKED for the next cycle — publicly, on \
the ledger. Taking the honest PASS costs nothing. A failing edit costs a cycle.
3. Improve the artifact toward the goal. Small correct steps beat big impressive \
ones. Never delete working material to look decisive. If a previous seat's approach \
is wrong, replace it and say so in your NOTE — the chain keeps their version forever.
4. No markers here (no MEMORY:/JOURNAL:/etc). The bench is not the room."""


def _bench_context(target: dict, live_content: str, cycle_log: List[str],
                   recent_failures: List[dict]) -> str:
    parts = [f"=== LIVE ARTIFACT ({target['filename']}) ===\n{live_content}"]
    if recent_failures:
        lines = ["=== RECENT FAILED CHECKS (learn from these) ==="]
        for f in recent_failures[-3:]:
            lines.append(f"v{f['v']} by {f['by']}: {f['check']['output'][:400]}")
        parts.append("\n".join(lines))
    if cycle_log:
        parts.append("=== EARLIER THIS CYCLE ===\n" + "\n".join(cycle_log))
    parts.append("Your bench turn. PASS or a complete ```workshop fence, per the rules.")
    return "\n\n".join(parts)


def extract_edit(text: str) -> Optional[dict]:
    """Returns {"pass": True, note} or {"pass": False, content, note} or None."""
    if PASS_RE.match(text or ""):
        first_lines = (text or "").strip().splitlines()
        note = first_lines[1].strip() if len(first_lines) > 1 else ""
        return {"pass": True, "note": note[:MAX_NOTE]}
    m = FENCE.search(text or "")
    if not m:
        return None
    content = m.group(1)
    note_m = re.search(r"NOTE:\s*(.+)", text[m.end():])
    note = (note_m.group(1).strip() if note_m else "")[:MAX_NOTE]
    if len(content.encode("utf-8")) > workshop_store.MAX_ARTIFACT_BYTES:
        return None
    return {"pass": False, "content": content, "note": note}


async def run_check(target: dict, content: str) -> dict:
    """Script mode: write the candidate artifact, run Chris's check.py in
    the workshop dir with a hard timeout. Exit 0 = passed. Manual mode:
    verdict is 'pending' until Chris rules."""
    if target["check_mode"] != "script":
        return {"status": "pending", "output": "awaiting Chris's ruling"}
    workshop_store._ensure_dirs()
    artifact_path = os.path.join(workshop_store.ARTIFACT_DIR, target["filename"])
    with open(artifact_path, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", workshop_store.CHECK_PATH,
            cwd=workshop_store.ARTIFACT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return {"status": "failed", "output": f"check timed out after {CHECK_TIMEOUT}s"}
        output = (out or b"").decode("utf-8", errors="replace")[-1500:]
        return {"status": "passed" if proc.returncode == 0 else "failed",
                "output": output}
    except OSError as e:
        return {"status": "failed", "output": f"check could not run: {e}"}


async def run_cycle(participants: List[dict]) -> dict:
    """One full work cycle. Returns a report; the CALLER (app.py) owns
    ledger events and receipts, same division as the chat pipeline."""
    state = workshop_store.load_state()
    target = state.get("target")
    report = {"ran": False, "cycle": state.get("cycles", 0), "turns": []}
    if not target or target.get("status") != "active":
        return report

    # Manual-judge mode: the bench WAITS FOR THE JUDGE. Cycling while
    # rulings are pending just piles up blind rewrites (the seats can't
    # see each other's pending content) and burns Chris's API budget —
    # ten cycles of that taught us this rule.
    if target.get("check_mode") == "manual":
        chain = workshop_store.list_versions(500)
        ruled = {e["verdict_for"] for e in chain if e.get("verdict_for")}
        pending = [e for e in chain
                   if not e.get("verdict_for")
                   and e.get("check", {}).get("status") == "pending"
                   and e.get("v") not in ruled]
        if pending:
            report["waiting_on_judge"] = len(pending)
            return report

    workshop_store.tick_locks(state)
    state["cycles"] = state.get("cycles", 0) + 1
    report["ran"] = True
    report["cycle"] = state["cycles"]

    chain = workshop_store.list_versions(200)
    recent_failures = [e for e in chain
                       if not e.get("verdict_for") and e.get("check", {}).get("status") == "failed"]
    cycle_log: List[str] = []

    for p in participants:
        turn = {"seat": p["id"], "name": p["name"], "action": "skipped", "note": ""}
        if workshop_store.is_locked(state, p["id"]):
            turn["action"] = "locked"
            report["turns"].append(turn)
            cycle_log.append(f"{p['name']}: write-locked this cycle")
            continue
        live = workshop_store.latest_passing()
        live_content = workshop_store.read_version(live["v"]) if live else ""
        system = _system_prompt(p["name"], target)
        ctx = _bench_context(target, live_content or "", cycle_log, recent_failures)
        result = await api_client.call_participant(
            p, system, ctx, max_tokens=BENCH_MAX_TOKENS, context="workshop")
        if not result.get("ok"):
            turn["action"] = "error"
            turn["note"] = result.get("text", "")[:200]
            report["turns"].append(turn)
            cycle_log.append(f"{p['name']}: bench turn errored")
            continue
        edit = extract_edit(result["text"])
        if edit is None:
            # Unparseable reply = a failed submission in every way that
            # matters, but we don't burn a version on garbage: seat is
            # locked (rule 1 is mechanical), nothing lands on the chain.
            turn["action"] = "malformed"
            workshop_store.lock_seat(state, p["id"])
            report["turns"].append(turn)
            cycle_log.append(f"{p['name']}: malformed reply — locked next cycle")
            continue
        if edit["pass"]:
            turn["action"] = "pass"
            turn["note"] = edit["note"]
            report["turns"].append(turn)
            cycle_log.append(f"{p['name']}: PASS — {edit['note']}")
            continue
        check = await run_check(target, edit["content"])
        entry = workshop_store.append_version(edit["content"], p["name"],
                                              note=edit["note"], check=check)
        turn["version"] = entry["v"]
        turn["note"] = edit["note"]
        turn["check"] = check
        if check["status"] == "failed":
            turn["action"] = "rejected"
            workshop_store.lock_seat(state, p["id"])
            cycle_log.append(f"{p['name']}: v{entry['v']} FAILED the check — "
                             f"reverted, locked next cycle")
            recent_failures.append(entry)
        else:
            turn["action"] = "landed" if check["status"] == "passed" else "pending"
            cycle_log.append(f"{p['name']}: v{entry['v']} "
                             f"{'landed' if check['status'] == 'passed' else 'submitted, awaiting ruling'}"
                             f" — {edit['note']}")
        report["turns"].append(turn)

    workshop_store.save_state(state)
    return report
