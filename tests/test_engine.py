"""Tests for Core Engine — command handlers with mocked tmux."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gits.config import Settings
from gits.core.engine import Engine, _format_age
from gits.core.launcher import CLISession
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

    def test_bind_no_path_launches_browser(self, engine):
        async def _test():
            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", None, interaction)

            # Should send browser message via adapter
            adapter.send_message.assert_called_once()
            browser_msg = adapter.send_message.call_args[0][1]
            assert "Select Working Directory" in browser_msg.text
            assert browser_msg.buttons is not None

            # Should store pending browse state
            assert "ch-1" in engine._pending_binds
            assert engine._pending_binds["ch-1"]["type"] == "browse"

            # Interaction should get acknowledgement
            interaction.followup.send.assert_called_once()

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

            interaction = FakeInteraction()
            await engine.handle_screenshot("ch-1", interaction)

            # Screenshot now replies via interaction.followup.send(file=...)
            interaction.followup.send.assert_called_once()
            call_kwargs = interaction.followup.send.call_args[1]
            assert "file" in call_kwargs

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


# ------------------------------------------------------------------
# Session Picker tests
# ------------------------------------------------------------------


def _make_sessions(count: int = 3) -> list[CLISession]:
    """Create a list of fake CLISession objects for testing."""
    now = time.time()
    sessions = []
    for i in range(count):
        sessions.append(
            CLISession(
                session_id=f"sess-{i:04d}-abcdef",
                summary=f"Session {i} summary text",
                message_count=10 * (i + 1),
                file_path=f"/tmp/fake/{i}.jsonl",
                mtime=now - (3600 * (i + 1)),  # 1h, 2h, 3h ago...
            )
        )
    return sessions


class TestFormatAge:
    def test_just_now(self):
        assert _format_age(time.time() - 10) == "just now"

    def test_minutes(self):
        assert _format_age(time.time() - 180) == "3m ago"

    def test_hours(self):
        assert _format_age(time.time() - 7200) == "2h ago"

    def test_days(self):
        assert _format_age(time.time() - 86400) == "1d ago"

    def test_weeks(self):
        assert _format_age(time.time() - 604800 * 3) == "3w ago"


class TestBuildSessionPickerMessage:
    def test_message_text_contains_sessions(self, engine):
        sessions = _make_sessions(3)
        msg = engine._build_session_picker_message(sessions, "/tmp/project", "ch-1")

        assert msg.text is not None
        assert "Resume Session?" in msg.text
        assert "/tmp/project" in msg.text
        assert "Session 0 summary" in msg.text
        assert "Session 2 summary" in msg.text

    def test_message_has_buttons(self, engine):
        sessions = _make_sessions(2)
        msg = engine._build_session_picker_message(sessions, "/tmp/project", "ch-1")

        assert msg.buttons is not None
        # 2 sessions in 1 row of 2 + 1 row for New Session = 2 rows
        assert len(msg.buttons) == 2

        # Last row should have New Session button
        last_row = msg.buttons[-1]
        assert any("New Session" in btn.label for btn in last_row)

    def test_callback_data_format(self, engine):
        sessions = _make_sessions(1)
        msg = engine._build_session_picker_message(sessions, "/tmp/project", "ch-1")

        # Session button callback
        session_btn = msg.buttons[0][0]
        assert session_btn.callback_data == "bind_resume:ch-1:0"

        # New session button callback
        new_btn = msg.buttons[-1][0]
        assert new_btn.callback_data == "bind_new:ch-1"

    def test_max_5_sessions_displayed(self, engine):
        sessions = _make_sessions(8)
        msg = engine._build_session_picker_message(sessions, "/tmp/project", "ch-1")

        # Should only show 5 sessions (buttons in rows of 2: 3 rows + 1 new)
        assert msg.buttons is not None
        all_buttons = [btn for row in msg.buttons for btn in row]
        resume_buttons = [b for b in all_buttons if b.callback_data.startswith("bind_resume")]
        assert len(resume_buttons) == 5

    def test_callback_data_within_100_chars(self, engine):
        sessions = _make_sessions(5)
        # Use a long channel_id
        msg = engine._build_session_picker_message(
            sessions, "/tmp/project", "1234567890" * 5
        )
        for row in msg.buttons:
            for btn in row:
                assert len(btn.callback_data) <= 100


class TestSessionPickerFlow:
    """Test the full bind flow with session discovery."""

    def test_bind_shows_picker_when_sessions_exist(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "my-project"
            project_dir.mkdir()

            sessions = _make_sessions(2)
            engine.launcher.discover_sessions = MagicMock(return_value=sessions)

            # Set up adapter to capture sent messages
            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", str(project_dir), interaction)

            # Should NOT have created a tmux window yet
            engine.tmux.create_window.assert_not_called()

            # Should have sent a picker message via adapter
            adapter.send_message.assert_called_once()
            call_args = adapter.send_message.call_args
            assert call_args[0][0] == "ch-1"
            picker_msg = call_args[0][1]
            assert picker_msg.buttons is not None
            assert "Resume Session?" in picker_msg.text

            # Should have stored pending bind
            assert "ch-1" in engine._pending_binds

            # Interaction should get acknowledgement
            interaction.followup.send.assert_called_once()
            reply = interaction.followup.send.call_args[0][0]
            assert "existing session" in reply.lower()

        asyncio.run(_test())

    def test_bind_skips_picker_when_no_sessions(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "my-project"
            project_dir.mkdir()

            engine.launcher.discover_sessions = MagicMock(return_value=[])

            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", str(project_dir), interaction)

            # Should have created tmux window directly
            engine.tmux.create_window.assert_called_once()

            # Should have created binding
            b = engine.session_mgr.get_binding("ch-1")
            assert b is not None

            # No pending binds
            assert "ch-1" not in engine._pending_binds

        asyncio.run(_test())

    def test_bind_resume_button_click(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "my-project"
            project_dir.mkdir()

            sessions = _make_sessions(3)

            # Set up adapter
            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            # Simulate pending bind (as if picker was shown)
            engine._pending_binds["ch-1"] = {
                "path": str(project_dir),
                "window_name": "test-channel",
                "cli": "claude",
                "sessions": sessions,
                "created_at": time.time(),
            }

            # Click resume button for session index 1
            await engine.handle_button_click("ch-1", "user-1", "bind_resume:ch-1:1")

            # Should have created tmux window with resume command
            engine.tmux.create_window.assert_called_once()
            call_kwargs = engine.tmux.create_window.call_args.kwargs
            assert call_kwargs["cwd"] == str(project_dir)
            # The command should include --resume
            assert "resume" in call_kwargs["command"] or sessions[1].session_id in call_kwargs["command"]

            # Binding should exist with session_id
            b = engine.session_mgr.get_binding("ch-1")
            assert b is not None
            assert b.cli_session_id == sessions[1].session_id

            # Pending bind should be cleaned up
            assert "ch-1" not in engine._pending_binds

            # Confirmation message sent
            assert adapter.send_message.call_count >= 1

        asyncio.run(_test())

    def test_bind_new_button_click(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "my-project"
            project_dir.mkdir()

            sessions = _make_sessions(2)

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            engine._pending_binds["ch-1"] = {
                "path": str(project_dir),
                "window_name": "test-channel",
                "cli": "claude",
                "sessions": sessions,
                "created_at": time.time(),
            }

            # Click new session button
            await engine.handle_button_click("ch-1", "user-1", "bind_new:ch-1")

            # Should have created tmux window without resume
            engine.tmux.create_window.assert_called_once()
            call_kwargs = engine.tmux.create_window.call_args.kwargs
            assert call_kwargs["command"] == "claude"  # plain cli, no --resume

            # Binding should exist without session_id
            b = engine.session_mgr.get_binding("ch-1")
            assert b is not None
            assert b.cli_session_id is None

            # Pending bind cleaned up
            assert "ch-1" not in engine._pending_binds

        asyncio.run(_test())

    def test_bind_resume_expired_pending(self, engine):
        async def _test():
            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            # No pending bind exists
            await engine.handle_button_click("ch-1", "user-1", "bind_resume:ch-1:0")

            # Should send expiry message
            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert "expired" in msg.text.lower()

        asyncio.run(_test())

    def test_bind_new_expired_pending(self, engine):
        async def _test():
            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            await engine.handle_button_click("ch-1", "user-1", "bind_new:ch-1")

            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert "expired" in msg.text.lower()

        asyncio.run(_test())

    def test_bind_resume_invalid_index(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "my-project"
            project_dir.mkdir()

            sessions = _make_sessions(2)

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            engine._pending_binds["ch-1"] = {
                "path": str(project_dir),
                "window_name": "test-channel",
                "cli": "claude",
                "sessions": sessions,
                "created_at": time.time(),
            }

            # Click with invalid index
            await engine.handle_button_click("ch-1", "user-1", "bind_resume:ch-1:99")

            # Should send error, not create window
            engine.tmux.create_window.assert_not_called()
            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert "invalid" in msg.text.lower()

        asyncio.run(_test())


# ------------------------------------------------------------------
# Directory Browser tests
# ------------------------------------------------------------------


class TestBuildDirBrowserMessage:
    def test_message_shows_current_dir(self, engine, tmp_path):
        # Create some subdirs
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()

        msg = engine._build_dir_browser_message(str(tmp_path), "ch-1", page=0)

        assert msg.text is not None
        assert "Select Working Directory" in msg.text
        assert str(tmp_path) in msg.text

    def test_message_has_dir_buttons(self, engine, tmp_path):
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        (tmp_path / "gamma").mkdir()

        msg = engine._build_dir_browser_message(str(tmp_path), "ch-1", page=0)

        assert msg.buttons is not None
        # Flatten all buttons
        all_buttons = [btn for row in msg.buttons for btn in row]
        dir_buttons = [b for b in all_buttons if b.callback_data.startswith("browse:")]
        assert len(dir_buttons) == 3
        labels = [b.label for b in dir_buttons]
        assert "alpha" in labels
        assert "beta" in labels
        assert "gamma" in labels

    def test_skips_hidden_dirs(self, engine, tmp_path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "visible").mkdir()

        msg = engine._build_dir_browser_message(str(tmp_path), "ch-1", page=0)

        all_buttons = [btn for row in msg.buttons for btn in row]
        dir_buttons = [b for b in all_buttons if b.callback_data.startswith("browse:")]
        assert len(dir_buttons) == 1
        assert dir_buttons[0].label == "visible"

    def test_pagination_with_many_dirs(self, engine, tmp_path):
        # Create 10 subdirs
        for i in range(10):
            (tmp_path / f"dir{i:02d}").mkdir()

        # Page 0 should show first 6
        msg0 = engine._build_dir_browser_message(str(tmp_path), "ch-1", page=0)
        all_buttons = [btn for row in msg0.buttons for btn in row]
        dir_buttons = [b for b in all_buttons if b.callback_data.startswith("browse:")]
        assert len(dir_buttons) == 6

        # Should have paging buttons
        page_buttons = [b for b in all_buttons if b.callback_data.startswith("browse_page:")]
        assert len(page_buttons) >= 1  # at least Next

        # Page 1 should show remaining 4
        msg1 = engine._build_dir_browser_message(str(tmp_path), "ch-1", page=1)
        all_buttons1 = [btn for row in msg1.buttons for btn in row]
        dir_buttons1 = [b for b in all_buttons1 if b.callback_data.startswith("browse:")]
        assert len(dir_buttons1) == 4

    def test_has_select_and_cancel_buttons(self, engine, tmp_path):
        msg = engine._build_dir_browser_message(str(tmp_path), "ch-1", page=0)

        all_buttons = [btn for row in msg.buttons for btn in row]
        select_buttons = [b for b in all_buttons if b.callback_data.startswith("browse_select:")]
        cancel_buttons = [b for b in all_buttons if b.callback_data.startswith("browse_cancel:")]
        parent_buttons = [b for b in all_buttons if b.callback_data.startswith("browse_parent:")]

        assert len(select_buttons) == 1
        assert len(cancel_buttons) == 1
        assert len(parent_buttons) == 1

    def test_callback_data_within_100_chars(self, engine, tmp_path):
        for i in range(10):
            (tmp_path / f"dir{i:02d}").mkdir()

        long_channel = "1234567890" * 5
        msg = engine._build_dir_browser_message(str(tmp_path), long_channel, page=0)

        for row in msg.buttons:
            for btn in row:
                assert len(btn.callback_data) <= 100, (
                    f"callback_data too long ({len(btn.callback_data)}): {btn.callback_data}"
                )

    def test_empty_dir_still_has_nav(self, engine, tmp_path):
        msg = engine._build_dir_browser_message(str(tmp_path), "ch-1", page=0)

        assert msg.buttons is not None
        # Should still have nav row + action row even with no dirs
        all_buttons = [btn for row in msg.buttons for btn in row]
        assert any(b.callback_data.startswith("browse_parent:") for b in all_buttons)
        assert any(b.callback_data.startswith("browse_select:") for b in all_buttons)


class TestBrowseButtonHandlers:
    def test_browse_enter_subdir(self, engine, tmp_path):
        async def _test():
            (tmp_path / "subA").mkdir()
            (tmp_path / "subB").mkdir()

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            engine._pending_binds["ch-1"] = {
                "type": "browse",
                "current_dir": str(tmp_path),
                "page": 0,
                "window_name": "test-channel",
                "cli": "claude",
            }

            # subA is index 0 in sorted list (subA, subB)
            await engine.handle_button_click("ch-1", "user-1", "browse:ch-1:0:0")

            # State should be updated to the subdir
            assert engine._pending_binds["ch-1"]["current_dir"] == str(tmp_path / "subA")

            # Should send updated browser message
            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert "subA" in msg.text

        asyncio.run(_test())

    def test_browse_parent(self, engine, tmp_path):
        async def _test():
            child = tmp_path / "child"
            child.mkdir()

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            engine._pending_binds["ch-1"] = {
                "type": "browse",
                "current_dir": str(child),
                "page": 0,
                "window_name": "test-channel",
                "cli": "claude",
            }

            await engine.handle_button_click("ch-1", "user-1", "browse_parent:ch-1")

            assert engine._pending_binds["ch-1"]["current_dir"] == str(tmp_path)
            adapter.send_message.assert_called_once()

        asyncio.run(_test())

    def test_browse_parent_blocked_by_allowed_paths(self, engine, tmp_path):
        async def _test():
            child = tmp_path / "child"
            child.mkdir()

            engine.settings.allowed_paths = [str(child)]

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            engine._pending_binds["ch-1"] = {
                "type": "browse",
                "current_dir": str(child),
                "page": 0,
                "window_name": "test-channel",
                "cli": "claude",
            }

            await engine.handle_button_click("ch-1", "user-1", "browse_parent:ch-1")

            # Should stay at child, not go to parent
            assert engine._pending_binds["ch-1"]["current_dir"] == str(child)

            # Should send error message
            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert "allowed" in msg.text.lower()

        asyncio.run(_test())

    def test_browse_page_navigation(self, engine, tmp_path):
        async def _test():
            for i in range(10):
                (tmp_path / f"dir{i:02d}").mkdir()

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            engine._pending_binds["ch-1"] = {
                "type": "browse",
                "current_dir": str(tmp_path),
                "page": 0,
                "window_name": "test-channel",
                "cli": "claude",
            }

            await engine.handle_button_click("ch-1", "user-1", "browse_page:ch-1:1")

            assert engine._pending_binds["ch-1"]["page"] == 1
            adapter.send_message.assert_called_once()

        asyncio.run(_test())

    def test_browse_select_no_sessions(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "my-project"
            project_dir.mkdir()

            engine.launcher.discover_sessions = MagicMock(return_value=[])

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            engine._pending_binds["ch-1"] = {
                "type": "browse",
                "current_dir": str(project_dir),
                "page": 0,
                "window_name": "test-channel",
                "cli": "claude",
            }

            await engine.handle_button_click("ch-1", "user-1", "browse_select:ch-1")

            # Should create tmux window directly
            engine.tmux.create_window.assert_called_once()

            # Should create binding
            b = engine.session_mgr.get_binding("ch-1")
            assert b is not None
            assert b.work_dir == str(project_dir)

            # Pending browse should be cleaned up
            assert "ch-1" not in engine._pending_binds

        asyncio.run(_test())

    def test_browse_select_with_sessions(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "my-project"
            project_dir.mkdir()

            sessions = _make_sessions(2)
            engine.launcher.discover_sessions = MagicMock(return_value=sessions)

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            engine._pending_binds["ch-1"] = {
                "type": "browse",
                "current_dir": str(project_dir),
                "page": 0,
                "window_name": "test-channel",
                "cli": "claude",
            }

            await engine.handle_button_click("ch-1", "user-1", "browse_select:ch-1")

            # Should NOT create tmux window yet (session picker shown)
            engine.tmux.create_window.assert_not_called()

            # Should show session picker
            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert "Resume Session?" in msg.text

            # Pending bind should transition to session picker state
            assert "ch-1" in engine._pending_binds
            assert "sessions" in engine._pending_binds["ch-1"]

        asyncio.run(_test())

    def test_browse_cancel(self, engine):
        async def _test():
            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            engine._pending_binds["ch-1"] = {
                "type": "browse",
                "current_dir": "/tmp",
                "page": 0,
                "window_name": "test-channel",
                "cli": "claude",
            }

            await engine.handle_button_click("ch-1", "user-1", "browse_cancel:ch-1")

            # Pending browse should be cleaned up
            assert "ch-1" not in engine._pending_binds

            # Should send cancellation message
            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert "cancelled" in msg.text.lower()

        asyncio.run(_test())

    def test_browse_expired_state(self, engine):
        async def _test():
            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            # No pending bind
            await engine.handle_button_click("ch-1", "user-1", "browse_select:ch-1")

            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert "expired" in msg.text.lower()

        asyncio.run(_test())

    def test_bind_no_path_with_allowed_paths(self, engine, tmp_path):
        async def _test():
            engine.settings.allowed_paths = [str(tmp_path)]

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", None, interaction)

            # Should start browsing at the first allowed path
            assert "ch-1" in engine._pending_binds
            assert engine._pending_binds["ch-1"]["current_dir"] == str(tmp_path)

        asyncio.run(_test())

    def test_browse_enter_blocked_by_allowed_paths(self, engine, tmp_path):
        async def _test():
            allowed = tmp_path / "allowed"
            allowed.mkdir()
            forbidden = tmp_path / "forbidden"
            forbidden.mkdir()

            engine.settings.allowed_paths = [str(allowed)]

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            # Start browsing at tmp_path (which contains both)
            engine._pending_binds["ch-1"] = {
                "type": "browse",
                "current_dir": str(tmp_path),
                "page": 0,
                "window_name": "test-channel",
                "cli": "claude",
            }

            # Try to enter 'forbidden' — sorted: ['allowed', 'forbidden'], index 1
            await engine.handle_button_click("ch-1", "user-1", "browse:ch-1:0:1")

            # Should remain at tmp_path
            assert engine._pending_binds["ch-1"]["current_dir"] == str(tmp_path)

            # Should send error
            adapter.send_message.assert_called_once()
            msg = adapter.send_message.call_args[0][1]
            assert "allowed" in msg.text.lower()

        asyncio.run(_test())
