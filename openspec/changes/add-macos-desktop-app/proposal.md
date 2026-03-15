# Change: Add macOS Desktop App with Frosted Glass UI

## Why
GITS currently requires developer-level expertise to set up (Python 3.12, uv, tmux, env vars, Discord bot tokens). To reach non-technical users, we need a polished macOS desktop app with one-click install, guided setup, and a native frosted glass (vibrancy) UI — no terminal required for initial configuration.

## What Changes
- Add a Tauri v2 desktop app shell with macOS vibrancy/frosted glass effects
- Bundle Python backend via pytauri (PyO3 bridge, zero IPC overhead)
- Statically compile and embed tmux + libevent + ncurses in the app bundle
- Add a setup wizard UI for Discord bot token, AI provider login, and directory selection
- Replace manual `.env` configuration with a GUI settings panel
- Add `brew install --cask gits` and `.dmg` distribution
- Provide guided Discord bot creation flow with step-by-step instructions and OAuth link generation
- Support user's own Claude/ChatGPT API keys or OAuth login (no shared keys)
- Add auto-update mechanism via Tauri's built-in updater
- Add local direct chat interface — users can interact with coding CLIs directly through the app without Discord, using an elegant frosted glass conversation view with markdown rendering and syntax-highlighted code blocks

## Impact
- Affected specs: `terminal-ui-bridge` (MODIFIED — app now wraps tmux internally), `discord-interactions` (MODIFIED — setup flow changes)
- New specs: `desktop-app`, `setup-wizard`, `user-auth`, `local-chat`
- Affected code: `src/gits/__main__.py` (new desktop entry point), `src/gits/config.py` (GUI config source), build system (Tauri + Rust + pytauri)
- New directories: `src-tauri/` (Rust/Tauri shell), `ui/` (web frontend), `scripts/build-tmux.sh`
