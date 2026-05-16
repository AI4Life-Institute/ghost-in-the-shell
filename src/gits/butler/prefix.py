"""Single source of truth for the butler/vault prefix protocol.

Two consumers must agree on this regex:

- ``gits.butler.butler_cli`` (encoder) — stamps it on outgoing dispatches.
- ``gits.adapters.discord.bot`` (decoder) — recognizes it on incoming
  messages so vault-dispatched bot messages don't get dropped by the
  "ignore my own bot's messages" rule.

Previously the pattern was copy-pasted in both places and drift caused
real outages (see vault tasks ifjqr3 / 5tid38). Keep imports honest:
both sides ``from gits.butler.prefix import VAULT_DISPATCH_RE``.

The ``GITS_DISPATCH_PREFIX_PATTERN`` env var, if set, overrides the
default — preserved from bot.py's prior behavior so an operator can
broaden recognition without a code change.
"""

from __future__ import annotations

import os
import re

DEFAULT_PATTERN = (
    r"^(?:[\U0001F300-\U0001FAFF]\s*)?(?:\*\*)?"
    r"\[(butler|管家|vault)(:[^\]]*)?\](?:\*\*)?"
)

VAULT_DISPATCH_RE = re.compile(
    os.environ.get("GITS_DISPATCH_PREFIX_PATTERN", DEFAULT_PATTERN)
)

LABEL_DEFAULT = "butler"

BUTLER_FOOTER = "\n━━━━━━━━"


def decorate(label: str, user: str, content: str) -> str:
    """Wrap ``content`` with the standard butler decoration.

    - Prefix: ``📨 **[label:user]** `` (emoji + bold; the bracket is the
      recognition anchor for :data:`VAULT_DISPATCH_RE`).
    - Trailing :data:`BUTLER_FOOTER` separator — UNLESS the body is a
      slash command (e.g. ``/bind <path>``). The bot's command router
      slices payload after the prefix and positional-parses, so a footer
      would arrive as a bogus arg.
    """
    prefix = f"📨 **[{label}:{user}]** "
    is_slash = content.lstrip().startswith("/")
    suffix = "" if is_slash else BUTLER_FOOTER
    return f"{prefix}{content}{suffix}"
