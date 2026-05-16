# GITS CLI Helper

You are helping the user run, debug, and operate the GITS project from the command line.

## Project context

- **Runtime**: `uv run` (never `python` directly — always `uv run python` or `uv run pytest`)
- **Entry point**: `uv run gits` or `uv run python -m gits`
- **Tests**: `uv run pytest tests/ -v`
- **Config**: env vars in `.env` or `~/.gits/config.toml`
- **Database**: SQLite at `~/.gits/gits.db` — query with `sqlite3` or Python `sqlite3` module
- **Browser agent**: `openclaw` CLI — controls Chrome via WebSocket extension
- **Terminal sessions**: `tmux` — each workspace runs in a named tmux session

## What this skill does

When the user runs `/cli [optional description]`, figure out what they need and do it. Common cases:

### Start / stop / status

```bash
# Start a workspace
uv run gits start --workspace myproject --ai claude

# Check running sessions
tmux ls

# Stop a workspace
uv run gits stop --workspace myproject

# Full status
uv run gits status
```

### Query the database

Probe the schema first, then answer:

```bash
sqlite3 ~/.gits/gits.db ".schema"
sqlite3 ~/.gits/gits.db "SELECT * FROM tasks ORDER BY created_at DESC LIMIT 20;"
sqlite3 ~/.gits/gits.db "SELECT * FROM artifacts WHERE task_id = 'X';"
sqlite3 ~/.gits/gits.db "SELECT action, input, output FROM steps WHERE task_id = 'X' ORDER BY seq;"
```

If the user wants to export: `sqlite3 -csv ~/.gits/gits.db "SELECT ..." > out.csv`

### Browser agent tasks

```bash
# Check openclaw is working
openclaw doctor

# List Chrome profiles
openclaw browser list-profiles

# Start a browser session for a profile
openclaw browser --browser-profile myprofile start

# Snapshot current page (get interactive element refs)
openclaw browser --browser-profile myprofile snapshot --labels

# Navigate
openclaw browser --browser-profile myprofile navigate https://example.com

# Click an element by ref
openclaw browser --browser-profile myprofile click e42

# Type into a field
openclaw browser --browser-profile myprofile type e17 "search query"

# Run JavaScript and get result
openclaw browser --browser-profile myprofile evaluate --fn "localStorage.getItem('token')"
```

### Run tests

```bash
uv run pytest tests/ -v
uv run pytest tests/test_engine.py -v
uv run pytest -k "test_name" -v
```

### Logs and debugging

```bash
# Tail gits logs
tail -f ~/.gits/gits.log

# Check tmux session output
tmux attach -t gits-myproject
# (Ctrl+B then D to detach)

# Check sqlite integrity
sqlite3 ~/.gits/gits.db "PRAGMA integrity_check;"
```

## How to respond

1. **If the user gives a goal** (e.g. `/cli check what browser tasks ran today`): translate it to the right command(s), run them, and show a clean summary of the output. Don't dump raw output — interpret it.

2. **If the user gives a command to run** (e.g. `/cli run pytest`): run it, explain what happened, flag any errors.

3. **If something fails**: read the error carefully, diagnose the likely cause (missing dep? wrong env? port conflict? tmux not running?), and suggest a fix. Don't just re-run the same thing.

4. **For database queries**: show results as a formatted table in the terminal, not raw JSON. Use `column -t` or similar for alignment if many columns.

5. **Keep responses short**: show the command you ran, then the key result. Skip boilerplate output that doesn't add information.

## Platform support

Ghost supports two messaging platforms:

- **Discord** — slash commands (`/bind`, `/info`, `/bash`, etc.) in any channel or thread. Configure with `ghost discord setup` or set `GITS_DISCORD_TOKEN` in `~/.gits/config.env`.
- **WeChat** — plain-text commands (`/bind`, `/s`, `/bash`, etc.) via WeChat messages. Configure with `ghost wechat` (QR-code login via ilinkai). Both platforms can run simultaneously.

## Common issues

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ModuleNotFoundError` | Wrong Python env | Use `uv run`, not `python` |
| `tmux: no server running` | tmux not started | `tmux new -s gits-main` |
| `openclaw: command not found` | Not installed | Check `which openclaw`; install from bundle |
| `sqlite3: no such table` | DB not initialized | `uv run gits init-db` |
| Discord bot not connecting | Token missing/wrong | Check `~/.gits/config.env` for `GITS_DISCORD_TOKEN` |
| WeChat not connecting | Not logged in | Run `ghost wechat` to re-authenticate via QR code |
| WeChat QR code expired | Scan timeout (120s) | Run `ghost wechat --relogin` to get a new QR code |
| `address already in use` | Port conflict | `lsof -i :PORT` then kill the process |
