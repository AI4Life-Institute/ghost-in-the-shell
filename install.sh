#!/usr/bin/env bash
set -euo pipefail

REPO="AI4Life-Institute/ghost-in-the-shell"
BRANCH="master"
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

# ── 3b. install bundled Claude Code skills (G-4 irzsbr) ────────────────────────
# Honors GHOST_NO_INSTALL_SKILLS=1 opt-out. Non-fatal: a failure here
# (e.g. ~/.claude/ permission issue) shouldn't block the rest of install.
if [ "${GHOST_NO_INSTALL_SKILLS:-}" = "1" ]; then
  info "skipping skills install (GHOST_NO_INSTALL_SKILLS=1)"
else
  info "Installing bundled Claude Code skills..."
  if ghost install-skills 2>&1; then
    ok "skills installed → ~/.claude/skills/"
  else
    printf '\033[1;33m! skills install failed (non-fatal). Re-run later with: ghost install-skills\033[0m\n'
  fi
fi

# ── 4. shell rc — ensure ghost is on PATH permanently ──────────────────────────
_add_to_path() {
  local dir="$1" rc="$2"
  if [ -f "$rc" ] && ! grep -q "$dir" "$rc" 2>/dev/null; then
    printf '\n# Ghost / uv tools\nexport PATH="%s:$PATH"\n' "$dir" >> "$rc"
  fi
}
_UV_BIN="$(uv tool bin-dir 2>/dev/null || echo "$HOME/.local/bin")"
_add_to_path "$_UV_BIN" "$HOME/.zshrc"
_add_to_path "$_UV_BIN" "$HOME/.bashrc"

# ── 5. launch setup wizard ─────────────────────────────────────────────────────
printf '\033[1;32m\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
printf '  Ghost installed ✓  Starting setup…\n'
printf '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m\n\n'

exec "$_UV_BIN/ghost"
