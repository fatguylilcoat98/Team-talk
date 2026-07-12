"""PDF export for Team Talk sessions.

Chris's ask: long sessions make the chat window unwieldy, and a session
worth keeping deserves a file that survives the app — something you can
archive, print, or hand to someone with no server in sight.

Rendering notes:
- Uses the system DejaVu fonts (stock on Debian/Ubuntu) so the room's
  actual words — accents, arrows, box-drawing — render correctly. If the
  fonts are missing we fall back to Helvetica and transliterate to
  latin-1 rather than crash.
- Color emoji have no glyphs in any PDF base font, so astral-plane
  characters are stripped from the output. The words are the record;
  the confetti stays in the browser.
"""

import os
import re
from datetime import datetime, timezone

from fpdf import FPDF

# Everything above the Basic Multilingual Plane (all modern emoji) plus
# the invisible joiners/variation selectors they travel with.
_ASTRAL = re.compile("[\\U00010000-\\U0010FFFF\\uFE0F\\u200D]")

_FONT_DIRS = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/local/share/fonts/dejavu",
)

INK = (38, 34, 28)          # body text — matches the UI's #26221c
FAINT = (138, 130, 114)     # markers / metadata — #8a8272
CHRIS_GOLD = (184, 134, 11)


def _find_fonts():
    """(regular, bold) DejaVu paths, or (None, None) if unavailable."""
    for d in _FONT_DIRS:
        reg = os.path.join(d, "DejaVuSans.ttf")
        bold = os.path.join(d, "DejaVuSans-Bold.ttf")
        if os.path.isfile(reg) and os.path.isfile(bold):
            return reg, bold
    return None, None


def _hex_rgb(color: str, fallback=(136, 136, 136)):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(color or "").strip())
    if not m:
        return fallback
    h = m.group(1)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class _SessionPDF(FPDF):
    def __init__(self, title: str):
        super().__init__(format="A4")
        self.doc_title = title
        reg, bold = _find_fonts()
        self.unicode_ok = bool(reg)
        if self.unicode_ok:
            self.add_font("Deja", "", reg)
            self.add_font("Deja", "B", bold)
            self.family = "Deja"
        else:
            self.family = "Helvetica"
        self.set_margins(15, 15, 15)
        self.set_auto_page_break(auto=True, margin=18)

    def clean(self, text: str) -> str:
        text = _ASTRAL.sub("", str(text or ""))
        if not self.unicode_ok:
            text = text.encode("latin-1", "replace").decode("latin-1")
        return text

    def footer(self):
        self.set_y(-13)
        self.set_font(self.family, "", 8)
        self.set_text_color(*FAINT)
        self.cell(0, 6, f"{self.doc_title}  ·  page {self.page_no()}/{{nb}}",
                  align="C")


def export_pdf(session: dict, normalize_round, mode_marker) -> bytes:
    """Render one session as PDF bytes. Layout mirrors the HTML export."""
    pdf = _SessionPDF(str(session.get("id", "Team Talk")))
    _render_session(pdf, session, normalize_round, mode_marker)
    return bytes(pdf.output())


def export_pdf_bundle(sessions: list, normalize_round, mode_marker) -> bytes:
    """Many sessions, one archive PDF — Chris's ask: the whole room's
    record in a single file that's easy to save, transfer, and show.
    Cover page with the roll call, then each session from a fresh page."""
    pdf = _SessionPDF("Team Talk archive")
    pdf.add_page()
    pdf.set_font(pdf.family, "B", 20)
    pdf.set_text_color(*INK)
    pdf.ln(30)
    pdf.cell(0, 10, "Team Talk — The Record", align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(pdf.family, "", 10)
    pdf.set_text_color(*FAINT)
    exported = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_rounds = sum(len(s.get("rounds", [])) for s in sessions)
    pdf.cell(0, 6, pdf.clean(
        f"{len(sessions)} session{'s' if len(sessions) != 1 else ''} · "
        f"{total_rounds} rounds · exported {exported}"),
        align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)
    pdf.set_font(pdf.family, "", 9.5)
    pdf.set_text_color(*INK)
    for s in sessions:
        first = (s.get("rounds") or [{}])[0].get("chris_message", "")
        pdf.multi_cell(0, 5.5, pdf.clean(
            f"•  {s.get('id', '?')}  ·  {str(s.get('created_at', ''))[:10]}  ·  "
            f"{len(s.get('rounds', []))} rounds — {first[:80]}"),
            new_x="LMARGIN", new_y="NEXT")
    for s in sessions:
        _render_session(pdf, s, normalize_round, mode_marker)
    return bytes(pdf.output())


def _render_session(pdf: _SessionPDF, session: dict, normalize_round,
                    mode_marker) -> None:
    title = str(session.get("id", "Team Talk"))
    pdf.add_page()

    pdf.set_font(pdf.family, "B", 16)
    pdf.set_text_color(*INK)
    pdf.cell(0, 8, pdf.clean(f"Team Talk — {title}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(pdf.family, "", 9)
    pdf.set_text_color(*FAINT)
    exported = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(0, 5,
             pdf.clean(f"Created {session.get('created_at', 'unknown')}  ·  "
                       f"exported {exported}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    for raw in session.get("rounds", []):
        r = normalize_round(raw)

        marker = f"ROUND {r.get('round', '?')}"
        mode_title = mode_marker(r)
        if mode_title:
            marker += f"  ·  {mode_title}"
        if r.get("timestamp"):
            marker += f"  ·  {str(r['timestamp'])[:16].replace('T', ' ')}"
        pdf.ln(3)
        pdf.set_font(pdf.family, "B", 8)
        pdf.set_text_color(*FAINT)
        pdf.multi_cell(0, 4.5, pdf.clean(marker), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        chris_name = "SPLENDOR (FOR CHRIS)" if r.get("via_splendor") else "CHRIS"
        _speaker_block(pdf, chris_name, CHRIS_GOLD, r.get("chris_message", ""))
        att_names = ", ".join(a.get("name", "") for a in r.get("attachments", []))
        if att_names:
            pdf.set_font(pdf.family, "", 8.5)
            pdf.set_text_color(*FAINT)
            pdf.multi_cell(0, 4.5, pdf.clean(f"[attached: {att_names}]"),
                           new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)

        for resp in r.get("responses", []):
            # Blind rounds stay anonymous on paper too
            shown = resp.get("label") or resp.get("name", "AI")
            if resp.get("persona"):
                shown += f"  ({resp['persona']})"
            _speaker_block(pdf, shown.upper(), _hex_rgb(resp.get("color")),
                           resp.get("text", ""),
                           tokens=resp.get("tokens"))


def _speaker_block(pdf: _SessionPDF, name: str, rgb, text: str, tokens=None):
    pdf.set_font(pdf.family, "B", 9.5)
    pdf.set_text_color(*rgb)
    pdf.multi_cell(0, 5, pdf.clean(name), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(pdf.family, "", 10)
    pdf.set_text_color(*INK)
    pdf.multi_cell(0, 5, pdf.clean(text), new_x="LMARGIN", new_y="NEXT")
    if tokens:
        pdf.set_font(pdf.family, "", 7.5)
        pdf.set_text_color(*FAINT)
        pdf.cell(0, 4, pdf.clean(f"tokens: {tokens}"),
                 new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
