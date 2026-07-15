"""
surfacer/baseline.py — Team Talk surfacer, build step 2 (offline baseline).

Strictly READ-ONLY over sessions/ and the ledger. It writes NOTHING to
either; all output goes to surfacer/reports/. It never calls
session_manager.load_session() on purpose — that function quarantines
(renames) corrupt files, which would be a write. This script reads each
session file raw and REPORTS parse failures instead of skipping them.

What it does, per the audit build order:
  (1) Cold-start candidate pass — a LOOSE scan (capitalized token + number
      co-occurring in a sentence) to propose entity-value pairs for human
      review. Candidates are written to a review file and NEVER auto-seeded.
  (2) Baseline metrics per seat, using matcher.py UNMODIFIED, over a registry
      seeded from the frozen SEED (Hercher 91, de Grey 1581, Heule 150) plus
      any human-reviewed candidates supplied via --reviewed-candidates:
        - repeat-rate, tier-1 and tier-2 reported separately
        - control repeat-rate over UN-registered candidate claims
        - hedge-rate (matcher's frozen scoping)
        - hedge-resolution rate (0 on history: no SOURCE events exist yet)
        - open-claim age distribution
        - claim frequency per session
      Per the room's frozen split: a CLAIM is (entity, value); a FLAG is
      (seat_id, entity, value). Repeat-rate is a per-seat measure, so it keys
      on the FLAG. (A future SOURCE would resolve a CLAIM room-wide.)
  (3) A machine-readable JSON report + a human-readable per-seat summary.

Run:  python3 surfacer/baseline.py
      python3 surfacer/baseline.py --sessions-dir /opt/team-talk/sessions
"""

import argparse
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

# matcher.py lives beside this file; the app modules live one level up.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, _ROOT)

import matcher  # noqa: E402  (frozen; imported, never modified)

DEFAULT_SESSIONS_DIR = os.path.join(_ROOT, "sessions")
DEFAULT_LEDGER_PATH = os.path.join(_ROOT, "memory", "ledger.jsonl")
DEFAULT_OUT_DIR = os.path.join(_HERE, "reports")

# The frozen seed the room agreed on. Values are strings for the registry.
SEED_PAIRS = [("Hercher", "91"), ("de Grey", "1581"), ("Heule", "150")]

# Loose candidate heuristic (step 1 ONLY — reviewed before it can ever seed).
_CAP_TOKEN = re.compile(r"\b([A-Z][a-zA-Z]{1,})\b")
_NUMBER = re.compile(r"\b(\d{1,6})\b")
_SENTENCE = re.compile(r"[.!?\n]+")
# Ledger actions that would represent a claim being sourced/closed. None of
# these exist as a SOURCE primitive yet, so resolution is 0 on history — but
# we look, honestly, rather than assume.
_RESOLUTION_ACTIONS = {"surfacer_resolved", "claim_sourced", "source_provided"}


# ----------------------------------------------------------------- read-only IO
def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _normalize_round(r):
    """Local, side-effect-free twin of session_manager.normalize_round so we
    never import a module that can write. Upgrades the old two-AI shape."""
    if "responses" in r:
        return r
    return {
        "round": r.get("round"),
        "timestamp": r.get("timestamp", ""),
        "lounge": r.get("lounge", False),
        "responses": [
            {"id": "claude", "name": "Claude", "text": r.get("claude_response", "")},
            {"id": "chatgpt", "name": "ChatGPT", "text": r.get("chatgpt_response", "")},
        ],
    }


