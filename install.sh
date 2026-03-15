#!/usr/bin/env bash
set -euo pipefail

REPO="AI4Life-Institute/ghost-in-the-shell"
PACKAGE="ghost-in-the-shell"

info()  { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
die()   { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. tmux ────────────────────────────────────────────────────────────────────
if ! command -v tmux &>/dev/null; then
  info "Installing tmux..."
  if command -v brew &>/dev/null; then
    brew install tmux
  elif command -v apt-get &>/dev/null; then
    sudo apt-get install -y tmux
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y tmux
  else
    die "tmux not found — install it manually: https://github.com/tmux/tmux"
  fi
fi
ok "tmux $(tmux -V | cut -d' ' -f2)"

# ── 2. uv ──────────────────────────────────────────────────────────────────────
if ! command -v uv &>/dev/null; then
  info "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # add to PATH for the rest of this script
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv &>/dev/null || die "uv install failed — try manually: https://docs.astral.sh/uv/"
ok "uv $(uv --version | cut -d' ' -f2)"

# ── 3. ghost ───────────────────────────────────────────────────────────────────
info "Installing ghost..."
uv tool install "git+https://github.com/${REPO}.git"

# ensure uv tool bin dir is on PATH
UV_TOOL_BIN="$(uv tool dir)/../../bin"
UV_TOOL_BIN="$(uv tool bin-dir 2>/dev/null || echo "$HOME/.local/bin")"
if ! command -v ghost &>/dev/null; then
  export PATH="$UV_TOOL_BIN:$PATH"
fi

ok "ghost installed → $(command -v ghost)"

# ── 4. next steps ──────────────────────────────────────────────────────────────
cat <<'EOF'

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Ghost in the Shell — installed!
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Next: create a .env file with your Discord bot token:

  GITS_DISCORD_TOKEN=your-bot-token
  ALLOWED_GUILDS=["your-server-id"]

Then start:

  ghost start

Docs: https://github.com/AI4Life-Institute/ghost-in-the-shell
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EOF
