"""The relayed reference must be able to become a clickable link (task [[gldref]], ghost#41).

A ref that omits the guild cannot be turned into a Discord permalink, because
the permalink path is ``/channels/<guild>/<channel>/<message>``. So the
reference was machine-verifiable (``GET /channels/{c}/messages/{m}`` resolves
the author) but never human-clickable. These tests pin the guild segment, the
DM placeholder, and the round trip between rendering and parsing.
"""

from __future__ import annotations

import re

import pytest

from gits.adapters.base import IncomingMessage
from gits.core.utterance_ref import (
    DM_GUILD,
    ParsedRef,
    format_ref,
    parse_ref,
    permalink,
)


def _msg(**kw) -> IncomingMessage:
    base = dict(
        platform="discord",
        channel_id="222",
        user_id="333",
        text="可以合",
        message_id="444",
    )
    base.update(kw)
    return IncomingMessage(**base)


class TestGuildSegment:
    """The rendered ref carries the guild, so a permalink can be built."""

    def test_guild_message_ref_carries_guild(self):
        ref = format_ref(_msg(guild_id="111"))
        assert ref == "[ref: discord:111/222/444 · from:333]"

    def test_rendered_ref_becomes_a_working_permalink(self):
        """The whole point of the ticket: ref -> clickable URL."""
        parsed = parse_ref(format_ref(_msg(guild_id="111")))
        assert permalink(parsed) == "https://discord.com/channels/111/222/444"

    def test_thread_message_keeps_guild_and_uses_thread_as_channel(self):
        """Inside a thread ``channel_id`` is the thread id and the guild is
        still present; ``/channels/<guild>/<thread>/<message>`` is a valid
        permalink, so this must not fall through to the DM branch."""
        parsed = parse_ref(format_ref(_msg(guild_id="111", channel_id="999")))
        assert parsed.guild_id == "111"
        assert permalink(parsed) == "https://discord.com/channels/111/999/444"


class TestDirectMessage:
    """A DM has no guild, so the permalink needs the ``@me`` placeholder."""

    def test_dm_renders_at_me_in_the_guild_position(self):
        assert format_ref(_msg(guild_id=None)) == "[ref: discord:@me/222/444 · from:333]"

    def test_dm_permalink_has_the_right_shape(self):
        parsed = parse_ref(format_ref(_msg(guild_id=None)))
        assert parsed.guild_id == DM_GUILD == "@me"
        assert permalink(parsed) == "https://discord.com/channels/@me/222/444"

    def test_at_me_is_never_used_for_a_non_discord_platform(self):
        """``@me`` is a Discord path literal. Another platform that supplies no
        guild must not get a Discord-shaped placeholder grafted on."""
        ref = format_ref(_msg(platform="telegram", guild_id=None))
        assert ref == "[ref: telegram:222/444 · from:333]"
        assert "@me" not in ref


class TestLegacyForm:
    """Refs already written into persistent governance records use the
    two-segment form. Parsing them must keep working -- this is the tripwire
    the ticket asked to be moved here from ``tests/test_engine.py``."""

    def test_two_segment_ref_still_parses(self):
        parsed = parse_ref("[ref: discord:222/444 · from:333]")
        assert parsed == ParsedRef(
            platform="discord", guild_id=None, channel_id="222",
            message_id="444", user_id="333",
        )

    def test_legacy_ref_permalink_falls_back_to_at_me(self):
        """Best effort: a legacy ref cannot say which guild it came from, and
        ``@me`` is the only guess available to a parser."""
        parsed = parse_ref("[ref: discord:222/444 · from:333]")
        assert permalink(parsed) == "https://discord.com/channels/@me/222/444"

    def test_legacy_and_extended_forms_both_parse(self):
        """Pin both arities in one assertion so neither can silently drop."""
        legacy = parse_ref("[ref: discord:222/444 · from:333]")
        extended = parse_ref("[ref: discord:111/222/444 · from:333]")
        assert (legacy.channel_id, legacy.message_id) == ("222", "444")
        assert (extended.channel_id, extended.message_id) == ("222", "444")
        assert legacy.guild_id is None
        assert extended.guild_id == "111"

    def test_non_numeric_ids_still_parse(self):
        """Ids are opaque strings to the parser; ghost's own tests use
        ``ch-ref``/``m-42`` style ids and other platforms are not numeric."""
        parsed = parse_ref("[ref: discord:ch-ref/m-42 · from:u-authority]")
        assert parsed.guild_id is None
        assert parsed.channel_id == "ch-ref"
        assert parsed.message_id == "m-42"


