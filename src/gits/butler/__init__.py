"""butler — vault-PM Discord dispatch CLI, ported from vault's Tools/butler.

Split across two argparse subcommand groups in ``gits.__main__``:

- ``ghost discord <noun> <verb>`` — raw Discord REST primitives
  (transport-only; e.g. ``thread create``, ``channel list``, ``message send``).
  Meta-verbs (``setup``, ``whoami``) stay flat.
- ``ghost butler <verb>`` — PM semantics (identity, bind, dispatch, prefix
  decoration); calls into the same low-level helpers as ``ghost discord``.

The vault-side bot (``gits.adapters.discord.bot``) also imports the shared
prefix regex from :mod:`gits.butler.prefix` — so encoder (CLI) and decoder
(bot) can never drift.
"""

from __future__ import annotations

import os
import sys

ENABLED_KEY = "GITS_BUTLER_ENABLED"
_CONFIG_ENV = os.path.expanduser("~/.gits/config.env")


def butler_enabled(config_path: str | None = _CONFIG_ENV) -> bool:
    """Whether butler mode (cross-channel dispatch) is switched on.

    Off by default. Turn it on with ``GITS_BUTLER_ENABLED=1`` in the
    environment or in ``~/.gits/config.env``. While off: the ``ghost butler``
    CLI and mutating ``ghost discord`` verbs refuse, the gateway ignores
    ``[butler:<user>]``-prefixed bot messages and butler-created threads, the
    vault impl-preflight guard stands down, and ``ghost setup`` skips syncing
    the butler skills.

    Stdlib only — read from the PreToolUse guard and the CLI, neither of which
    may import ``gits.config`` (pydantic).
    """
    val = os.environ.get(ENABLED_KEY)
    if val is None and config_path:
        try:
            with open(config_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(ENABLED_KEY + "="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


def require_enabled(command: str) -> None:
    """Exit with a pointer to the switch when butler mode is off."""
    if not butler_enabled():
        sys.exit(
            f"ghost: `{command}` is disabled — butler mode is off.\n"
            f"  Set {ENABLED_KEY}=1 in ~/.gits/config.env to turn it back on."
        )
