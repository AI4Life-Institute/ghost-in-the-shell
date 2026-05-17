"""OAuth token keepalive for isolated claude accounts.

Per openspec change ``add-default-account-native-and-refresh``: non-default
accounts live under ``~/.claude-{name}/`` and don't benefit from claude's
own keychain refresh loop (the macOS keychain entry is global, so it's only
kept warm by the account routing through ``~/.claude/`` natively — i.e. the
default account). This module periodically invokes claude for every
non-default account so its refresh token doesn't silently expire.

The refresh is delegated entirely to claude itself — we just exec
``CLAUDE_CONFIG_DIR=<dir> claude --print ping`` and let claude's startup
path do its normal OAuth refresh. Ghost does **not** implement its own
refresh client (preserves the invariant documented in README:201).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .account import AccountLayout
    from .account_vault import AccountVault

logger = logging.getLogger(__name__)

#: Default per-account subprocess timeout. Claude's startup + one short
#: completion typically finishes in <15s; 60s is generous headroom for
#: network blips while still bounding total run time.
DEFAULT_TIMEOUT_S = 60

#: The no-op prompt sent to claude. Short to keep token cost negligible.
REFRESH_PROMPT = "ping"


@dataclass
class RefreshResult:
    """Outcome of one account's refresh attempt."""

    account: str
    success: bool
    exit_code: int
    duration_s: float
    stderr_tail: str = ""
    skipped_reason: str | None = None  # set when refresh was not attempted


def refresh_account(
    name: str,
    layout: AccountLayout,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    claude_bin: str | None = None,
    is_default: bool = False,
) -> RefreshResult:
    """Run ``claude --print ping`` for one account and report.

    When ``is_default`` is True, the refresh is invoked WITHOUT
    ``CLAUDE_CONFIG_DIR`` (default account uses ``~/.claude/`` natively,
    per the same change). For non-default accounts, ``CLAUDE_CONFIG_DIR``
    is set to the account's dir.

    Returns a :class:`RefreshResult` describing the outcome. Any exception
    is caught and surfaced as ``success=False`` — callers iterating
    multiple accounts must not crash on one account's failure.
    """
    bin_path = claude_bin or shutil.which("claude")
    if bin_path is None:
        return RefreshResult(
            account=name, success=False, exit_code=-1, duration_s=0.0,
            skipped_reason="claude binary not found on PATH",
        )

    env = os.environ.copy()
    if not is_default:
        env["CLAUDE_CONFIG_DIR"] = str(layout.account_dir(name))
    else:
        # Default account: drop any inherited CLAUDE_CONFIG_DIR so we
        # exercise the native ~/.claude/ path (the whole point of this
        # change). Inherited values would otherwise mask the test.
        env.pop("CLAUDE_CONFIG_DIR", None)

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [bin_path, "--print", REFRESH_PROMPT],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as e:
        return RefreshResult(
            account=name, success=False, exit_code=-1,
            duration_s=time.monotonic() - start,
            stderr_tail=f"timeout after {timeout_s}s",
        )
    except OSError as e:
        return RefreshResult(
            account=name, success=False, exit_code=-1,
            duration_s=time.monotonic() - start,
            stderr_tail=f"OSError: {e}",
        )

    duration = time.monotonic() - start
    stderr_tail = (proc.stderr or "").strip().splitlines()[-3:]
    return RefreshResult(
        account=name,
        success=proc.returncode == 0,
        exit_code=proc.returncode,
        duration_s=duration,
        stderr_tail="\n".join(stderr_tail),
    )


#: Default daily interval between in-process refresh attempts.
DEFAULT_INTERVAL_S = 24 * 60 * 60
#: Stagger after daemon start before the first refresh fires (lets the
#: daemon stabilize and avoids burning a refresh on every restart).
START_DELAY_S = 5 * 60
#: Where ``last_refresh_at`` is persisted across restarts.
STATE_FILENAME = "token_refresh_state.json"


def refresh_all_non_default(
    vault: AccountVault,
    layout: AccountLayout,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> list[RefreshResult]:
    """Refresh every account whose name is NOT the manifest default.

    Sequential, not parallel — concurrent claude invocations can race on
    shared state (statsig file, etc.). One account at a time keeps things
    boring. Returns one :class:`RefreshResult` per non-default account.
    """
    try:
        manifest = vault.load()
    except Exception as e:
        logger.error("token_refresh: cannot load manifest: %s", e)
        return []

    default = manifest.default
    results: list[RefreshResult] = []
    for entry in manifest.accounts:
        if entry.name == default:
            continue
        result = refresh_account(entry.name, layout, timeout_s=timeout_s)
        results.append(result)
        if result.success:
            logger.info(
                "token_refresh: %s ok in %.1fs", entry.name, result.duration_s,
            )
        else:
            logger.warning(
                "token_refresh: %s FAILED (exit=%d, %.1fs): %s",
                entry.name, result.exit_code, result.duration_s,
                result.stderr_tail or result.skipped_reason or "(no stderr)",
            )
    return results


class TokenRefreshScheduler:
    """In-process daily OAuth refresh — portable across machines.

    Runs ``refresh_all_non_default`` once per ``interval_s`` (24h default)
    inside the ghost daemon, persisting ``last_refresh_at`` to
    ``<state_dir>/token_refresh_state.json`` so daemon restarts don't
    re-trigger a refresh on every boot. The blocking subprocess call is
    offloaded with ``asyncio.to_thread`` to keep the event loop responsive.

    Per ``add-default-account-native-and-refresh``: this is the portable
    counterpart to the optional launchd plist — when the ghost daemon is
    running anywhere (laptop, server, new machine), the refresh just
    works without host-level scheduler setup.
    """

    def __init__(
        self,
        vault: AccountVault,
        layout: AccountLayout,
        state_dir: Path,
        *,
        interval_s: int = DEFAULT_INTERVAL_S,
        start_delay_s: int = START_DELAY_S,
    ) -> None:
        self._vault = vault
        self._layout = layout
        self._state_path = Path(state_dir) / STATE_FILENAME
        self._interval_s = interval_s
        self._start_delay_s = start_delay_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Spawn the background loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="token-refresh")
        logger.info(
            "TokenRefreshScheduler started (interval=%ds, start_delay=%ds)",
            self._interval_s, self._start_delay_s,
        )

    async def stop(self) -> None:
        """Cancel the background loop. Idempotent."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    def _read_last_refresh(self) -> float:
        try:
            data = json.loads(self._state_path.read_text())
            return float(data.get("last_refresh_at", 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return 0.0

    def _write_last_refresh(self, ts: float) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            tmp.write_text(json.dumps({"last_refresh_at": ts}))
            tmp.replace(self._state_path)
        except OSError as e:
            logger.warning("token_refresh: cannot persist last_refresh_at: %s", e)

    def _seconds_until_next_refresh(self) -> float:
        last = self._read_last_refresh()
        if last <= 0:
            return float(self._start_delay_s)
        elapsed = time.time() - last
        remaining = self._interval_s - elapsed
        return max(float(self._start_delay_s), remaining) if remaining > 0 else 0.0

    async def _loop(self) -> None:
        try:
            while True:
                wait = self._seconds_until_next_refresh()
                if wait > 0:
                    await asyncio.sleep(wait)
                await asyncio.to_thread(
                    refresh_all_non_default, self._vault, self._layout,
                )
                self._write_last_refresh(time.time())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("TokenRefreshScheduler crashed")