class TestRoundTrip:
    """``parse_ref`` is the inverse of ``format_ref`` -- the property that
    keeps the two from drifting apart as separate hand-written spellings."""

    @pytest.mark.parametrize("guild_id", ["111", None])
    def test_round_trip(self, guild_id):
        msg = _msg(guild_id=guild_id)
        parsed = parse_ref(format_ref(msg))
        assert parsed.platform == msg.platform
        assert parsed.channel_id == msg.channel_id
        assert parsed.message_id == msg.message_id
        assert parsed.user_id == msg.user_id
        assert parsed.guild_id == (guild_id or DM_GUILD)

    def test_round_trip_through_the_engine_producer(self):
        """The hard condition from the PM ruling: ``_format_utterance_ref``
        must really call ``format_ref``, so the module has a production
        caller and is not just a unit-test fixture calling itself."""
        from gits.core.engine import _format_utterance_ref

        msg = _msg(guild_id="111")
        assert _format_utterance_ref(msg) == format_ref(msg)


class TestNoRef:
    """The guard rails utrref established must survive this change."""

    def test_missing_message_id_returns_none(self):
        assert format_ref(_msg(message_id=None)) is None
        assert format_ref(_msg(message_id="")) is None

    def test_command_payloads_get_no_ref(self):
        """`!cmd` and `/cmd` are parsed by the CLI; an appended ref would
        become an extra argument."""
        assert format_ref(_msg(text="!ls", guild_id="111")) is None
        assert format_ref(_msg(text="/bind", guild_id="111")) is None

    def test_unparseable_input_returns_none(self):
        assert parse_ref("no ref here") is None
        assert parse_ref("") is None
        assert parse_ref(None) is None


class TestPermalinkRefusesToGuess:
    """A bad link is worse than no link: it looks like it was already checked."""

    def test_permalink_of_none_is_none(self):
        assert permalink(None) is None

    def test_permalink_only_for_discord(self):
        """A telegram ref has no discord.com permalink; do not invent one."""
        parsed = parse_ref("[ref: telegram:222/444 · from:333]")
        assert permalink(parsed) is None


# ---------------------------------------------------------------------------
# Provenance: the guild really comes from the inbound message
# ---------------------------------------------------------------------------


class _StubGuild:
    def __init__(self, gid: int) -> None:
        self.id = gid


class _StubAuthor:
    def __init__(self, uid: int) -> None:
        self.id = uid
        self.bot = False

    def __eq__(self, other) -> bool:  # never equal to bot.user
        return self is other


class _StubChannel:
    def __init__(self, cid: int) -> None:
        self.id = cid


class _StubMessage:
    """The narrowest stand-in for ``discord.Message`` that ``on_message``
    touches. Deliberately not a MagicMock: an auto-speccing mock would answer
    ``message.guild.id`` with a truthy mock even if the adapter never read it,
    which is exactly the vacuum this test exists to rule out."""

    def __init__(self, guild_id: int | None) -> None:
        import discord

        self.guild = _StubGuild(guild_id) if guild_id is not None else None
        self.author = _StubAuthor(777)
        self.channel = _StubChannel(222)
        self.content = "可以合"
        self.id = 444
        self.attachments = []
        self.reference = None
        self.type = discord.MessageType.default
        self.reactions_added: list[str] = []

    async def add_reaction(self, emoji: str) -> None:
        self.reactions_added.append(emoji)


class _StubBot:
    """``self.bot`` as far as ``_handle_message`` is concerned."""

    user = object()  # never equal to the stub author

    async def process_commands(self, message) -> None:
        pass


class _StubEngine:
    """The channel must be bound or ``_handle_message`` returns before it
    builds an IncomingMessage at all."""

    class _SessionMgr:
        def get_binding(self, channel_id):
            return object()

    def __init__(self) -> None:
        self.session_mgr = self._SessionMgr()


