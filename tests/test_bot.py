"""Tests for Discord bot adapter."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest


class TestArchiveThread:
    def test_archive_thread_locks(self):
        """archive_thread must set both archived=True and locked=True."""
        async def _test():
            # Import here to avoid top-level discord dependency issues
            from gits.adapters.discord.bot import DiscordAdapter
            from gits.config import Settings

            settings = MagicMock(spec=Settings)
            adapter = DiscordAdapter.__new__(DiscordAdapter)
            adapter.bot = MagicMock()

            # Mock a Discord Thread
            mock_thread = MagicMock(spec=discord.Thread)
            mock_thread.edit = AsyncMock()
            adapter.bot.get_channel = MagicMock(return_value=mock_thread)

            await adapter.archive_thread("123456")

            mock_thread.edit.assert_called_once_with(archived=True, locked=True)

        asyncio.run(_test())
