"""Tests for Core Engine — command handlers with mocked tmux."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gits.config import Settings
from gits.core.engine import Engine
from gits.core.tmux import WindowInfo


class FakeInteraction:
    """Fake Discord interaction for testing."""

    def __init__(self, channel_name="test-channel"):
        self.followup = MagicMock()
        self.followup.send = AsyncMock()
        self.channel = MagicMock()
        self.channel.name = channel_name
        self.channel_id = "123456"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        gits_dir=tmp_path / ".gits",
        gits_discord_token="test-token",
        tmux_session_name="test-gits",
        coding_cli_command="claude",
    )


@pytest.fixture
def engine(settings):
    e = Engine(settings)
    # Mock tmux controller so we don't need a real tmux
    e.tmux = MagicMock()
    e.tmux.ensure_session = AsyncMock()
    e.tmux.create_window = AsyncMock(
        return_value=WindowInfo(window_id="@1", name="test", cwd="/tmp")
    )
    e.tmux.kill_window = AsyncMock(return_value=True)
    e.tmux.send_text = AsyncMock()
    e.tmux.send_keys = AsyncMock()
    e.tmux.window_exists = AsyncMock(return_value=True)
    e.tmux.capture_pane_ansi = AsyncMock(return_value="hello\x1b[31mworld\x1b[0m")
    return e


class TestHandleBind:
    def test_bind_with_path(self, engine, tmp_path):
        async def _test():
            # Create a real directory to bind
            project_dir = tmp_path / "my-project"
            project_dir.mkdir()

            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", str(project_dir), interaction)

            # Should have created tmux window
            engine.tmux.create_window.assert_called_once()
            call_args = engine.tmux.create_window.call_args
            assert call_args.kwargs["cwd"] == str(project_dir)

            # Should have created a binding
            b = engine.session_mgr.get_binding("ch-1")
            assert b is not None
            assert b.work_dir == str(project_dir)

            # Should have replied
            interaction.followup.send.assert_called_once()
            reply = interaction.followup.send.call_args[0][0]
            assert "Bound" in reply

        asyncio.run(_test())

    def test_bind_nonexistent_path(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", "/nonexistent/path/xyz", interaction)

            # Should NOT create a window
            engine.tmux.create_window.assert_not_called()

            # Should reply with error
            reply = interaction.followup.send.call_args[0][0]
            assert "not found" in reply.lower()

        asyncio.run(_test())

    def test_bind_no_path(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", None, interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "Usage" in reply or "path" in reply.lower()

        asyncio.run(_test())

    def test_bind_allowed_paths_restriction(self, engine, tmp_path):
        async def _test():
            engine.settings.allowed_paths = ["/allowed/only"]
            project_dir = tmp_path / "forbidden"
            project_dir.mkdir()

            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", str(project_dir), interaction)

            engine.tmux.create_window.assert_not_called()
            reply = interaction.followup.send.call_args[0][0]
            assert "allowed" in reply.lower()

        asyncio.run(_test())


class TestHandleUnbind:
    def test_unbind_existing(self, engine, tmp_path):
        async def _test():
            # First bind
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            # Then unbind
            interaction = FakeInteraction()
            await engine.handle_unbind("ch-1", interaction)

            assert engine.session_mgr.get_binding("ch-1") is None
            reply = interaction.followup.send.call_args[0][0]
            assert "Unbound" in reply

        asyncio.run(_test())

    def test_unbind_nonexistent(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_unbind("ch-999", interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "not bound" in reply.lower()

        asyncio.run(_test())


class TestHandleStatus:
    def test_status_bound(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            await engine.handle_status("ch-1", interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "test-channel" in reply or "Directory" in reply

        asyncio.run(_test())

    def test_status_not_bound(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_status("ch-999", interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "Not bound" in reply

        asyncio.run(_test())


class TestHandleStop:
    def test_stop_sends_escape(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            await engine.handle_stop("ch-1", interaction)

            # Should send Escape twice
            calls = engine.tmux.send_keys.call_args_list
            assert len(calls) == 2
            assert calls[0].args[1] == "Escape"
            assert calls[1].args[1] == "Escape"

        asyncio.run(_test())

    def test_stop_not_bound(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_stop("ch-999", interaction)
            reply = interaction.followup.send.call_args[0][0]
            assert "Not bound" in reply

        asyncio.run(_test())


class TestHandleKill:
    def test_kill_removes_binding(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())
            assert engine.session_mgr.get_binding("ch-1") is not None

            interaction = FakeInteraction()
            await engine.handle_kill("ch-1", interaction)

            engine.tmux.kill_window.assert_called_once()
            assert engine.session_mgr.get_binding("ch-1") is None

        asyncio.run(_test())


class TestHandleScreenshot:
    def test_screenshot_bound(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            # Set up adapter mock
            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            interaction = FakeInteraction()
            await engine.handle_screenshot("ch-1", interaction)

            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert msg.image is not None
            assert msg.image[:4] == b"\x89PNG"

        asyncio.run(_test())

    def test_screenshot_not_bound(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_screenshot("ch-999", interaction)
            reply = interaction.followup.send.call_args[0][0]
            assert "Not bound" in reply or "bind" in reply.lower()

        asyncio.run(_test())


class TestHandleModel:
    def test_model_with_name(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            await engine.handle_model("ch-1", "opus", interaction)

            engine.tmux.send_text.assert_called()
            # Find the /model call (not the initial CLI launch)
            model_calls = [
                c for c in engine.tmux.send_text.call_args_list
                if "/model" in str(c)
            ]
            assert len(model_calls) >= 1
            reply = interaction.followup.send.call_args[0][0]
            assert "opus" in reply.lower()

        asyncio.run(_test())

    def test_model_without_name(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            await engine.handle_model("ch-1", None, interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "sonnet" in reply.lower()
            assert "opus" in reply.lower()

        asyncio.run(_test())


class TestHandleBash:
    def test_bash_runs_command(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            await engine.handle_bash("ch-1", "echo hello", interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "hello" in reply
            assert "Exit code: 0" in reply

        asyncio.run(_test())

    def test_bash_not_bound(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_bash("ch-999", "ls", interaction)
            reply = interaction.followup.send.call_args[0][0]
            assert "Not bound" in reply

        asyncio.run(_test())


class TestHandleCliForward:
    def test_forward_with_slash(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            await engine.handle_cli_forward("ch-1", "/compact", interaction)

            # Should have sent /compact to tmux
            forward_calls = [
                c for c in engine.tmux.send_text.call_args_list
                if "/compact" in str(c)
            ]
            assert len(forward_calls) >= 1

        asyncio.run(_test())

    def test_forward_without_slash(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            await engine.handle_cli_forward("ch-1", "doctor", interaction)

            # Should have prepended /
            forward_calls = [
                c for c in engine.tmux.send_text.call_args_list
                if "/doctor" in str(c)
            ]
            assert len(forward_calls) >= 1

        asyncio.run(_test())


class TestHandleMessage:
    def test_forwards_text(self, engine, tmp_path):
        async def _test():
            from gits.adapters.base import IncomingMessage

            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            msg = IncomingMessage(
                platform="discord",
                channel_id="ch-1",
                user_id="user-1",
                text="fix the bug please",
            )
            await engine.handle_message(msg)

            # Should have sent to tmux
            text_calls = [
                c for c in engine.tmux.send_text.call_args_list
                if "fix the bug" in str(c)
            ]
            assert len(text_calls) >= 1

        asyncio.run(_test())

    def test_ignores_unbound_channel(self, engine):
        async def _test():
            from gits.adapters.base import IncomingMessage

            msg = IncomingMessage(
                platform="discord",
                channel_id="ch-999",
                user_id="user-1",
                text="hello",
            )
            call_count_before = engine.tmux.send_text.call_count
            await engine.handle_message(msg)
            assert engine.tmux.send_text.call_count == call_count_before

        asyncio.run(_test())