class TestGuildProvenance:
    """Prove the guild value travels from the inbound message -- not from a
    constant and not from configuration. Otherwise "there is a guild segment
    now" could be satisfied by filling in a fake one."""

    GUILD_FROM_MESSAGE = 999888777
    GUILD_FROM_CONFIG = 111111111

    @pytest.fixture
    def adapter(self):
        from gits.adapters.discord.bot import DiscordAdapter

        # Built without __init__ so no Discord client, token or gateway is
        # involved; only the attributes _handle_message actually reads.
        adapter = DiscordAdapter.__new__(DiscordAdapter)
        # Access is granted by user, not by guild. That is what a DM relies on
        # in production (a DM has no guild to match), and it lets the guild
        # whitelist below hold *only* a decoy.
        adapter.allowed_users = {777}
        # The whitelist holds a guild the message was NOT sent from, and never
        # the message's own. So 999888777 appearing in the rendered ref cannot
        # have come from configuration -- there is nowhere else to read it from
        # but the inbound message.
        adapter.allowed_guilds = {self.GUILD_FROM_CONFIG}
        adapter._message_callbacks = []
        adapter.bot = _StubBot()
        adapter._engine = _StubEngine()
        return adapter

    async def _relay(self, adapter, guild_id: int | None) -> IncomingMessage:
        """Drive the real ``_handle_message`` path -- the same method
        discord.py's ``on_message`` event calls -- and return what it built."""
        seen: list[IncomingMessage] = []

        async def cb(msg: IncomingMessage) -> None:
            seen.append(msg)

        adapter._message_callbacks = [cb]
        stub = _StubMessage(guild_id)
        await adapter._handle_message(stub)
        # Anti-vacuum: the reference under test came out of the production
        # inbound path applied to a real message object, not out of a string
        # the test wrote itself.
        assert len(seen) == 1, "the inbound path was never exercised"
        assert seen[0].raw is stub, "ref was not built from the inbound message"
        assert "👀" in stub.reactions_added, "not the real relay path"
        return seen[0]

    @pytest.mark.asyncio
    async def test_guild_id_comes_from_the_inbound_message(self, adapter):
        incoming = await self._relay(adapter, self.GUILD_FROM_MESSAGE)
        assert incoming.guild_id == str(self.GUILD_FROM_MESSAGE)
        assert incoming.guild_id != str(self.GUILD_FROM_CONFIG)

    @pytest.mark.asyncio
    async def test_rendered_ref_carries_the_inbound_guild(self, adapter):
        incoming = await self._relay(adapter, self.GUILD_FROM_MESSAGE)
        ref = format_ref(incoming)
        assert str(self.GUILD_FROM_MESSAGE) in ref
        assert str(self.GUILD_FROM_CONFIG) not in ref
        assert permalink(parse_ref(ref)) == (
            f"https://discord.com/channels/{self.GUILD_FROM_MESSAGE}/222/444"
        )

    @pytest.mark.asyncio
    async def test_settings_are_never_consulted_for_the_guild(
        self, adapter, monkeypatch
    ):
        """Spy: reading ghost's config on this path would mean the guild could
        come from a default rather than from the message."""
        import gits.config as config

        calls: list[str] = []

        def boom(*a, **kw):
            calls.append("Settings")
            raise AssertionError("Settings() must not be read on the relay path")

        monkeypatch.setattr(config, "Settings", boom)
        incoming = await self._relay(adapter, self.GUILD_FROM_MESSAGE)
        assert calls == []
        assert incoming.guild_id == str(self.GUILD_FROM_MESSAGE)

    @pytest.mark.asyncio
    async def test_dm_has_no_guild_on_the_inbound_message(self, adapter):
        """``@me`` is applied by the renderer because the message says DM --
        the adapter reports the absence honestly rather than filling it in."""
        incoming = await self._relay(adapter, None)
        assert incoming.guild_id is None
        assert format_ref(incoming) == "[ref: discord:@me/222/444 · from:777]"


class TestRedSideProof:
    """Documents the gap this ticket closes, in the ticket's own terms: the
    two-segment form cannot produce a usable permalink. Kept as a live
    assertion so the claim stays true rather than living only in a PR body."""

    def test_legacy_form_alone_cannot_identify_the_guild(self):
        legacy = "[ref: discord:222/444 · from:333]"
        parsed = parse_ref(legacy)
        assert parsed.guild_id is None, (
            "the pre-gldref output carried no guild segment: " + legacy
        )
        # ...so any permalink built from it is a guess, not a fact.
        assert re.match(
            r"^https://discord\.com/channels/@me/", permalink(parsed)
        ), "a legacy ref can only be guessed at, never resolved"
