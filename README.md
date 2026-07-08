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

## Usage

1. Type your message in the input box
2. Press "Send" (or Ctrl+Enter)
3. Both Claude and ChatGPT respond simultaneously
4. Their responses appear side-by-side
5. Continue the conversation — each AI sees the other's previous answers, so you can say things like:
   - "Claude, respond to ChatGPT's point about memory."
   - "You two debate this for a minute."

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
