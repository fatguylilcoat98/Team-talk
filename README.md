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

```bash
cd /opt/team-talk
sudo -u teamtalk git pull
sudo systemctl restart team-talk
```

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

**What you can set:**

- Anthropic API Key and OpenAI API Key
- Claude Model and ChatGPT Model
- Host and Port (take effect after a restart: `sudo systemctl restart team-talk`)

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
| `CLAUDE_MODEL` | `claude-opus-4-8` | Which Claude model to use |
| `CHATGPT_MODEL` | `gpt-4-turbo` | Which OpenAI model to use |

## Troubleshooting

- **"No module named anthropic"**: Run `pip install -r requirements.txt`
- **"API key error" / "ANTHROPIC_API_KEY is not set"**: Check your `.env` file has valid keys and is in the same folder as `app.py`
- **"Connection refused"**: The app runs on `http://localhost:5000` — use that exact URL
- **One AI shows an error, the other answered**: That's by design — one API failing never blocks the other. The error appears in that AI's panel; just send again.

---

Built for collaborative AI discussion.
