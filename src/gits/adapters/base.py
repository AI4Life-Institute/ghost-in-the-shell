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
class IncomingMessage:
    """Platform-agnostic inbound message."""

    platform: str  # "discord" | "telegram" | …
    channel_id: str
    user_id: str
    text: str | None = None
    image_paths: list[str] = field(default_factory=list)
    reply_to: str | None = None
    raw: Any = None  # original platform message object


@dataclass
class OutgoingMessage:
    """Platform-agnostic outbound message."""

    text: str | None = None
    image: bytes | None = None  # PNG image data
    buttons: list[list[Button]] | None = None
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
