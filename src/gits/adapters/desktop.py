"""DesktopAdapter — routes Engine output to the Electron shell via stdout JSON.

Protocol (newline-delimited JSON on stdout):
  {"event": "output",  "channel_id": "...", "text": "...", "image_b64": "..."}
  {"event": "edit",    "channel_id": "...", "message_id": "...", "text": "..."}
  {"event": "delete",  "channel_id": "...", "message_id": "..."}
  {"event": "hitl",    "channel_id": "...", "message_id": "...",
                       "buttons": [[...]] | "select_options": [...]}
  {"event": "thread",  "channel_id": "...", "thread_id": "..."}
"""

from __future__ import annotations

import base64
import json
import sys
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from .base import (
    Button,
    ButtonCallback,
    IncomingMessage,
    MessageCallback,
    OutgoingMessage,
    PlatformAdapter,
    SelectOption,
)


class DesktopAdapter(PlatformAdapter):
    """Platform adapter that emits JSON events to stdout for Electron to consume."""

    def __init__(self, emit: Callable[[dict], None]) -> None:
        self._emit = emit
        self._message_cb: MessageCallback | None = None
        self._button_cb: ButtonCallback | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """No-op: desktop adapter is always 'connected'."""

    async def stop(self) -> None:
        """No-op."""

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def send_message(self, channel_id: str, msg: OutgoingMessage) -> str:
        message_id = str(uuid.uuid4())[:8]

        if msg.image:
            self._emit({
                "event": "output",
                "channel_id": channel_id,
                "message_id": message_id,
                "image_b64": base64.b64encode(msg.image).decode(),
            })
        elif msg.select_options:
            self._emit({
                "event": "hitl",
                "channel_id": channel_id,
                "message_id": message_id,
                "text": msg.text,
                "placeholder": msg.select_placeholder,
                "select_options": [
                    {"label": o.label, "value": o.value, "description": o.description}
                    for o in msg.select_options
                ],
            })
        elif msg.buttons:
            self._emit({
                "event": "hitl",
                "channel_id": channel_id,
                "message_id": message_id,
                "text": msg.text,
                "buttons": [
                    [{"label": b.label, "callback_data": b.callback_data} for b in row]
                    for row in msg.buttons
                ],
            })
        else:
            self._emit({
                "event": "output",
                "channel_id": channel_id,
                "message_id": message_id,
                "text": msg.text or "",
            })

        return message_id

    async def edit_message(
        self, channel_id: str, message_id: str, msg: OutgoingMessage
    ) -> None:
        self._emit({
            "event": "edit",
            "channel_id": channel_id,
            "message_id": message_id,
            "text": msg.text or "",
        })

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        self._emit({
            "event": "delete",
            "channel_id": channel_id,
            "message_id": message_id,
        })

    # ------------------------------------------------------------------
    # Inbound registration
    # ------------------------------------------------------------------

    def on_message(self, callback: MessageCallback) -> None:
        self._message_cb = callback

    def on_button_click(self, callback: ButtonCallback) -> None:
        self._button_cb = callback

    # ------------------------------------------------------------------
    # Threads (no-op: desktop uses channel IDs directly)
    # ------------------------------------------------------------------

    async def create_thread(
        self, channel_id: str, title: str, auto_archive_minutes: int = 10080
    ) -> str:
        thread_id = channel_id  # reuse channel — desktop has no threading
        self._emit({"event": "thread", "channel_id": channel_id, "thread_id": thread_id})
        return thread_id

    async def archive_thread(self, thread_id: str) -> None:
        pass

    # ------------------------------------------------------------------
    # Dispatch helpers (called by _cmd_desktop to feed inbound events)
    # ------------------------------------------------------------------

    async def dispatch_message(self, msg: IncomingMessage) -> None:
        if self._message_cb:
            await self._message_cb(msg)

    async def dispatch_button(
        self, channel_id: str, user_id: str, callback_data: str
    ) -> None:
        if self._button_cb:
            await self._button_cb(channel_id, user_id, callback_data)
