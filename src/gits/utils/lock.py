"""Async-safe file mutex via fcntl.flock.

Used by the subscription credential layer to serialize every operation that
mutates ``~/.claude/.credentials.json`` across the ghost daemon and any
concurrent ``gits`` CLI invocations.

Crash safety: ``fcntl.flock`` locks are released by the kernel automatically
when the holding process exits, so a crash during a credential mutation cannot
deadlock subsequent ghost starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

logger = logging.getLogger(__name__)


class CredentialLockTimeout(RuntimeError):
    """Raised when the credential lock cannot be acquired within the timeout."""


@contextlib.asynccontextmanager
async def credential_lock(
    lock_path: Path,
    timeout: float | None = None,
    poll_interval: float = 0.1,
) -> AsyncIterator[None]:
    """Hold an exclusive ``fcntl.flock`` on *lock_path* for the body's duration.

    The lock file is created if missing. Concurrent acquirers block (or raise
    ``CredentialLockTimeout`` if *timeout* is set and exceeded).

    Args:
        lock_path: Path to the lock file (e.g., ``~/.gits/.switch.lock``).
        timeout: Max seconds to wait for the lock. ``None`` means wait forever.
        poll_interval: How often to retry while waiting (we use non-blocking
            ``LOCK_NB`` so the asyncio event loop stays responsive).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = await asyncio.to_thread(os.open, str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        await _acquire(fd, timeout, poll_interval, lock_path)
        try:
            yield
        finally:
            await asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_UN)
    finally:
        await asyncio.to_thread(os.close, fd)


async def _acquire(
    fd: int,
    timeout: float | None,
    poll_interval: float,
    lock_path: Path,
) -> None:
    deadline: float | None
    if timeout is None:
        deadline = None
    else:
        deadline = asyncio.get_event_loop().time() + timeout

    waited = False
    while True:
        try:
            await asyncio.to_thread(fcntl.flock, fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if waited:
                logger.info("Acquired credential lock %s after wait", lock_path)
            return
        except BlockingIOError:
            if not waited:
                logger.info("Waiting for credential lock %s...", lock_path)
                waited = True
            if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                raise CredentialLockTimeout(
                    f"Could not acquire {lock_path} within {timeout}s"
                ) from None
            await asyncio.sleep(poll_interval)


def is_locked(lock_path: Path) -> bool:
    """Return True if *lock_path* is currently held by some process.

    Probe via ``LOCK_NB`` — if it would block, somebody holds the lock. This is
    a cheap check used by HealthMonitor to defer recovery during a switch.
    """
    if not lock_path.exists():
        return False
    fd = None
    try:
        fd = os.open(str(lock_path), os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # We got the lock — nobody held it. Release immediately.
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)
