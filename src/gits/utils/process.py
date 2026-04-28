"""Process management helpers — kill confirmation with SIGTERM/SIGKILL escalation."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from collections.abc import Iterable

logger = logging.getLogger(__name__)


def _is_alive(pid: int) -> bool:
    """Return True if the pid is still a running (non-zombie) process.

    If the caller is the parent of *pid*, this first attempts a non-blocking
    waitpid to reap zombies. In production ghost is rarely the parent of the
    claude processes it kills (they live under tmux pane shells), so the
    waitpid call typically raises ChildProcessError and we fall through to the
    kill(pid, 0) probe. In tests where the test runner is the parent, the
    waitpid call reaps the zombie correctly.
    """
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False  # we just reaped it; definitely dead
    except ChildProcessError:
        pass  # pid is not our child (production case)
    except OSError:
        pass

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def kill_claude_process(
    pids: Iterable[int],
    grace_seconds: float = 5.0,
    poll_interval: float = 0.1,
    reap_after_kill: float = 1.0,
) -> dict[int, bool]:
    """Kill the given pids: SIGTERM, poll until dead, SIGKILL after grace.

    Returns {pid: killed_ok}. ``killed_ok`` is True if the pid is no longer
    alive after the procedure (either it exited cleanly or the kernel reaped
    it). False means the pid is still alive even after SIGKILL + reap_after_kill
    seconds — the caller MUST treat this as a failure.

    Args:
        pids: Iterable of process IDs to kill.
        grace_seconds: How long to wait after SIGTERM before escalating to SIGKILL.
            For idle suspension use 2s; for credential-mutating switches use 5s.
        poll_interval: How frequently to recheck pid liveness during the grace window.
        reap_after_kill: How long to wait after SIGKILL for the kernel to reap the pid.
    """
    pid_list = [int(p) for p in pids if int(p) > 0]
    if not pid_list:
        return {}

    # SIGTERM all
    for pid in pid_list:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.debug("Sent SIGTERM to pid %d", pid)
        except ProcessLookupError:
            pass

    # Poll for graceful exit
    deadline = asyncio.get_event_loop().time() + grace_seconds
    while pid_list and asyncio.get_event_loop().time() < deadline:
        pid_list = [p for p in pid_list if _is_alive(p)]
        if not pid_list:
            break
        await asyncio.sleep(poll_interval)

    # SIGKILL stragglers
    if pid_list:
        for pid in pid_list:
            try:
                os.kill(pid, signal.SIGKILL)
                logger.warning(
                    "Sent SIGKILL to pid %d after %.1fs SIGTERM grace", pid, grace_seconds
                )
            except ProcessLookupError:
                pass
        await asyncio.sleep(reap_after_kill)

    # Final liveness check across the originally-requested set
    result: dict[int, bool] = {}
    for pid in [int(p) for p in pids if int(p) > 0]:
        result[pid] = not _is_alive(pid)
    return result


async def find_claude_children(pane_pid: int) -> list[int]:
    """Return pids of direct children of pane_pid (typically the claude process).

    Returns [] if pgrep fails or pane_pid has no children.
    """
    if pane_pid <= 0:
        return []
    try:
        out = await asyncio.to_thread(
            subprocess.check_output,
            ["pgrep", "-P", str(pane_pid)],
            text=True,
        )
    except subprocess.CalledProcessError:
        return []  # pgrep returns 1 when no matches
    except FileNotFoundError:
        logger.warning("pgrep not found on PATH")
        return []
    return [int(p) for p in out.split() if p.strip()]
