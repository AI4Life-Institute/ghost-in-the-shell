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
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://discord.com/api/v10"
CONFIG = os.path.expanduser("~/.gits/config.env")
USER_AGENT = "ghost-butler/1.0 (vault dispatch)"


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


def api(
    path: str,
    method: str = "GET",
    body: Any = None,
    query: dict | None = None,
) -> tuple[int, Any]:
    """Call Discord REST. Returns (http_status, parsed_body_or_raw_text)."""
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
