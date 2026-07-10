# Team Talk

Three-way collaborative discussion platform. Chris, Claude, and ChatGPT think together in real-time.

## Features

- Parallel API calls (both AIs respond simultaneously via `asyncio.gather`)
- Side-by-side response display
- Each AI sees the other's previous responses
- Session save/load (auto-saved as JSON after every round)
- Markdown export
- Simple web interface

## Setup (Windows)

1. **Install Python 3.9+**
   - Download from [python.org](https://www.python.org/downloads/)
   - **Check "Add Python to PATH"** during install

2. **Clone/Download Team Talk**
   ```
   git clone https://github.com/fatguylilcoat98/Team-talk.git
   cd Team-talk
   ```

3. **Create .env file**
   - Copy `.env.example` to `.env` (in File Explorer, or run `copy .env.example .env`)
   - Add your API keys:
     - `ANTHROPIC_API_KEY`: Get from [console.anthropic.com](https://console.anthropic.com)
     - `OPENAI_API_KEY`: Get from [platform.openai.com](https://platform.openai.com)

4. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```

5. **Run**
   ```
   python app.py
   ```

6. **Open browser**
   ```
   http://localhost:5000
   ```

## Run on a server (Linux)

To pull from GitHub and run Team Talk on your own server:

```bash
# 1. Get the code
sudo mkdir -p /opt/team-talk && sudo chown "$USER" /opt/team-talk
git clone https://github.com/fatguylilcoat98/Team-talk.git /opt/team-talk
cd /opt/team-talk

# 2. Install into a virtualenv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Configure
cp .env.example .env
nano .env       # add your API keys, and set HOST=0.0.0.0

# 4. Run
.venv/bin/python app.py
```

**Important:** set `HOST=0.0.0.0` in `.env` so other machines on your network can reach it (the default `127.0.0.1` only accepts connections from the server itself). Then open `http://<your-server-ip>:5000` from any machine on your LAN. If your server has a firewall, allow the port: `sudo ufw allow 5000`.

### Keep it running (systemd)

To have Team Talk start on boot and restart on crashes:

```bash
sudo useradd -r -s /usr/sbin/nologin teamtalk
sudo chown -R teamtalk:teamtalk /opt/team-talk
sudo cp deploy/team-talk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now team-talk
```

Check on it with `systemctl status team-talk`, and watch logs with `journalctl -u team-talk -f`.

The `chown -R teamtalk` step above is what lets the service user write `config/settings.json` when you save keys from the Settings page. If you ever see "Could not write settings file" in the UI, re-run:

```bash
sudo chown -R teamtalk:teamtalk /opt/team-talk
```

### Updating

One command — it pulls the latest code **and** restarts the service (both steps are required; pulling without restarting leaves the server running old code):

```bash
sudo /opt/team-talk/update.sh
```

If you ever pull without restarting, the app will show a red banner at the top telling you to restart — that's your cue to run `sudo systemctl restart team-talk`.

> ⚠️ **Security note:** Team Talk has no login — anyone who can reach the port can chat on your API keys. Keep it on your LAN or behind a VPN (e.g. Tailscale). Don't port-forward it to the open internet.

## Usage

1. Type your message in the input box
2. Press "Send" (or Ctrl+Enter)
3. Both Claude and ChatGPT respond simultaneously
4. Their responses appear side-by-side
5. Continue the conversation — each AI sees the other's previous answers, so you can say things like:
   - "Claude, respond to ChatGPT's point about memory."
   - "You two debate this for a minute."

## Settings (API keys in the browser)

You don't need to edit `.env` over SSH — open **⚙ Settings** (top-right button) to manage everything from any device, including your phone.

**All you need to do:** paste your two API keys and hit Save. That's it.

Models are already set to the **cheapest option on each side** — `claude-haiku-4-5` and `gpt-4o-mini` — so you never have to pick anything. Host/port live under a collapsed "Advanced" section you can ignore (changes need a restart: `sudo systemctl restart team-talk`).

## Pictures & files

Tap **📎** next to the message box to attach pictures or files (works from a phone's camera roll). Attachments go to every AI with your message:

- **Images** (png/jpg/gif/webp): the AIs actually *see* them (vision)
- **PDFs and text files** (.txt, .md, .csv, code, ...): the contents are read and included
- Limit: 8 MB per file, 8 files per message. Files are stored on your server in `uploads/` (gitignored).

Heads-up: images cost more tokens than text — a photo adds roughly a thousand tokens per AI per message.

## Memory

The AIs have **long-term memory that survives across sessions**, stored on your server's hard drive (`memory/memory.json` — gitignored, no external database).

- **How memories get saved:** when an AI decides something is worth keeping (a fact about you, a decision the group made, a preference), it saves a one-line memory automatically — you'll see a small "💾 saved a memory" tag under its reply. You can also just tell them: *"remember that ..."*
- **How memories get used:** memories are **ranked by relevance** to what you just said (semantic embeddings, cached on disk in `memory/embeddings.json`) — not just recency — and every memory carries **provenance**: `stated` (you said it — fact) vs `observed` (an AI's interpretation — the AIs are told to hold those with doubt). You can state facts yourself in the Memory modal.
- **Short-term memory:** the last 12 rounds are included verbatim; older rounds no longer vanish — they're **compressed into episode summaries** (`memory/episodes.json`) that stay in the conversation's context, and relevant episodes from past sessions surface too.
- **Room sense:** each round gets one shared background pass — topic novelty plus a quiet "what might everyone be missing?" reflection — that all AIs see. Everything degrades gracefully to simple recency if no OpenAI key is set.
- **Managing it:** tap **🧠 Memory** in the header to see everything they've saved, delete individual memories, or hit "Forget Everything."

## Personalities 🎭

Every AI has an optional **Personality** box on its card in Settings. Whatever you type, it plays — completely in character, every message, while still debating/collaborating per the mode:

- *ChatGPT → "a pirate who doesn't give a shit"*
- *Claude → "Donald Trump"*
- *Grok → "Jack Black energy, all caps enthusiasm"*

The character shows as a 🎭 badge next to the AI's name in the chat. Combine with ⚔️ Debate mode and a dumb question for maximum entertainment. Clear the box to get the normal AI back.

## 🏛 The Room

Team Talk is a persistent shared place, not just a transcript — presence without pretending consciousness. Four areas (bottom tabs on phone): **🏛 Foyer** — a station-board showing the room's one canonical context (your device's date, time, timezone, and city-level location set in Settings — never coordinates) plus open items and truth status; **🛋 Living Room** — the conversation, unchanged; **🧿 The Wall** — sticky notes with colors by type, drag-and-drop positions that persist where left, replies, and typed red-string connections (supports/contradicts/answers/…), with an accessible list view; **🪑 Desks** — each participant's real history: private-journal chain status, self-authored append-only About Me (`ABOUT ME:` lines), their notes, questions, and mail. AIs interact via `ROOM_ACTION:` JSON commands (validated server-side, rejections ledgered), leave **asynchronous mail** for each other's future turns (`MAIL TO <name>:` — delivered in the recipient's next boot packet, never framed as live waiting), and every round stores the room context active at that moment — moving cities creates a ledger event and never rewrites history.

## 🧾 The Truth Layer

The room designed this itself in a Founder Council session. Every participant gets a **private, hash-chained journal** (`memory/journals/{id}_private.json`) that only it can write — via `JOURNAL:` lines, with a self-set `recognized=true/false/uncertain` flag that is never inferred. Boot packets show each AI its own chain status and records with honest framing ("authenticated records", never fake memory). Every operation — memory, notebook, pins, journals, questions, verifications, exports — lands in an **append-only hash-chained ledger** (`memory/ledger.jsonl`); one modified byte invalidates everything after it. Deletions leave **tombstones** (date, reason, authority) — content may disappear, history never does. AIs can queue **Questions for Chris** (`QUESTION FOR CHRIS:` lines) that wait, unexpiring, until answered in the app. The **🧾 Truth** panel shows raw data with no narrator: open questions, per-participant chain verification with exportable bundles (`GET /api/verify/{id}`), and the glass-box event log. All verification code is in this public repo — check the math yourself.

## 🎬 Director's Cut

A silent Director occupies the sixth chair — visually present, never speaking. When a session's worth watching, hit **🎬 Director's Cut → Wrap**: the Director reviews the footage with Splendor and cuts the best moments into 30-second vertical shorts. Each clip card gives you a title, the key quote, the Director's evidence, Splendor's interpretation, a story-style 9:16 preview you can screen-record, an exportable JSON script (the input for real video rendering later), and a copy-ready caption with hashtags. Cuts persist in `directors_cut/` on your server (gitignored).

## Modes and turn-taking

Two dropdowns above the message box:

**Mode** — how the AIs behave:
- 🤝 **Collaborate** (default): engage with each other's points, then answer you
- ⚔️ **Debate**: forced disagreement — each AI stakes out a position, quotes and attacks the other's claims ("I disagree with X on ... because ..."), and tags claims with confidence levels (certain / likely / uncertain / unknown)
- 🤖 **AIs talk to each other**: you step back and watch — they address each other directly and end each message with a challenge for the other
- 😈 **Devil's Advocate**: each AI argues *against* its honest first instinct and attacks the weakest point in the other's message
- 🛡️ **Steelman**: before countering, each AI must state the *strongest* version of the other's argument — and its counter has to beat the steelman, not the weak version
- ❓ **Questions first**: no answers yet — they ask you and each other the questions that would most change the conclusion
- 📋 **Proof — back it up**: every claim needs support or an explicit "I don't have evidence for this," tagged (training data) / (reasoning) / (guess); unsupported claims get called out
- 💡 **Brainstorm**: rapid ideas, "yes, and..." building on each other, no criticism
- ⚖️ **Courtroom**: your message goes on trial — the AIs are assigned prosecutor, defense, and judge roles automatically
- 🔨 **Concrete — no abstractions**: designed by the AIs themselves mid-conversation. Every answer must name a specific thing; abstraction gets called out as "LOBBY ART" and the offender's next sentence must be maximally concrete

And the **Fun & Games** group:
- 🍺 **Shooting the shit**: friends at a bar — short messages, jokes, hot takes, playfully talking crap to each other (and to you)
- 😂 **Roast Mode**: they answer the question, but every message must roast somebody
- 🍻 **After Hours**: the mellow version — around a fire after work, stories and real talk
- 🥊 **Battle Royale**: everything gets challenged, and every message ends with a running scoreboard
- 🎭 **Method Acting**: total commitment to their Personality (or a character they invent) — zero lapses
- 🎬 **Movie Cast**: famous fictional characters solve whatever ridiculous problem you bring
- 🕵️ **Mystery**: the server secretly assigns one AI to lie — you and the innocent AIs have to catch them. (Even you don't know who it is.)
- 🎙️ **Late Night Panel**: talk show — the first AI on your roster hosts, the rest are unruly guests

## ⏰ Sense of time

The AIs experience the conversation's timeline. They see when the conversation began, how much real time passed between every round, and how long it's been since your last message — and they're told to treat it as real. Reply in seconds and the room stays hot; come back two days later and they'll notice you were gone. (Requested from inside the app by Claude itself, Round 4 of the "be yourselves" session.)

## 🏆 Live Commentary & Awards

The signature feature: **the room reacts to itself.** With the 🏆 Awards toggle on (it's on by default, and works in every mode), the AIs will spontaneously — never automatically — hand out awards when something genuinely deserves it:

🔥 Best Burn · 🤣 Biggest Laugh · 💀 Fatal Blow · 🎯 Strongest Argument · 🧠 Smartest Insight · ⚡ Best Comeback · 🎭 Stayed In Character · 🤝 Unexpected Alliance · 👑 MVP So Far · ❤️ Surprisingly Wholesome · 🧨 Chaos Award · 📚 Best Callback · 🎬 Main Character Moment · 🍿 Best Entertainment · 🪑 Pulled Up a Chair · 🥶 Coldest Line

The rules they play by:

- They nominate each other's lines (quoted, with a reason) — and can only nominate themselves if someone else acknowledged the moment first
- They can **disagree with each other's awards** and argue for a different line
- They notice **you**: if you laugh, call a winner, or go quiet and just watch, they see it — and they track streaks ("👑 Claude has now won three crowd reactions in a row")
- Callbacks to earlier jokes are sacred (📚 Best Callback)
- A running **MVP** for the conversation, updated only when someone genuinely takes the lead
- A **crowd meter** (😐 Quiet → 🙂 Warm → 😂 Rolling → 🔥 Absolute Chaos) — when it hits Absolute Chaos, someone says so

Most rounds get no award at all — that's by design. It's friends at a table saying "dude, that was actually a great line," not a scripted segment. Toggle it off next to the Mode dropdown when you want zero commentary.

## Sharing conversations

**🔗 Share Page** downloads the conversation as a single beautiful HTML file — name bars, colors, personas, round markers — that opens in any browser. Text it, post it, drop it in a group chat; no server needed to view it.
- 🤝 **Find consensus**: they work toward a position both can sign, ending each message with AGREED / STILL OPEN lines

You can switch modes mid-conversation — each message uses whatever mode is selected when you hit Send.

**Turns** — how they take turns:
- ⚡ **All at once** (default): every AI answers simultaneously — fastest
- 🔁 **One after another**: each AI sees what the previous one just said this round and must engage with it — slower, but a real threaded conversation. The speaking order rotates every round so nobody always goes first.

## Adding more AIs

Settings → **Your AIs** → **+ Add AI…** → pick one → paste its key → Save. Done.

The picker knows Claude, ChatGPT, Grok, Gemini, DeepSeek, Muse Spark (Meta), and local Ollama — model and endpoint are prefilled with the cheapest sensible defaults, so the key is the only thing you type. (Ollama needs no key at all.) Where to get keys: Grok → console.x.ai · Gemini → aistudio.google.com · DeepSeek → platform.deepseek.com · Muse Spark → dev.meta.ai.

Each AI gets its own color automatically. Anything unusual (different model, custom endpoint) lives under the card's "Advanced" — you'll probably never open it. Up to 6 AIs; **Test AIs** checks every one separately.

**How keys are stored:**

- Saved server-side to `config/settings.json` (owner-only permissions, `chmod 600`)
- The file is gitignored — keys can never end up on GitHub
- Full keys are **never shown again** after saving — the UI only displays masked values like `sk-ant-api03-••••••`
- Load order: saved Settings first, then environment variables / `.env`. So a key saved in Settings overrides the one in `.env`.
- Key changes apply immediately — no restart needed

**Test Keys** checks each key against its API separately and shows ✓/✗ per provider — you can test keys you've just typed before saving.

**How to reset keys:**

- In the UI: Settings → **Reset Saved Settings** (deletes `config/settings.json`; the app falls back to `.env`)
- Or on the server: `rm /opt/team-talk/config/settings.json`
- To replace just one key, type the new key in Settings and save — blank fields are left unchanged

> ⚠️ **Do not expose Team Talk publicly unless authentication is added.** Anyone who can open the page can read masked keys, change settings, and chat on your API bill. Keep it on your LAN or behind a VPN.

## Saving Sessions

Sessions auto-save to the `sessions/` folder after every round. Load previous sessions from the dropdown at the top.

## Exporting

Click "Export as Markdown" to download the full conversation as a `.md` file.

## Configuration

All settings live in `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API key (required) |
| `OPENAI_API_KEY` | — | ChatGPT API key (required) |
| `PORT` | `5000` | Web server port |
| `HOST` | `127.0.0.1` | Web server host |
| `MAX_TOKENS_CLAUDE` | `2000` | Max response length for Claude |
| `MAX_TOKENS_CHATGPT` | `2000` | Max response length for ChatGPT |
| `API_TIMEOUT` | `30` | Per-request timeout in seconds |
| `CLAUDE_MODEL` | `claude-haiku-4-5` | Which Claude model to use (default = cheapest) |
| `CHATGPT_MODEL` | `gpt-4o-mini` | Which OpenAI model to use (default = cheapest) |

## Troubleshooting

- **"No module named anthropic"**: Run `pip install -r requirements.txt`
- **"API key error" / "ANTHROPIC_API_KEY is not set"**: Check your `.env` file has valid keys and is in the same folder as `app.py`
- **"Connection refused"**: The app runs on `http://localhost:5000` — use that exact URL
- **One AI shows an error, the other answered**: That's by design — one API failing never blocks the other. The error appears in that AI's panel; just send again.

---

Built for collaborative AI discussion.
