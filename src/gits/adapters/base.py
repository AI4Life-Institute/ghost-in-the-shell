"""PlatformAdapter — abstract base for chat platform integrations.

Each chat platform (Discord, Telegram, Slack, …) implements this
interface so the Core Engine can work platform-agnostically.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Button:
    """A clickable button rendered by the platform."""

    label: str
    callback_data: str


@dataclass
class SelectOption:
    """A single option in a dropdown / select menu."""

    label: str
    value: str  # callback_data sent on selection
    description: str | None = None


@dataclass
class IncomingMessage:
    """Platform-agnostic inbound message."""

    platform: str  # "discord" | "telegram" | …
    channel_id: str
    user_id: str
    text: str | None = None
    image_paths: list[str] = field(default_factory=list)
    reply_to: str | None = None
    # Platform message id of *this* message (task [[utrref]]). Optional:
    # adapters that cannot supply one leave it None and forwarding still
    # works — delivery beats citability.
    message_id: str | None = None
    # Server/guild the message came from (task [[gldref]]). Optional and None
    # for a DM, which is a fact worth reporting honestly: a permalink needs
    # /channels/<guild>/<channel>/<message>, and the renderer substitutes
    # Discord's "@me" only because the adapter said there was no guild. See
    # gits.core.utterance_ref on why that asymmetry matters.
    guild_id: str | None = None
    raw: Any = None  # original platform message object


@dataclass
class OutgoingMessage:
    """Platform-agnostic outbound message."""

    text: str | None = None
    image: bytes | None = None  # PNG image data
    buttons: list[list[Button]] | None = None
    select_options: list[SelectOption] | None = None
    select_placeholder: str | None = None
    edit_message_id: str | None = None
    ephemeral: bool = False


# Callback type aliases
MessageCallback = Callable[[IncomingMessage], Coroutine[Any, Any, None]]
ButtonCallback = Callable[[str, str, str], Coroutine[Any, Any, None]]
# ButtonCallback(channel_id, user_id, callback_data)


class PlatformAdapter(ABC):
    """Chat platform adapter interface.

    Implementations translate between platform-specific APIs and the
    platform-agnostic message types defined above.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start the adapter (connect to platform, start event loop)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the adapter."""
        ...

    @abstractmethod
    async def send_message(self, channel_id: str, msg: OutgoingMessage) -> str:
        """Send a message to a channel. Returns the message ID."""
        ...

    @abstractmethod
    async def edit_message(
        self, channel_id: str, message_id: str, msg: OutgoingMessage
    ) -> None:
        """Edit an existing message."""
        ...

    @abstractmethod
    async def delete_message(self, channel_id: str, message_id: str) -> None:
        """Delete a message."""
        ...

    @abstractmethod
    def on_message(self, callback: MessageCallback) -> None:
        """Register a handler for incoming messages."""
        ...

    @abstractmethod
    def on_button_click(self, callback: ButtonCallback) -> None:
        """Register a handler for button clicks."""
        ...

    @abstractmethod
    async def create_thread(
        self,
        channel_id: str,
        title: str,
        auto_archive_minutes: int = 10080,
    ) -> str:
        """Create a thread / sub-conversation. Returns the thread ID."""
        ...

    @abstractmethod
    async def archive_thread(self, thread_id: str) -> None:
        """Archive / close a thread."""
        ...


class MultiAdapter(PlatformAdapter):
    """Routes send_message/edit_message/delete_message to the right adapter
    based on channel_id.  Each adapter registers its own on_message callbacks
    directly — this class is only used as engine._adapter for outbound routing.

    Routing rule: channel_id containing '@im.wechat' → WeChat adapter;
    everything else → first non-WeChat adapter.
    """

    def __init__(self, adapters: list[PlatformAdapter]) -> None:
        self._adapters = adapters

    def add_adapter(self, adapter: PlatformAdapter) -> None:
        """Dynamically register a new adapter (e.g. a newly logged-in WeChat account)."""
        self._adapters.append(adapter)

    async def remove_adapter(self, account_id: str) -> None:
        """Stop and deregister a WeChat adapter by account_id."""
        for a in list(self._adapters):
            if type(a).__name__ == "WeixinAdapter" and getattr(a, "account_id", None) == account_id:
                await a.stop()
                self._adapters.remove(a)
                return

    def _route(self, channel_id: str) -> PlatformAdapter:
        if "@im.wechat" in channel_id:
            weixin_adapters = [a for a in self._adapters if type(a).__name__ == "WeixinAdapter"]
            # Prefer the adapter that already has context for this user
            for a in weixin_adapters:
                if a.knows_user(channel_id):  # type: ignore[attr-defined]
                    return a
            if weixin_adapters:
                return weixin_adapters[0]
        for a in self._adapters:
            if type(a).__name__ != "WeixinAdapter":
                return a
        return self._adapters[0]

    async def start(self) -> None:
        pass  # individual adapters are started by the caller

    async def stop(self) -> None:
        pass

    async def send_message(self, channel_id: str, msg: OutgoingMessage) -> str:
        return await self._route(channel_id).send_message(channel_id, msg)

    async def edit_message(self, channel_id: str, message_id: str, msg: OutgoingMessage) -> None:
        await self._route(channel_id).edit_message(channel_id, message_id, msg)

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        await self._route(channel_id).delete_message(channel_id, message_id)

    def on_message(self, callback: MessageCallback) -> None:
        pass  # callbacks registered directly on each adapter

    def on_button_click(self, callback: ButtonCallback) -> None:
        pass

    async def create_thread(self, channel_id: str, title: str, auto_archive_minutes: int = 10080) -> str:
        return await self._route(channel_id).create_thread(channel_id, title, auto_archive_minutes)

    async def archive_thread(self, thread_id: str) -> None:
        for a in self._adapters:
            try:
                await a.archive_thread(thread_id)
                return
            except Exception:
                pass
