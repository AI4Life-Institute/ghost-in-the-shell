"""Discord REST helpers — stdlib urllib only.

Both ``ghost discord <verb>`` and ``ghost butler <verb>`` call into this
module; no HTTP code is duplicated between the two subcommand groups.

stdlib-only is a hard constraint on the CLI path (see task a2ec59 AC).
Do NOT introduce aiohttp / httpx / discord.py here — discord.py is only
used by the gateway loop in ``gits.adapters.discord.bot``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .prefix import VAULT_DISPATCH_RE

API = "https://discord.com/api/v10"
CONFIG = os.path.expanduser("~/.gits/config.env")
STATE = os.path.expanduser("~/.gits/state.json")
USER_AGENT = "ghost-butler/1.0 (vault dispatch)"

# POST here publishes a message. Guarded below; every other route is not.
_MESSAGE_POST_RE = re.compile(r"^/channels/(\d+)/messages$")


def load_token() -> str:
    """Read ``GITS_DISCORD_TOKEN`` out of ``~/.gits/config.env``.

    Same token the gateway bot uses — intentional: one bot identity per
    machine, vault dispatches and live bot messages share it.
    """
    try:
        with open(CONFIG) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GITS_DISCORD_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        sys.exit(f"Config not found: {CONFIG}")
    sys.exit(f"GITS_DISCORD_TOKEN not found in {CONFIG}")


def self_bound_channel() -> str | None:
    """Channel the *calling* session is itself bound to, or ``None``.

    The caller runs inside a tmux pane the gateway created for a binding, so
    ``$TMUX_PANE`` -> window id -> binding is an identity, not a guess.
    Returns ``None`` outside tmux, when tmux or the state file can't be read,
    or when the pane's window carries no binding (a plain shell, a cron, a
    dispatch worker) -- every failure mode leaves the send permitted, because
    a broken lookup must never swallow a report.

    Reads ``~/.gits/state.json`` directly rather than importing the engine's
    SessionManager: this module is on the stdlib-only CLI path.
    """
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane, "#{window_id}"],
            capture_output=True, text=True, timeout=5,
        )
        window_id = out.stdout.strip()
        if out.returncode != 0 or not window_id:
            return None
        with open(STATE) as f:
            bindings = json.load(f).get("bindings") or {}
        # tmux reuses window ids, so several *stale* bindings can carry the
        # same one. The live session is the most recently active of them —
        # taking the first match would silently resolve to a dead binding and
        # let the guard through.
        matches = [
            (b.get("last_active_at") or 0, c)
            for c, b in bindings.items()
            if b.get("window_id") == window_id
        ]
        if not matches:
            return None
        return max(matches)[1]
    except Exception:
        return None


def _refuse_self_send(channel_id: str, body: Any) -> None:
    """Refuse publishing a message into the caller's own bound channel.

    A bound channel already carries everything the session says: the gateway
    tails the CLI transcript and posts each assistant text there. Publishing
    the same words again puts a second copy in the channel, and the
    butler-prefixed copy is forwarded back into the session's own pane, where
    it reads as a fresh instruction and can trigger yet another report.

    The check lives here, at the single REST chokepoint, rather than on one
    verb: ``ghost butler send`` and ``ghost discord message send`` both reach
    Discord through :func:`api`, and a guard on either one alone just teaches
    the caller to use the other.

    Slash payloads are exempt: ``/bind`` and friends are control messages the
    gateway routes through ``_handle_butler_command``, not prose the transcript
    relay would have duplicated.
    """
    content = (body or {}).get("content") if isinstance(body, dict) else None
    if not content:
        return
    # Strip butler decoration before asking "is this a command?" — by the time
    # a dispatch reaches here the payload reads `📨 **[butler:x]** /bind ...`.
    payload = VAULT_DISPATCH_RE.sub("", content).lstrip()
    if payload.startswith("/"):
        return
    if self_bound_channel() != channel_id:
        return
    sys.exit(
        f"ghost: refusing to publish into {channel_id} — that is this "
        "session's own bound channel.\n"
        "  Everything you say in this session is already posted there: the "
        "gateway tails this\n"
        "  CLI's transcript and forwards each reply to that channel. The "
        "operator has read it.\n"
        "  Sending it again puts a second copy in the channel and feeds a "
        "copy back into this\n"
        "  pane as if it were a new instruction. Answering in this session IS "
        "answering in the\n"
        "  channel. To reach a *different* channel or thread, pass its id."
    )


def api(
    path: str,
    method: str = "GET",
    body: Any = None,
    query: dict | None = None,
) -> tuple[int, Any]:
    """Call Discord REST. Returns (http_status, parsed_body_or_raw_text)."""
    if method == "POST":
        m = _MESSAGE_POST_RE.match(path)
        if m is not None:
            _refuse_self_send(m.group(1), body)
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(
            {k: v for k, v in query.items() if v is not None}
        )
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bot {load_token()}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode() if body else None,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read().decode()
            return resp.status, (json.loads(data) if data else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def die(status: int, body: Any) -> None:
    msg = (
        json.dumps(body, ensure_ascii=False)
        if isinstance(body, (dict, list))
        else body
    )
    sys.exit(f"[{status}] {msg}")
