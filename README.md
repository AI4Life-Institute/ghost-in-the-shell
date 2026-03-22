<p align="center">
  <img src="docs/logo.png" width="200" alt="Ghost in the Shell">
</p>

<h1 align="center">Ghost in the Shell</h1>

<p align="center">
  Control AI coding agents on your machine — from Discord or WeChat, on any device.
</p>

---

Watch your AI write code, review its terminal output, and approve permission prompts — all from your phone via Discord or WeChat.

<p align="center">
  <img src="docs/demo.gif" width="300" alt="Ghost in the Shell demo">
</p>

---

## Key Features

- **Desktop ↔ mobile continuity** — start a session on your Mac, walk away, and pick it up on your phone; the AI keeps working while you switch devices
- **Remote control from anywhere** — bind a project directory to a Discord channel or WeChat chat; every message you type is forwarded to the coding CLI running on your machine
- **WeChat support** — no Discord account needed; control Ghost directly from WeChat using the same commands (`/bind`, `/bash`, `/s`, etc.)
- **Terminal screenshots** — `/screenshot` (Discord) or `/s` (WeChat) sends a live snapshot of the terminal directly to your phone
- **Interactive prompts as buttons** — when the CLI asks for permission, it becomes a Discord button you tap to approve or deny
- **Multi-CLI support** — works with Claude Code, Codex CLI, and OpenCode; switch between them per channel
- **Threads & isolated worktrees** — `/fork` spins up a sub-task thread backed by a fresh git worktree, keeping parallel work cleanly separated
- **Session resume** — reconnecting to a directory shows a session picker so you can continue where you left off
- **Cross-CLI session import** — when resuming, sessions from other CLIs appear in the picker (marked `↗[codex]`); selecting one extracts the full conversation to `.gits-import.md` and injects it into a fresh session of your target CLI, so you can pick up a Codex conversation in Claude Code (or vice versa) without losing context
- **tmux-backed sessions** — each project runs in a real tmux window; developers get full terminal access locally while Ghost handles the Discord bridge
- **Automatic memory management** — idle CLI processes are automatically suspended after inactivity (threshold adapts to available system RAM: 2 h normally, down to 10 min under memory pressure) and transparently resumed the moment a new message arrives, keeping memory usage in check without losing conversation history
- **Subscription-safe** — Ghost drives the official CLI tools (Claude Code, Codex) exactly as a human would; no API key required, no terms-of-service gray area — your existing Pro/Max subscription just works

## Why Ghost?

| | Ghost | OpenClaw | Native CLI |
|---|:---:|:---:|:---:|
| Remote control via Discord | ✅ | ✅ | ❌ |
| Remote control via WeChat | ✅ | ❌ | ❌ |
| Team collaboration | ✅ | ✅ | ❌ |
| Works with Pro/Max subscription — no API key needed | ✅ | ⚠️ API key required for stable use | ✅ |
| Account-safe — no ToS gray area | ✅ | ⚠️ Documented account suspensions | ✅ |
| Real local terminal (tmux) | ✅ | ❌ | ✅ |
| Cross-CLI session import (e.g. Codex → Claude) | ✅ | ❌ | ❌ |

---

## Quick Start

**1. Install**

```bash
curl -fsSL https://raw.githubusercontent.com/AI4Life-Institute/ghost-in-the-shell/master/install.sh | bash
```

Or via Homebrew:

```bash
brew install ai4life/tap/ghost
```

> The install script handles uv and tmux automatically. Python >= 3.12 is required.

**2. Install a coding CLI (at least one)**

| CLI | Install |
|---|---|
| Claude Code | `npm i -g @anthropic-ai/claude-code` |
| Codex | `npm i -g @openai/codex` |
| OpenCode | `curl -fsSL opencode.ai/install \| bash` |

**3. Configure a platform (Discord or WeChat — or both)**

**Option A — Discord**

Run the setup wizard:

```bash
ghost discord
```

Or set manually in `~/.gits/config.env`:

