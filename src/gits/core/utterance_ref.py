"""The one definition of the utterance-reference format (task [[gldref]]).

An utterance reference is the compact pointer ghost appends to a message it
relays into a CLI session, so an agent can cite the words it was given without
asking anyone for evidence (task [[utrref]])::

    [ref: <platform>:<guild_id>/<channel_id>/<message_id> · from:<user_id>]

Why this module exists
----------------------
Not for backwards compatibility -- there is, as of ghost#41, no consumer
anywhere that parses this string. ghost's own :mod:`gits.hooks.core_os_ticket`
accepts only full permalinks (deliberately -- see its ``_UTTERANCE_REF_RE``),
and builder-os stores ``utterance_ref`` as an opaque string with no pattern at
all. It exists for two other reasons:

1. **It is the format's only definition.** A producer and a future reader that
   each hand-write their own spelling drift apart by omission -- exactly the
   failure the guild segment itself is an instance of.
2. **It makes "the old form still parses" a testable claim.** Without a
   parser there is no object for that claim to be pinned to.

:func:`format_ref` therefore has a real production caller
(``gits.core.engine._format_utterance_ref``), and :func:`parse_ref` is its
inverse, pinned by a round-trip test.

The guild segment
-----------------
A reference without a guild cannot become a Discord permalink, because the
permalink path is ``/channels/<guild>/<channel>/<message>``. That made the
reference machine-verifiable (``GET /channels/{c}/messages/{m}`` resolves the
author) but never human-clickable, which is what ghost#41 is about.

Direct messages have no guild, and Discord spells that position ``@me``. The
placeholder is asymmetric on purpose:

* :func:`format_ref` emits ``@me`` **only** when the platform affirmatively
  reported no guild, i.e. it really is a DM. It is never a filler for "guild
  unknown": an ``@me`` permalink for a guild message resolves to nothing, and
  a broken link is worse than no link because it looks like it was checked.
* :func:`permalink` falls back to ``@me`` for a two-segment legacy reference,
  because that is the only guess available to a reader after the fact. That is
  a documented best effort, not a claim about where the message came from.

Nothing here validates or unifies the ``discord:`` / ``ghost-discord:``
prefixes -- two spellings are in circulation and unifying one side alone would
create a second divergence while fixing the first (builder-os#210).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..adapters.base import IncomingMessage

__all__ = ["DM_GUILD", "ParsedRef", "format_ref", "parse_ref", "permalink"]

#: How Discord spells "no guild" in a permalink path: DMs and group DMs live
#: under ``/channels/@me/...``.
DM_GUILD = "@me"

#: The platform whose ids ``permalink`` knows how to turn into a URL. Other
#: platforms round-trip through this module fine; they just have no
#: discord.com permalink, and inventing one would be a lie.
_PERMALINK_PLATFORM = "discord"

_PERMALINK_BASE = "https://discord.com/channels"

# Ids are opaque strings, not necessarily numeric: ghost's own tests use
# ``ch-ref``/``m-42``, and other platforms number things their own way. The
# path is split on "/" rather than matched with an optional leading group so
# that arity is read explicitly instead of resting on regex backtracking.
_REF_RE = re.compile(
    r"\[ref: (?P<platform>[^:\s\]]+):(?P<path>[^\s\]]+) · from:(?P<user_id>[^\s\]]+)\]"
)

# Command payloads are parsed by the CLI, not read as prose: `!cmd` runs a
# shell command and `/cmd` a slash command. An appended reference would arrive
# as an extra argument, so those are relayed verbatim.
_COMMAND_PREFIXES = ("!", "/")


@dataclass(frozen=True)
class ParsedRef:
    """The facts carried by an utterance reference.

    ``guild_id`` is ``None`` for a two-segment legacy reference -- meaning
    "this reference does not say", which is distinct from ``"@me"`` meaning
    "the platform reported a DM".
    """

    platform: str
    channel_id: str
    message_id: str
    user_id: str
    guild_id: str | None = None


def format_ref(msg: IncomingMessage) -> str | None:
    """Render a compact, machine-parseable pointer to *msg* itself.

    Returns ``None`` when there is nothing citable to point at, or when the
    payload is a command -- callers then relay the bare text rather than
    dropping the message. Delivery beats citability.
    """
    if not msg.message_id or not msg.channel_id or not msg.platform:
        return None
    if (msg.text or "").lstrip().startswith(_COMMAND_PREFIXES):
        return None

    guild_id = getattr(msg, "guild_id", None)
    if not guild_id and msg.platform == _PERMALINK_PLATFORM:
        # Affirmatively a DM: the adapter reported the absence of a guild, and
        # Discord's own spelling for that position is "@me".
        guild_id = DM_GUILD

    path = f"{guild_id}/{msg.channel_id}" if guild_id else msg.channel_id
    return f"[ref: {msg.platform}:{path}/{msg.message_id} · from:{msg.user_id}]"


def parse_ref(ref: str | None) -> ParsedRef | None:
    """Read back a reference rendered by :func:`format_ref`.

    Accepts both the three-segment form and the two-segment form written
    before ghost#41 (whose references are already in persistent governance
    records). Returns ``None`` for anything it does not recognise rather than
    guessing at a malformed reference.
    """
    if not ref:
        return None
    m = _REF_RE.search(ref)
    if not m:
        return None

    segments = m.group("path").split("/")
    if len(segments) == 3:
        guild_id, channel_id, message_id = segments
    elif len(segments) == 2:
        guild_id = None
        channel_id, message_id = segments
    else:
        return None
    if not channel_id or not message_id:
        return None

    return ParsedRef(
        platform=m.group("platform"),
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        user_id=m.group("user_id"),
    )


def permalink(parsed: ParsedRef | None) -> str | None:
    """Build the clickable Discord URL for *parsed*, or ``None``.

    ``None`` when there is no reference, or when the reference is not from a
    platform with discord.com permalinks. A legacy reference carrying no guild
    falls back to ``@me`` -- see this module's docstring on why that fallback
    is safe to read but never safe to write.
    """
    if parsed is None or parsed.platform != _PERMALINK_PLATFORM:
        return None
    return (
        f"{_PERMALINK_BASE}/{parsed.guild_id or DM_GUILD}"
        f"/{parsed.channel_id}/{parsed.message_id}"
    )
