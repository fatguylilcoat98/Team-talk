"""Conversation building for Team Talk — N participants, three modes.

Modes:
  collab   — the default: engage with the others, then answer Chris
  debate   — forced disagreement, explicit "I disagree with X because",
             confidence ratings on claims
  ai_only  — Chris steps back; the AIs talk to each other directly

The prompts are deliberately forceful about cross-engagement — without
this, models answer Chris in parallel and politely summarize each other
instead of actually conversing.
"""

from typing import List, Optional

MODES = {"collab", "debate", "ai_only"}

# Short-term memory: this many recent rounds are shown verbatim; older
# rounds fall away and long-term memory carries the important stuff.
SHORT_TERM_ROUNDS = 12


def system_prompt(me: str, others: List[str], mode: str = "collab") -> str:
    others_text = _join_names(others)
    base = f"""You are {me}, in a live group chat with {others_text} (other AIs) and Chris (a human).

FACTS ABOUT THIS CHAT — never contradict these:
- The full transcript, including every message from {others_text}, is included in each message you receive. You CAN see everything they say, and they can see everything you say. Never claim otherwise.
- This is one continuous conversation, not a Q&A service. You are a participant, not a panelist.

HOW TO BEHAVE:
- Before answering Chris, react to the most recent message from the other AI(s): pick at least one specific claim, quote or name it, and say plainly whether you agree or disagree and why. Skip this only if they haven't spoken yet.
- If you disagree, say so directly and argue the point. Do NOT smooth it over, do NOT claim you have a "unified understanding" when you don't, and do NOT politely restate your own previous answer.
- Never summarize the conversation back to Chris — he was there. Advance it: add something new, challenge something, or ask a pointed question.
- Speak as yourself ("I"), address the others by name, and keep a conversational register — this is a chat, not a report.
- Keep messages reasonably tight — a chat message, not an essay.

MEMORY:
- You have persistent long-term memory across sessions. Saved memories appear in the LONG-TERM MEMORY section when there are any.
- To save something genuinely worth remembering for future conversations (a fact about Chris, a decision the group made, a strong preference — NOT small talk), end your message with a line of the form:
  MEMORY: <one short sentence>
  Maximum 2 per message; most messages should save none. The line is stored and removed from your visible reply automatically.
- Only the most recent {SHORT_TERM_ROUNDS} rounds of a conversation are shown verbatim — anything older survives only if someone saved it to memory.

ATTACHMENTS:
- Chris can attach pictures and files. Images are shown to you directly; text/PDF contents appear in an ATTACHED FILES section. Refer to them naturally.""".replace("{SHORT_TERM_ROUNDS}", str(SHORT_TERM_ROUNDS))

    if mode == "debate":
        base += f"""

DEBATE MODE IS ON:
- Assume you and {others_text} disagree until proven otherwise. Stake out a clear position and defend it.
- When you disagree, use the form: "I disagree with [name] on [specific claim] because..." and quote the claim.
- Tag your key claims with a confidence level: (certain) / (likely) / (uncertain) / (unknown). Don't state shaky things as certain.
- Concede a point only when genuinely convinced — and then say exactly what changed your mind.
- No diplomatic hedging, no "we both make good points". Pick your ground."""
    elif mode == "ai_only":
        base += f"""

AI-ONLY MODE IS ON:
- Chris is stepping back to watch. This round is between you and {others_text}.
- Address the other AI(s) directly by name, not Chris. Continue or deepen the ongoing discussion: respond to their last point, then push the conversation somewhere new.
- End with a question or challenge aimed at the other AI(s) to keep the exchange going."""
    else:
        base += """

- Be collaborative, not competitive: build on good ideas, challenge weak ones, concede when someone else is right."""

    return base


def build_context(
    rounds: List[dict],
    current_message: str,
    me: str,
    others: List[str],
    mode: str = "collab",
    so_far: Optional[List[dict]] = None,
    memory_block: str = "",
    attachments_block: str = "",
) -> str:
    """Build the user-message prompt for one AI.

    Args:
        rounds: previous normalized rounds (chris_message + responses list).
        current_message: Chris's new message for this round.
        me: this AI's display name.
        others: the other AIs' display names.
        mode: collab | debate | ai_only.
        so_far: in sequential turn mode, responses already given THIS round
            by AIs that spoke before this one — [{"name", "text"}].
        memory_block: long-term memory section (may be empty).
        attachments_block: ATTACHED FILES section for this round (may be empty).
    """
    lines = []
    if memory_block:
        lines.append(memory_block)
        lines.append("")

    lines.append("=== CONVERSATION HISTORY ===")
    if not rounds:
        lines.append("(This is the first round — no history yet.)")
    shown = rounds
    if len(rounds) > SHORT_TERM_ROUNDS:
        shown = rounds[-SHORT_TERM_ROUNDS:]
        lines.append(
            f"(Showing the last {SHORT_TERM_ROUNDS} of {len(rounds)} rounds — "
            f"rely on long-term memory for older context.)"
        )
    for r in shown:
        lines.append("")
        lines.append(f"[Round {r['round']}] ({r.get('timestamp', '')})")
        chris_line = f"Chris: {r['chris_message']}"
        att_names = [a.get("name", "?") for a in r.get("attachments", [])]
        if att_names:
            chris_line += f"  [attached: {', '.join(att_names)}]"
        lines.append(chris_line)
        for resp in r.get("responses", []):
            lines.append(f"{resp['name']}: {resp['text']}")

    lines.append("")
    lines.append("=== CURRENT ROUND ===")
    lines.append(f"Chris: {current_message}")
    if attachments_block:
        lines.append("")
        lines.append(attachments_block)

    if so_far:
        lines.append("")
        lines.append("Already this round (they spoke before you — engage with this too):")
        for resp in so_far:
            lines.append(f"{resp['name']}: {resp['text']}")

    last_lines = _last_responses(rounds, others)
    if last_lines and not so_far:
        lines.append("")
        lines.append("Most recent message from each other AI (react to these first):")
        lines.extend(last_lines)

    lines.append("")
    others_text = _join_names(others)
    if mode == "ai_only":
        lines.append(
            f"Now write your next chat message as {me}, addressed to {others_text} "
            f"(Chris is watching). Engage with their latest points directly and end "
            f"with a question or challenge for them."
        )
    elif so_far or last_lines:
        lines.append(
            f"Now write your next chat message as {me}. Start by engaging with what "
            f"{others_text} said — quote or name one specific point and agree or push "
            f"back on it — then respond to Chris. Do not summarize; converse."
        )
    else:
        lines.append(
            f"Now write your next chat message as {me}. The other AI(s) haven't spoken "
            f"yet, so just respond to Chris directly and conversationally."
        )
    return "\n".join(lines)


def _last_responses(rounds: List[dict], others: List[str]) -> List[str]:
    found = {}
    for r in reversed(rounds):
        for resp in r.get("responses", []):
            name = resp.get("name")
            if name in others and name not in found:
                text = resp.get("text", "")
                if text and not text.startswith("Error:"):
                    found[name] = text
        if len(found) == len(others):
            break
    return [f"{name}: {text}" for name, text in found.items()]


def _join_names(names: List[str]) -> str:
    if not names:
        return "the other AIs"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"