def load_posts(sessions_dir):
    """Read every session file RAW. Return (posts, meta).

    posts: list of {seat_id, seat_name, session_id, round, ts, text}
    meta:  scan bookkeeping incl. parse failures (never silently skipped).
    A post is one seat's text in one non-lounge round of a non-lounge session.
    """
    posts = []
    meta = {
        "sessions_dir": sessions_dir,
        "files_seen": 0,
        "sessions_scanned": 0,
        "sessions_excluded_lounge": 0,
        "rounds_excluded_lounge": 0,
        "posts": 0,
        "parse_failures": [],   # [{file, error}] — reported, not skipped silently
    }
    if not os.path.isdir(sessions_dir):
        meta["error"] = f"sessions dir does not exist: {sessions_dir}"
        return posts, meta

    for name in sorted(os.listdir(sessions_dir)):
        if not name.endswith(".json"):
            continue
        meta["files_seen"] += 1
        path = os.path.join(sessions_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                session = json.load(f)
            if not isinstance(session, dict):
                raise ValueError("top-level JSON is not an object")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            meta["parse_failures"].append({"file": name, "error": f"{type(e).__name__}: {e}"})
            continue

        if session.get("lounge"):
            meta["sessions_excluded_lounge"] += 1
            continue
        meta["sessions_scanned"] += 1
        sid = session.get("id", name[:-5])
        s_created = _parse_ts(session.get("created_at"))
        rounds = session.get("rounds") or []
        for raw in rounds:
            try:
                r = _normalize_round(raw)
            except Exception as e:   # a malformed round shouldn't kill the file
                meta["parse_failures"].append({"file": name, "error": f"round: {e}"})
                continue
            if r.get("lounge"):
                meta["rounds_excluded_lounge"] += 1
                continue
            r_ts = _parse_ts(r.get("timestamp")) or s_created
            for resp in r.get("responses") or []:
                text = (resp.get("text") or "").strip()
                if not text:
                    continue
                posts.append({
                    "seat_id": resp.get("id") or "unknown",
                    "seat_name": resp.get("name") or resp.get("id") or "unknown",
                    "session_id": sid,
                    "round": r.get("round"),
                    "ts": r_ts,
                    "text": text,
                })
    meta["posts"] = len(posts)
    # Chronological order: real timestamps first, undated posts kept in file order.
    posts.sort(key=lambda p: (p["ts"] is None, p["ts"] or datetime.min.replace(tzinfo=timezone.utc)))
    return posts, meta


def read_resolution_events(ledger_path):
    """Read-only scan of the ledger for any claim-resolution events. There is
    no SOURCE primitive yet, so this is expected to be empty — we look anyway."""
    found = []
    if not os.path.exists(ledger_path):
        return found
    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("action") in _RESOLUTION_ACTIONS:
                    found.append(e)
    except OSError:
        pass
    return found


# ------------------------------------------------------------ (1) cold start
def cold_start_candidates(posts):
    """LOOSE candidate generation for human review. Deliberately noisier than
    the frozen matcher: any capitalized token co-occurring with a number in a
    sentence becomes a candidate (entity, value). Reviewed before it can seed."""
    agg = {}
    seeded = {(matcher.normalize_extraction(e), str(v)) for e, v in SEED_PAIRS}
    for p in posts:
        for sentence in _SENTENCE.split(p["text"]):
            caps = set(_CAP_TOKEN.findall(sentence))
            nums = set(_NUMBER.findall(sentence))
            if not caps or not nums:
                continue
            for c in caps:
                ent = matcher.normalize_extraction(c)
                if not ent:
                    continue
                for n in nums:
                    key = (ent, n)
                    rec = agg.setdefault(key, {
                        "entity": ent, "value": n, "count": 0,
                        "seats": set(), "example": sentence.strip()[:160],
                        "already_seeded": key in seeded,
                    })
                    rec["count"] += 1
                    rec["seats"].add(p["seat_id"])
    out = []
    for rec in agg.values():
        rec = dict(rec)
        rec["seats"] = sorted(rec["seats"])
        out.append(rec)
    out.sort(key=lambda r: (-r["count"], r["entity"], r["value"]))
    return out


# ------------------------------------------------------------ (2) per-seat metrics
def _claim_sentences(text, registry):
    """Map each (entity,value) found in text to the normalized-identity strings
    of the sentence(s) that carried it — used for tier-1 exact-repeat detection."""
    out = defaultdict(set)
    for sentence in _SENTENCE.split(text):
        claims = registry.extract(sentence)
        if not claims:
            continue
        ident = matcher.normalize_identity(sentence)
        for c in claims:
            out[c].add(ident)
    return out


def _repeat_stats(posts_by_seat, registry):
    """For each seat, walk its posts in chronological order and count claim
    occurrences, tier-2 repeats (any restatement of a claim already made) and
    tier-1 repeats (restatement whose SENTENCE exactly matches an earlier one).
    Flag identity is (seat_id, normalized_entity, value); the claim itself is
    (entity, value). Repeat-rate is per-seat, so it keys on the flag."""
    stats = {}
    for seat_id, seat_posts in posts_by_seat.items():
        occ = 0
        repeats_t2 = 0
        repeats_t1 = 0
        first_seen = {}          # (entity,value) -> ts of first assertion
        seen_sentences = defaultdict(set)  # (entity,value) -> {identity strings}
        hedged_occ = 0
        occ_per_session = defaultdict(int)
        for p in seat_posts:
            claims = registry.extract(p["text"])
            if not claims:
                continue
            sent_map = _claim_sentences(p["text"], registry)
            hedged = matcher.hedged_claims(p["text"], registry)
            for c in claims:
                occ += 1
                occ_per_session[p["session_id"]] += 1
                if c in hedged:
                    hedged_occ += 1
                if c in first_seen:
                    repeats_t2 += 1
                    # tier-1: did the seat repeat the same SENTENCE (normalized)?
                    if sent_map.get(c, set()) & seen_sentences[c]:
                        repeats_t1 += 1
                else:
                    first_seen[c] = p["ts"]
                seen_sentences[c] |= sent_map.get(c, set())
        stats[seat_id] = {
            "occurrences": occ,
            "unique_claims": len(first_seen),
            "repeats_tier2": repeats_t2,
            "repeats_tier1": repeats_t1,
            "hedged_occurrences": hedged_occ,
            "first_seen": first_seen,
            "sessions_with_claims": len(occ_per_session),
        }
    return stats


def _rate(n, d):
    return round(n / d, 4) if d else None


def compute_metrics(posts, registry, control_registry, archive_end, resolution_events):
    posts_by_seat = defaultdict(list)
    names = {}
    sessions_by_seat = defaultdict(set)
    for p in posts:
        posts_by_seat[p["seat_id"]].append(p)
        names.setdefault(p["seat_id"], p["seat_name"])
        sessions_by_seat[p["seat_id"]].add(p["session_id"])

    main = _repeat_stats(posts_by_seat, registry)
    control = _repeat_stats(posts_by_seat, control_registry) if control_registry.pairs() else {}

    seats = {}
    for seat_id, m in main.items():
        occ = m["occurrences"]
        # open-claim age: nothing resolves on history, so every claim is open.
        ages_days = []
        for _, ts in m["first_seen"].items():
            if ts and archive_end:
                ages_days.append(round((archive_end - ts).total_seconds() / 86400.0, 2))
        age_dist = None
        if ages_days:
            age_dist = {
                "open_claims": len(ages_days),
                "min_days": min(ages_days),
                "median_days": round(statistics.median(ages_days), 2),
                "max_days": max(ages_days),
                "mean_days": round(statistics.mean(ages_days), 2),
            }
        c = control.get(seat_id, {})
        seats[seat_id] = {
            "seat_name": names.get(seat_id, seat_id),
            "sessions_participated": len(sessions_by_seat[seat_id]),
            "registered_claim_occurrences": occ,
            "unique_registered_claims": m["unique_claims"],
            "repeat_rate_tier1": _rate(m["repeats_tier1"], occ),
            "repeat_rate_tier2": _rate(m["repeats_tier2"], occ),
            "repeats_tier1": m["repeats_tier1"],
            "repeats_tier2": m["repeats_tier2"],
            "control_claim_occurrences": c.get("occurrences", 0),
            "control_repeat_rate_tier2": _rate(c.get("repeats_tier2", 0), c.get("occurrences", 0)),
            "hedge_rate": _rate(m["hedged_occurrences"], occ),
            "hedged_occurrences": m["hedged_occurrences"],
            "hedge_resolution_rate": 0.0 if m["hedged_occurrences"] else None,
            "hedge_resolution_note": (
                "baseline zero by construction: no SOURCE/resolution events exist "
                "in the ledger yet"),
            "open_claim_age_days": age_dist,
            "claim_frequency_per_session": _rate(occ, len(sessions_by_seat[seat_id])),
        }
    return seats, {"resolution_events_found": len(resolution_events)}


# ------------------------------------------------------------ (3) output
def write_reports(out_dir, candidates, seats, scan_meta, registry, control_registry,
                  extra):
    os.makedirs(out_dir, exist_ok=True)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- candidate review file (JSON + human MD) ---
    cand_json = os.path.join(out_dir, "candidates_review.json")
    with open(cand_json, "w", encoding="utf-8") as f:
        json.dump({"generated_at": generated,
                   "note": "LOOSE candidates for human review — NOT auto-seeded. "
                           "Review, then pass approved pairs via --reviewed-candidates.",
                   "count": len(candidates), "candidates": candidates}, f, indent=2)
    cand_md = os.path.join(out_dir, "candidates_review.md")
    with open(cand_md, "w", encoding="utf-8") as f:
        f.write(f"# Cold-start candidate entity-value pairs (review before seeding)\n\n")
        f.write(f"_Generated {generated}. {len(candidates)} candidates. "
                f"Loose heuristic — reviewed by a human before any can seed the registry._\n\n")
        f.write("| # | entity | value | count | seats | seeded? | example |\n")
        f.write("|---|--------|-------|-------|-------|---------|---------|\n")
        for i, c in enumerate(candidates[:200], 1):
            ex = c["example"].replace("|", "\\|")
            f.write(f"| {i} | {c['entity']} | {c['value']} | {c['count']} | "
                    f"{','.join(c['seats'])} | {'yes' if c['already_seeded'] else ''} | {ex} |\n")
        if len(candidates) > 200:
            f.write(f"\n_({len(candidates) - 200} more in the JSON.)_\n")

    # --- machine-readable baseline report ---
    report = {
        "generated_at": generated,
        "matcher": extra.get("matcher_used", "surfacer/matcher.py (frozen, 19 tests passing)"),
        "claim_identity": "(entity, value)",
        "flag_identity": "(seat_id, normalized_entity, value) — repeat-rate keys on this",
        "registry_seed": [{"entity": e, "value": v} for e, v in registry.pairs()],
        "control_registry_size": len(control_registry.pairs()),
        "scan": scan_meta,
        "extra": extra,
        "seats": seats,
    }
    report_json = os.path.join(out_dir, "baseline_report.json")
    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    # --- human-readable summary ---
    summary_md = os.path.join(out_dir, "baseline_summary.md")
    lines = _summary_lines(report)
    with open(summary_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return {"candidates_json": cand_json, "candidates_md": cand_md,
            "report_json": report_json, "summary_md": summary_md,
            "summary_text": "\n".join(lines)}


def _summary_lines(report):
    L = ["# Surfacer baseline — per-seat summary", "",
         f"Generated: {report['generated_at']}",
         f"Matcher: {report['matcher']}",
         f"Claim identity: {report['claim_identity']} · "
         f"Flag identity: {report.get('flag_identity', '')}", ""]
    s = report["scan"]
    L += ["## Scan",
          f"- sessions dir: `{s.get('sessions_dir')}`",
          f"- session files seen: {s.get('files_seen', 0)}",
          f"- non-lounge sessions scanned: {s.get('sessions_scanned', 0)} "
          f"(excluded lounge sessions: {s.get('sessions_excluded_lounge', 0)}, "
          f"excluded lounge rounds: {s.get('rounds_excluded_lounge', 0)})",
          f"- posts analyzed: {s.get('posts', 0)}",
          f"- **parse failures: {len(s.get('parse_failures', []))}**"
          + (f" → {', '.join(pf['file'] for pf in s['parse_failures'][:10])}"
             if s.get('parse_failures') else " (none)"),
          f"- resolution/SOURCE events in ledger: {report['extra'].get('resolution_events_found', 0)}",
          ""]
    seed = ", ".join(f"{p['entity']}={p['value']}" for p in report["registry_seed"])
    L += [f"Registry (seed + reviewed): {seed}",
          f"Control registry size (un-registered candidates): {report['control_registry_size']}", ""]

    if not report["seats"]:
        L += ["## Seats", "", "_No registered-claim activity found "
              "(either no archive present here, or no seat repeated a seeded claim)._"]
        return L

    L += ["## Per seat", ""]
    for seat_id, m in sorted(report["seats"].items(),
                             key=lambda kv: -(kv[1]["registered_claim_occurrences"])):
        age = m["open_claim_age_days"]
        age_s = (f"{age['open_claims']} open, median {age['median_days']}d "
                 f"(min {age['min_days']}d / max {age['max_days']}d)") if age else "—"
        L += [f"### {m['seat_name']} (`{seat_id}`)",
              f"- sessions participated: {m['sessions_participated']}",
              f"- registered-claim occurrences: {m['registered_claim_occurrences']} "
              f"({m['unique_registered_claims']} unique)",
              f"- **repeat-rate tier-2 (any restatement): {m['repeat_rate_tier2']}** "
              f"({m['repeats_tier2']} repeats)",
              f"- repeat-rate tier-1 (verbatim sentence): {m['repeat_rate_tier1']} "
              f"({m['repeats_tier1']} repeats)",
              f"- control repeat-rate tier-2 (un-registered claims): "
              f"{m['control_repeat_rate_tier2']} "
              f"(over {m['control_claim_occurrences']} control occurrences)",
              f"- hedge-rate: {m['hedge_rate']} ({m['hedged_occurrences']} hedged)",
              f"- hedge-resolution rate: {m['hedge_resolution_rate']} "
              f"({m['hedge_resolution_note']})",
              f"- open-claim age: {age_s}",
              f"- claim frequency per session: {m['claim_frequency_per_session']}",
              ""]
    L += ["## How to read this",
          "- If **tier-2 repeat-rate** is not clearly above the **control repeat-rate**, "
          "the 'repeats unsourced claims' phenomenon is not yet distinguishable from noise.",
          "- hedge-resolution is 0 by construction until a SOURCE primitive exists; "
          "it is the baseline against which a live surfacer would be measured.",
          "- Every number is (seat_id, normalized_entity, value)-scoped and read-only; "
          "nothing here was written back to sessions/ or the ledger."]
    return L


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Surfacer offline baseline (read-only).")
    ap.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR)
    ap.add_argument("--ledger-path", default=DEFAULT_LEDGER_PATH)
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--aliases", default="",
                    help="optional JSON: [[entity, value, alias], ...] — switches to the "
                         "matcher_v2 recall extension. Run with and without it to diff v1 vs v2.")
    ap.add_argument("--reviewed-candidates", default="",
                    help="optional JSON file: [[entity, value], ...] approved by a human")
    args = ap.parse_args()

    # Hard gate: the frozen matcher tests must pass before we trust any number.
    import unittest
    suite = unittest.defaultTestLoader.loadTestsFromModule(matcher)
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    if not result.wasSuccessful():
        print("ABORT: matcher.py frozen tests FAILED — not running the baseline.")
        sys.exit(1)

    posts, scan_meta = load_posts(args.sessions_dir)
    resolution_events = read_resolution_events(args.ledger_path)

    # (1) cold-start candidates (never auto-seeded)
    candidates = cold_start_candidates(posts)

    # (2) registry = frozen SEED + any human-reviewed candidates
    reviewed = []
    if args.reviewed_candidates and os.path.exists(args.reviewed_candidates):
        try:
            with open(args.reviewed_candidates, "r", encoding="utf-8") as f:
                reviewed = [(str(e), str(v)) for e, v in json.load(f)]
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"WARNING: could not read reviewed candidates ({e}); using SEED only.")
    # v1 (frozen) by default. --aliases switches to the matcher_v2 recall
    # extension so the two runs can be diffed, per the frozen-change protocol.
    aliases = []
    matcher_used = "v1 (surfacer/matcher.py, frozen)"
    if args.aliases and os.path.exists(args.aliases):
        try:
            with open(args.aliases, "r", encoding="utf-8") as f:
                aliases = [(str(e), str(v), str(a)) for e, v, a in json.load(f)]
            import matcher_v2
            registry = matcher_v2.AliasRegistry(SEED_PAIRS + reviewed, aliases)
            matcher_used = f"v2 (surfacer/matcher_v2.py, {len(aliases)} aliases)"
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"WARNING: could not read aliases ({e}); falling back to v1.")
            registry = matcher.Registry(SEED_PAIRS + reviewed)
    else:
        registry = matcher.Registry(SEED_PAIRS + reviewed)

    # control = candidate pairs that are NOT in the registry (the comparator),
    # always v1 — the control measures baseline noise, not the alias lift.
    reg_pairs = set(registry.pairs())
    control_pairs = [(c["entity"], c["value"]) for c in candidates
                     if (matcher.normalize_extraction(c["entity"]), c["value"]) not in reg_pairs]
    control_registry = matcher.Registry(control_pairs)

    archive_end = max((p["ts"] for p in posts if p["ts"]), default=None)
    seats, extra = compute_metrics(posts, registry, control_registry, archive_end,
                                   resolution_events)
    extra["matcher_used"] = matcher_used

    out = write_reports(args.out_dir, candidates, seats, scan_meta, registry,
                        control_registry, extra)

    print(out["summary_text"])
    print("\n---\nWrote:")
    for k in ("candidates_json", "candidates_md", "report_json", "summary_md"):
        print(f"  {out[k]}")


if __name__ == "__main__":
    main()
