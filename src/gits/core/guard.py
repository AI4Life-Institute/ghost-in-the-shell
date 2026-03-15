"""Guard mechanism — inject context into ops Coding Agent session, await decision."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GUARD_TIMEOUT = 600  # 10 minutes
GUARD_POLL_INTERVAL = 5  # seconds
DEFAULT_OPS_SESSION = "ghost-ops"


class GuardHandler:
    """Injects Guard prompts into a Coding Agent tmux session.

    Reuses existing engine.tmux to send text and capture output.
    The ops session is a standalone tmux session (NOT managed by engine's session_mgr).
    """

    def __init__(
        self,
        tmux: Any,           # TmuxController
        ops_session: str = DEFAULT_OPS_SESSION,
    ) -> None:
        self._tmux = tmux
        self.ops_session = ops_session

    async def trigger_guard(
        self,
        skill_name: str,
        run_id: str,
        failed_step: str,
        tool_description: str,
        log_tail: str,
        skill_description: str = "",
        guard_session: str | None = None,
    ) -> str:
        """Inject guard prompt into ops session, wait for GUARD_ACTION decision.

        Returns: "retry" | "skip" | "abort" | "fixed"
        """
        session = guard_session or self.ops_session

        # Ensure the ops session exists
        await asyncio.to_thread(_ensure_ops_session, session)

        # Get the window ID for the ops session
        window_id = await asyncio.to_thread(_get_session_window, session)
        if not window_id:
            logger.error("Guard: could not find window for session %s", session)
            return "abort"

        # Format the guard prompt
        prompt = _format_guard_prompt(
            skill_name=skill_name,
            run_id=run_id,
            failed_step=failed_step,
            tool_description=tool_description,
            log_tail=log_tail,
            skill_description=skill_description,
        )

        # Inject into the ops session
        logger.info("Guard: injecting prompt into session %s", session)
        try:
            await self._tmux.send_text(window_id, prompt, submit_keys="\n")
        except Exception:
            logger.exception("Guard: failed to inject prompt")
            return "abort"

        # Wait for the session to return to idle
        action = await self._wait_for_decision(window_id)
        logger.info("Guard decision for %s/%s: %s", skill_name, run_id, action)
        return action

    async def _wait_for_decision(self, window_id: str) -> str:
        """Poll session until idle, then extract GUARD_ACTION from output."""
        from .terminal_parser import parse_status_line

        deadline = time.time() + GUARD_TIMEOUT
        last_text = ""

        while time.time() < deadline:
            await asyncio.sleep(GUARD_POLL_INTERVAL)
            try:
                text = await self._tmux.capture_pane_text(window_id)
            except Exception:
                continue

            status = parse_status_line(text) or "idle"
            last_text = text

            if status == "idle":
                # Session is idle — extract decision
                action = _extract_guard_action(text)
                return action

        # Timeout — default to abort
        logger.warning("Guard timed out after %ds, defaulting to abort", GUARD_TIMEOUT)
        return "abort"

    async def ensure_ops_session(self, coding_cli: str = "claude") -> None:
        """Create the ops tmux session if it doesn't exist."""
        await asyncio.to_thread(_ensure_ops_session, self.ops_session, coding_cli)


def _format_guard_prompt(
    skill_name: str,
    run_id: str,
    failed_step: str,
    tool_description: str,
    log_tail: str,
    skill_description: str = "",
) -> str:
    """Format a Guard prompt for the Coding Agent."""
    parts = [
        f"GUARD REQUEST — Skill: {skill_name} | Run: {run_id}",
        "",
    ]
    if skill_description:
        parts += [f"Skill description: {skill_description}", ""]

    parts += [
        f"Failed step: {failed_step}",
        "",
    ]
    if tool_description:
        parts += [
            "Tool definition:",
            tool_description,
            "",
        ]

    parts += [
        "Last 50 lines of run log:",
        "```",
        log_tail,
        "```",
        "",
        "Please review the failure above and respond with one of:",
        "  GUARD_ACTION: retry    (transient error, retry the step)",
        "  GUARD_ACTION: skip     (ignore this step, continue skill)",
        "  GUARD_ACTION: abort    (stop the skill run)",
        "  GUARD_ACTION: fixed    (you fixed the underlying issue, continue)",
    ]
    return "\n".join(parts)


def _extract_guard_action(text: str) -> str:
    """Parse GUARD_ACTION from session output. Default: abort."""
    m = re.search(r'GUARD_ACTION:\s*(retry|skip|abort|fixed)', text, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return "abort"


def _get_session_window(session_name: str) -> str | None:
    """Get the first window ID for a tmux session (format: 'session:@id')."""
    result = subprocess.run(
        ["tmux", "list-windows", "-t", session_name, "-F", "#{window_id}"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        window_id = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
        if window_id:
            return f"{session_name}:{window_id}"
    return None


def _ensure_ops_session(session_name: str, coding_cli: str = "claude") -> None:
    """Create ops tmux session if it doesn't exist, optionally start coding CLI."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name],
            check=True, capture_output=True,
        )
        logger.info("Created ops session: %s", session_name)
        # Don't auto-start coding CLI — let the user open it
