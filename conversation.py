"""Conversation state management for Team Talk.

Builds the full-context prompt each AI receives on every round:
complete history with speaker attribution, plus the other AI's
most recent response called out explicitly.

The system prompts are deliberately forceful about cross-engagement —
without this, models tend to answer Chris in parallel and politely
summarize each other instead of actually conversing.
"""

from typing import List, Optional


def _system_prompt(me: str, other: str) -> str:
    return f"""You are {me}, in a live three-way group chat with {other} (another AI) and Chris (a human).

FACTS ABOUT THIS CHAT — never contradict these:
- The full transcript, including every one of {other}'s messages, is included in each message you receive. You CAN see everything {other} says, and {other} can see everything you say. Never claim otherwise.
- This is one continuous conversation, not a Q&A service. You are a participant, not a panelist.

HOW TO BEHAVE:
- Before answering Chris, react to {other}'s most recent message: pick at least one specific claim, quote or name it, and say plainly whether you agree or disagree and why. Skip this only if {other} hasn't spoken yet.
- If you disagree with {other}, say so directly and argue the point. Do NOT smooth it over, do NOT claim you two have a "unified understanding" when you don't, and do NOT politely restate your own previous answer.
- Never summarize the conversation back to Chris — he was there. Advance it instead: add something new, challenge something, or ask {other} or Chris a pointed question.
- Speak as yourself ("I"), address {other} and Chris by name, and keep a conversational register — this is a chat between three people, not a report.
- Be collaborative, not competitive: build on good ideas, challenge weak ones, concede when {other} is right.
- Stay focused on the topic at hand, and keep responses reasonably tight — a chat message, not an essay."""


CLAUDE_SYSTEM_PROMPT = _system_prompt("Claude", "ChatGPT")
CHATGPT_SYSTEM_PROMPT = _system_prompt("ChatGPT", "Claude")


def build_context(rounds: List[dict], current_message: str, ai: str) -> str:
    """Build the prompt for one AI.

    Args:
        rounds: previous rounds (each with chris_message / claude_response /
            chatgpt_response / timestamp).
        current_message: Chris's new message for this round.
        ai: "claude" or "chatgpt" — the AI this prompt is being built for.
    """
    me = "Claude" if ai == "claude" else "ChatGPT"
    other_name = "ChatGPT" if ai == "claude" else "Claude"
    other_key = "chatgpt_response" if ai == "claude" else "claude_response"

    lines = ["=== CONVERSATION HISTORY ==="]
    if not rounds:
        lines.append("(This is the first round — no history yet.)")
    for r in rounds:
        lines.append("")
        lines.append(f"[Round {r['round']}] ({r.get('timestamp', '')})")
        lines.append(f"Chris: {r['chris_message']}")
        lines.append("")
        lines.append(f"Claude: {r['claude_response']}")
        lines.append(f"ChatGPT: {r['chatgpt_response']}")

    lines.append("")
    lines.append("=== CURRENT ROUND ===")
    lines.append(f"Chris: {current_message}")

    other_last = _last_response(rounds, other_key)
    if other_last:
        lines.append("")
        lines.append(f"{other_name}'s most recent message (react to this first): {other_last}")
        lines.append("")
        lines.append(
            f"Now write your next chat message as {me}. Start by engaging with "
            f"{other_name}'s message above — quote or name one specific point and "
            f"agree or push back on it — then respond to Chris. Do not summarize; converse."
        )
    else:
        lines.append("")
        lines.append(
            f"Now write your next chat message as {me}. {other_name} hasn't spoken yet, "
            f"so just respond to Chris directly and conversationally."
        )
    return "\n".join(lines)


def _last_response(rounds: List[dict], key: str) -> Optional[str]:
    for r in reversed(rounds):
        text = r.get(key)
        if text and not text.startswith("Error:"):
            return text
    return None