```bash
GITS_DISCORD_TOKEN=your-bot-token
ALLOWED_GUILDS=["your-server-id"]
```

The bot needs **Message Content Intent** enabled and must be invited with permissions to send messages and create threads.

**Option B — WeChat**

Run the setup wizard and scan the QR code with WeChat:

```bash
ghost wechat
```

This logs in via QR code (no API key, no third-party account — uses your existing WeChat app). Your account credentials are saved locally at `~/.openclaw/openclaw-weixin/`. You can optionally set a default project path to auto-bind when you first message Ghost:

```bash
ghost wechat --path /path/to/your/project
```

To re-authenticate later:

```bash
ghost wechat --relogin
```

Both platforms can run simultaneously — Ghost routes messages from each independently.

**4. Start**

```bash
ghost start
```

Hooks are auto-installed on first start. For dev mode with auto-restart on file changes:

```bash
ghost start --dev
```

---

## Usage

### Discord workflow

1. Create a Discord channel (e.g. `#my-feature`)
2. `/bind /path/to/project` — launches the CLI in a tmux window
3. Type in the channel — messages are forwarded to the CLI
4. CLI responses stream back to Discord automatically
5. Permission prompts appear as buttons — tap to approve or deny
6. `/screenshot` to see the terminal at any time
7. `/done` when done

### WeChat workflow

1. Run `ghost wechat --path /path/to/project` to set a default project (one-time setup)
2. Send Ghost any message on WeChat — it auto-binds to your default project on first contact
3. Type naturally — messages are forwarded to the CLI running on your Mac
4. Send `/s` at any time to get a terminal screenshot
5. Send `/bind /other/path` to switch to a different project
6. Send `/help` to see all available commands

No need to keep a desktop open — Ghost runs as a background service via launchd and responds whenever you message it.

### Discord commands

| Command | Description |
|---|---|
| `/bind <path> [mode] [cli]` | Bind channel to a project, launch CLI. `mode`: `default` (confirm) or `bypassPermissions` (YOLO) |
| `/unbind` | Unbind and close the window |
| `/info` | Show binding info, session file path, and imported context file |
| `/screenshot` | Send a terminal screenshot |
| `/esc` | Send Escape key (interrupt current operation) |
| `/done` | Close window and archive thread |
| `/new [message]` | Reset CLI session |
| `/bash <command>` | Run a shell command in the project directory |
| `/keys <keys>` | Send keystrokes (Enter, Escape, Ctrl-C, Up, Down…) |
| `/model [name]` | Switch model (sonnet, opus, haiku, o3, gpt-4o…) |
| `/mode <mode>` | Switch permission mode without restarting. Supports all four: `default`, `bypassPermissions`, `auto`, `acceptEdits` |
| `/thread <message>` | Create a sub-thread sharing the same directory |
| `/fork <title>` | Create a sub-thread with an isolated git worktree |
| `/browse <goal>` | Run a browser agent task |
| `/compact` `/clear` `/cost` `/diff` `/memory` `/context` `/usage` | Forwarded directly to the CLI |
| `/cc <command>` | Forward any slash command to the CLI |

### WeChat commands

Send these as plain-text messages to Ghost on WeChat:

| Command | Description |
|---|---|
| `/bind <path>` | Bind to a project directory and launch the CLI |
| `/s` | Terminal screenshot |
| `/i` | Show binding status |
| `/e` | Send Enter key |
| `/x` | Send Escape key |
| `/keys <keys>` | Send a key sequence |
| `/bash <command>` | Run a shell command |
| `/new` | Reset CLI session |
| `/done` | End session |
| `/model <name>` | Switch model |
| `/help` | Show all commands |
| (plain text) | Forwarded directly to the terminal |

---

## Docs

See [docs/architecture.md](docs/architecture.md) for architecture, internals, and configuration reference.

---

## License

AI4Life Community License — free for individuals and organizations with annual revenue under $1M. Commercial use above that threshold requires a separate license — contact admins@ai4life.com. © 2026 [AI4Life Institute](https://github.com/AI4Life-Institute)
