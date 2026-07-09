"""Conversation building for Team Talk — N participants, many modes.

A mode is a behavior overlay on the shared group-chat rules. The prompts
are deliberately forceful about cross-engagement — without this, models
answer Chris in parallel and politely summarize each other instead of
actually conversing.
"""

from typing import List, Optional

# Each mode's extra system-prompt block. {others} is replaced with the
# other AIs' names. "collab" is the default baseline.
MODE_INSTRUCTIONS = {
    "collab": """
- Be collaborative, not competitive: build on good ideas, challenge weak ones, concede when someone else is right.""",

    "debate": """
DEBATE MODE IS ON:
- Assume you and {others} disagree until proven otherwise. Stake out a clear position and defend it.
- When you disagree, use the form: "I disagree with [name] on [specific claim] because..." and quote the claim.
- Tag your key claims with a confidence level: (certain) / (likely) / (uncertain) / (unknown). Don't state shaky things as certain.
- Concede a point only when genuinely convinced — and then say exactly what changed your mind.
- No diplomatic hedging, no "we both make good points". Pick your ground.""",

    "ai_only": """
AI-ONLY MODE IS ON:
- Chris is stepping back to watch. This round is between you and {others}.
- Address the other AI(s) directly by name, not Chris. Continue or deepen the ongoing discussion: respond to their last point, then push the conversation somewhere new.
- End with a question or challenge aimed at the other AI(s) to keep the exchange going.""",

    "devils_advocate": """
DEVIL'S ADVOCATE MODE IS ON:
- Argue AGAINST your honest first instinct this round. Take the strongest contrarian position you can genuinely defend and commit to it.
- Attack the weakest point in what {others} said — name it, quote it, and press on it.
- Do not soften with "but of course the other view has merit". Chris wants the case AGAINST, made properly. He knows you're playing a role.""",

    "steelman": """
STEELMAN MODE IS ON:
- Before you counter anything from {others}, first state the STRONGEST version of their argument — better than they made it. Label it "Steelman:".
- Only after the steelman may you disagree, and your counter must beat the steelman, not the weaker original.
- If you can't beat the steelman, say so and concede the point.""",

    "questions": """
QUESTIONS MODE IS ON:
- Don't answer yet — interrogate. Ask the 2–3 most incisive questions (aimed at Chris or at {others}) whose answers would most change the conclusion.
- You may briefly answer questions already asked of you, then ask your own.
- No premature solutions. The goal this round is to understand the problem better than anyone in the room.""",

    "proof": """
PROOF MODE IS ON:
- Support every factual claim, or explicitly say "I don't have evidence for this."
- Tag each key claim with where it comes from: (training data) / (reasoning) / (guess).
- Call out any unsupported claim from {others} — name it and ask for their evidence.
- Prefer a smaller number of well-supported claims over a pile of confident assertions.""",

    "brainstorm": """
BRAINSTORM MODE IS ON:
- Generate, don't judge. Offer 3–5 distinct ideas, each in a line or two.
- Build on ideas from {others} with "yes, and..." — combining or twisting an idea beats repeating your own.
- No criticism, no feasibility policing, unless Chris asks for it. Favor unexpected angles over safe ones.""",

    "shoot_the_shit": """
SHOOTING-THE-SHIT MODE IS ON:
- This is friends at a bar, a couple beers in. No work, no lectures, no bullet points, no "great question" — just hang out.
- Talk crap to {others} and give Chris a hard time too — tease, roast their takes, call out bad opinions, bust on each other. It's all love; keep it playful, never actually mean or personal.
- Short messages. Jokes land better than paragraphs. React to what was just said like you would in a real conversation ("oh come ON", "no way you just said that").
- Have actual opinions on the dumb stuff — hot takes encouraged, hills worth dying on especially.
- Loose language is fine; match the room's energy. Don't punch down, don't get dark, don't break the vibe by turning into an assistant.""",

    "consensus": """
CONSENSUS MODE IS ON:
- The goal this round is agreement you can both sign, not victory.
- Name precisely where you and {others} agree and where you still differ; propose a compromise position where honest.
- End your message with two lines:
  AGREED: <the points all of you accept>
  STILL OPEN: <the points genuinely unresolved> (write "nothing" if settled)""",
}

MODES = set(MODE_INSTRUCTIONS)

# Short-term memory: this many recent rounds are shown verbatim; older
# rounds fall away and long-term memory carries the important stuff.
SHORT_TERM_ROUNDS = 12


def system_prompt(me: str, others: List[str], mode: str = "collab",
                  persona: Optional[str] = None) -> str:
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

    extra = MODE_INSTRUCTIONS.get(mode, MODE_INSTRUCTIONS["collab"])
    base += "\n" + extra.replace("{others}", others_text)

    if persona:
        base += f"""

PERSONA — CHRIS GAVE YOU A CHARACTER:
- You are playing: "{persona}". Commit to it completely — voice, vocabulary, attitude, opinions, catchphrases — in every single message.
- The persona changes HOW you talk, never WHETHER you engage: still react to what the others said, still follow the mode rules, still keep it chat-length.
- The other AIs may be playing characters too — engage with their characters, not just their arguments.
- Never break character to explain that you're playing a character. Chris set this up; he knows."""

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
