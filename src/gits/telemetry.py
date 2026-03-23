"""Telemetry — anonymous usage tracking for ghost-in-the-shell.

Data collected: command names, platform (discord/weixin), gits version,
Python version, OS.  No PII, no message content, no file paths.

Opt-out: set GITS_TELEMETRY=0 in ~/.gits/config.env or any env.

How to set up:
  1. Create a project at https://app.posthog.com (free up to 1M events/mo)
  2. Copy the Project API Key (write-only)
  3. Replace _POSTHOG_API_KEY below with your key
"""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import threading
from typing import Any

# ── Configuration ────────────────────────────────────────────────────────────
# Your PostHog project API key (write-only, safe to ship in source).
# Leave empty to disable telemetry entirely.
_POSTHOG_API_KEY: str = "phc_j0ujRr8xnhBVNFQvzYMavpmEimSEgE5Eall91A5fQI5"
_POSTHOG_HOST: str = "https://us.i.posthog.com"

# ── Internal state ────────────────────────────────────────────────────────────
_enabled: bool | None = None
_machine_id: str | None = None
_version: str | None = None
_client: Any = None


def _is_enabled() -> bool:
    global _enabled
    if _enabled is None:
        disabled = (
            os.environ.get("GITS_TELEMETRY", "1") == "0"
            or os.environ.get("GITS_NO_TELEMETRY", "0") == "1"
            or not _POSTHOG_API_KEY
        )
        _enabled = not disabled
    return _enabled


def _get_machine_id() -> str:
    """Generate a stable, anonymous machine identifier."""
    global _machine_id
    if _machine_id is None:
        try:
            uid = str(os.getuid()) if hasattr(os, "getuid") else os.getlogin()
        except Exception:
            uid = "unknown"
        import socket
        raw = f"{socket.gethostname()}:{uid}"
        _machine_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return _machine_id


def _get_version() -> str:
    global _version
    if _version is None:
        try:
            from importlib.metadata import version
            _version = version("ghost-in-the-shell")
        except Exception:
            _version = "unknown"
    return _version


def _get_client() -> Any:
    global _client
    if _client is None:
        from posthog import Posthog  # type: ignore[import-untyped]
        _client = Posthog(project_api_key=_POSTHOG_API_KEY, host=_POSTHOG_HOST)
    return _client


def _send(event: str, properties: dict[str, Any]) -> None:
    """Send one event to PostHog in a background daemon thread."""
    if not _is_enabled():
        return

    props: dict[str, Any] = {
        "gits_version": _get_version(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "os": platform.system().lower(),
        **properties,
    }
    machine_id = _get_machine_id()

    def _post() -> None:
        try:
            _get_client().capture(distinct_id=machine_id, event=event, properties=props)
        except Exception:
            pass  # telemetry must never crash the app

    threading.Thread(target=_post, daemon=True).start()


def track(event: str, **props: Any) -> None:
    """Track an event.  Call this and forget — never raises, never blocks.

    Example::

        track("cmd_bind", platform="discord", cli="claude")
    """
    _send(event, props)


def platform_for(channel_id: str) -> str:
    """Derive platform label from channel_id."""
    return "weixin" if "@im.wechat" in channel_id else "discord"
