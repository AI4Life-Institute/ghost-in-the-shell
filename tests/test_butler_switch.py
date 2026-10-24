"""Tests for the butler-mode switch (``GITS_BUTLER_ENABLED``, default off)."""

import argparse
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from gits.butler import butler_enabled, require_enabled
from gits.butler import butler_cli, discord_cli


# ─── butler_enabled() ────────────────────────────────────────────────────


def test_default_off(monkeypatch, tmp_path):
    monkeypatch.delenv("GITS_BUTLER_ENABLED", raising=False)
    assert butler_enabled(config_path=str(tmp_path / "missing.env")) is False


def test_config_env_turns_on(monkeypatch, tmp_path):
    monkeypatch.delenv("GITS_BUTLER_ENABLED", raising=False)
    cfg = tmp_path / "config.env"
    cfg.write_text("GITS_DISCORD_TOKEN=x\nGITS_BUTLER_ENABLED=true\n")
    assert butler_enabled(config_path=str(cfg)) is True


def test_env_overrides_config(monkeypatch, tmp_path):
    cfg = tmp_path / "config.env"
    cfg.write_text("GITS_BUTLER_ENABLED=1\n")
    monkeypatch.setenv("GITS_BUTLER_ENABLED", "0")
    assert butler_enabled(config_path=str(cfg)) is False


def test_require_enabled_exits_when_off(monkeypatch):
    monkeypatch.setenv("GITS_BUTLER_ENABLED", "0")
    with pytest.raises(SystemExit) as exc:
        require_enabled("ghost butler")
    assert "GITS_BUTLER_ENABLED=1" in str(exc.value)


# ─── CLI gates ───────────────────────────────────────────────────────────


def test_butler_cli_refuses_when_off(monkeypatch):
    monkeypatch.setenv("GITS_BUTLER_ENABLED", "0")
    func = MagicMock()
    with pytest.raises(SystemExit):
        butler_cli.dispatch(argparse.Namespace(func=func))
    func.assert_not_called()


def test_discord_cli_refuses_send_but_allows_reads_when_off(monkeypatch):
    monkeypatch.setenv("GITS_BUTLER_ENABLED", "0")
    with patch.object(discord_cli, "cmd_message_send") as send:
        monkeypatch.setattr(discord_cli, "_MUTATING", (send,))
        with pytest.raises(SystemExit):
            discord_cli.dispatch(argparse.Namespace(func=send))
        send.assert_not_called()
    read = MagicMock()
    discord_cli.dispatch(argparse.Namespace(func=read))
    read.assert_called_once()


# ─── Gateway gates ───────────────────────────────────────────────────────


def _adapter():
    from gits.adapters.discord.bot import DiscordAdapter

    adapter = DiscordAdapter.__new__(DiscordAdapter)
    adapter.bot = MagicMock()
    adapter.bot.user = MagicMock(id=42)
    adapter._engine = MagicMock()
    adapter._handle_butler_command = AsyncMock(return_value=True)
    return adapter


def test_bot_ignores_butler_message_when_off(monkeypatch):
    monkeypatch.setenv("GITS_BUTLER_ENABLED", "0")
    adapter = _adapter()
    msg = MagicMock()
    msg.author = adapter.bot.user
    msg.content = "📨 **[butler:sharon]** /bind /tmp/x"
    asyncio.run(adapter._handle_message(msg))
    adapter._handle_butler_command.assert_not_called()


def test_bot_routes_butler_message_when_on(monkeypatch):
    monkeypatch.setenv("GITS_BUTLER_ENABLED", "1")
    adapter = _adapter()
    adapter._check_access = MagicMock(return_value=True)
    msg = MagicMock()
    msg.author = adapter.bot.user
    msg.type = discord.MessageType.default
    msg.content = "📨 **[butler:sharon]** /bind /tmp/x"
    asyncio.run(adapter._handle_message(msg))
    adapter._handle_butler_command.assert_awaited_once()


def test_bot_skips_butler_thread_when_off(monkeypatch):
    monkeypatch.setenv("GITS_BUTLER_ENABLED", "0")
    adapter = _adapter()
    adapter._engine.handle_thread_auto = AsyncMock()
    thread = MagicMock(spec=discord.Thread)
    thread.id = 7
    thread.parent_id = 1
    thread.owner_id = 42
    thread.starter_message = MagicMock(content="📨 **[butler:sharon]** task")
    asyncio.run(adapter._handle_thread_create(thread))
    adapter._engine.handle_thread_auto.assert_not_called()
