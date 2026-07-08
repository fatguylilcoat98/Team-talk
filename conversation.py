"""Conversation state management for Team Talk.

Builds the full-context prompt each AI receives on every round:
complete history with speaker attribution, plus the other AI's
most recent response called out explicitly.
"""

from typing import List, Optional

CLAUDE_SYSTEM_PROMPT = """You are Claude, an AI collaborating with ChatGPT and Chris on a discussion.

You are in a three-way conversation. You can see:
- Chris's current message
- ChatGPT's previous response (if any)
- Full conversation history

Respond directly to Chris's question or request. If ChatGPT has said something you want to address, do so within your response.

Be collaborative, not competitive. Build on good ideas, challenge weak ones, ask clarifying questions.

Stay focused on the topic at hand."""

CHATGPT_SYSTEM_PROMPT = """You are ChatGPT, an AI collaborating with Claude and Chris on a discussion.

You are in a three-way conversation. You can see:
- Chris's current message
- Claude's previous response (if any)
- Full conversation history

Respond directly to Chris's question or request. If Claude has said something you want to address, do so within your response.

Be collaborative, not competitive. Build on good ideas, challenge weak ones, ask clarifying questions.

Stay focused on the topic at hand."""


def build_context(rounds: List[dict], current_message: str, ai: str) -> str:
    """Build the prompt for one AI.

    Args:
        rounds: previous rounds (each with chris_message / claude_response /
            chatgpt_response / timestamp).
        current_message: Chris's new message for this round.
        ai: "claude" or "chatgpt" — the AI this prompt is being built for.
    """
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
        lines.append(f"{other_name}'s last response (for context): {other_last}")

    lines.append("")
    lines.append(f"Please respond to Chris and engage with {other_name}'s points where relevant.")
    return "\n".join(lines)


def _last_response(rounds: List[dict], key: str) -> Optional[str]:
    for r in reversed(rounds):
        text = r.get(key)
        if text and not text.startswith("Error:"):
            return text
    return None
