"""Security utilities — scrub sensitive env vars from tmux sessions."""

from __future__ import annotations

import asyncio
import logging

import libtmux

logger = logging.getLogger(__name__)

# Sensitive environment variables to remove from tmux sessions.
# Prevents coding CLIs running inside tmux from reading bot credentials.
SENSITIVE_ENV_VARS: tuple[str, ...] = (
    "DISCORD_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_API_KEY",
)


async def scrub_env(session: libtmux.Session) -> None:
    """Remove sensitive environment variables from a tmux session."""
    await asyncio.to_thread(_scrub_sync, session)


def _scrub_sync(session: libtmux.Session) -> None:
    for var in SENSITIVE_ENV_VARS:
        try:
            session.set_environment(var, "")
            session.remove_environment(var)
        except libtmux.exc.LibTmuxException:
            # Variable was not set — nothing to remove.
            pass
        except Exception:
            logger.debug("Failed to scrub env var %s", var, exc_info=True)
