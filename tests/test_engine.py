"""Tests for Core Engine — command handlers with mocked tmux."""

import asyncio
import re
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gits.config import Settings
from gits.core.engine import (
    MODEL_CMD_DESCRIPTION,
    MODEL_HELP,
    Engine,
    _create_worktree,
    _format_age,
    _is_git_repo,
    _is_worktree,
    _remove_worktree,
    _worktree_dirty_files,
)
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
    # Hermetic: skip ~/.gits/config.env and explicitly null fields that may
    # otherwise leak from dev env vars (e.g. ALLOWED_PATHS / GITS_DEFAULT_PATH).
    return Settings(
        _env_file=None,
        gits_dir=tmp_path / ".gits",
        gits_discord_token="test-token",
        tmux_session_name="test-gits",
        coding_cli_command="claude",
        allowed_paths=[],
        bind_root=None,
        gits_default_path=None,
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
    e.tmux.capture_pane_text = AsyncMock(return_value="output\n" + "\u2500" * 66 + "\n❯ \n")
    e.tmux.pane_current_command = AsyncMock(return_value="claude")
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

    def test_bind_no_path_replies_with_help(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", None, interaction)

            # Should reply with usage help
            interaction.followup.send.assert_called_once()
            reply = interaction.followup.send.call_args[0][0]
            assert "provide a path" in reply.lower()

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


class TestBindModelPin:
    """`/bind --model=` → launch command (openspec add-dispatch-model-pin)."""

    def test_fresh_claude_launch_carries_model(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            await engine.handle_bind(
                "ch-1", str(project_dir), FakeInteraction(), model="sonnet"
            )

            cmd = engine.tmux.create_window.call_args.kwargs["command"]
            assert cmd.endswith(" --model sonnet")

        asyncio.run(_test())

    def test_fresh_launch_without_model_unchanged(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            cmd = engine.tmux.create_window.call_args.kwargs["command"]
            assert "--model" not in cmd

        asyncio.run(_test())

    def test_resume_never_injects_model(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            await engine._create_bind(
                "ch-1", str(project_dir), "win", "claude", FakeInteraction(),
                session_id="abc-123", model="sonnet",
            )

            cmd = engine.tmux.create_window.call_args.kwargs["command"]
            assert "--resume" in cmd
            assert "--model" not in cmd

        asyncio.run(_test())

    def test_non_claude_cli_ignores_model(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            await engine._create_bind(
                "ch-1", str(project_dir), "win", "codex", FakeInteraction(),
                model="sonnet",
            )

            cmd = engine.tmux.create_window.call_args.kwargs["command"]
            assert "--model" not in cmd

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
            await engine.handle_esc("ch-1", interaction)

            # Should send Escape once
            calls = engine.tmux.send_keys.call_args_list
            assert len(calls) >= 1
            assert calls[0].args[1] == "Escape"

        asyncio.run(_test())

    def test_stop_not_bound(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_esc("ch-999", interaction)
            reply = interaction.followup.send.call_args[0][0]
            assert "Not bound" in reply

        asyncio.run(_test())


class TestHandleDone:
    def test_done_removes_binding(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())
            assert engine.session_mgr.get_binding("ch-1") is not None

            interaction = FakeInteraction()
            await engine.handle_done("ch-1", interaction)

            engine.tmux.kill_window.assert_called_once()
            assert engine.session_mgr.get_binding("ch-1") is None

        asyncio.run(_test())

    def test_done_reply_before_archive(self, engine, tmp_path):
        """Reply to interaction must happen before archive_thread is called."""
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            call_order = []

            async def _archive(channel_id):
                call_order.append("archive")

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            adapter.archive_thread = _archive
            engine.set_adapter(adapter)

            # Bind parent directly to bypass handle_bind
            await engine.session_mgr.bind(
                platform="discord",
                channel_id="ch-1",
                window_id="@1",
                window_name="parent",
                work_dir=str(project_dir),
                coding_cli="claude",
            )
            # Bind child thread so archive_thread gets called
            await engine.session_mgr.bind(
                platform="discord",
                channel_id="thread-1",
                window_id="@2",
                window_name="child",
                work_dir=str(project_dir),
                coding_cli="claude",
                parent_channel_id="ch-1",
            )

            interaction = FakeInteraction()
            orig_send = interaction.followup.send

            async def _reply(*args, **kwargs):
                call_order.append("reply")
                return await orig_send(*args, **kwargs)

            interaction.followup.send = _reply

            with patch("gits.core.engine._is_worktree", return_value=False):
                await engine.handle_done("ch-1", interaction)

            assert "reply" in call_order
            assert "archive" in call_order
            assert call_order.index("reply") < call_order.index("archive")

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


class TestModelHelpCopy:
    """Anti-staleness guards for the single-source-of-truth /model help."""

    def test_help_includes_default_and_best(self):
        assert "default" in MODEL_HELP
        assert "best" in MODEL_HELP

    def test_help_lists_real_1m_variants_not_invented_ones(self):
        assert "opus[1m]" in MODEL_HELP
        assert "sonnet[1m]" in MODEL_HELP
        # There is no haiku[1m] alias — don't invent it.
        assert "haiku[1m]" not in MODEL_HELP

    def test_help_hardcodes_no_version_numbers(self):
        # No "4.7"/"4.8"-style version numbers — aliases only.
        assert re.search(r"\d+\.\d+", MODEL_HELP) is None

    def test_discord_description_within_limit(self):
        assert len(MODEL_CMD_DESCRIPTION) <= 100


class TestHandleBash:
    def test_bash_sends_to_tmux(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            await engine.handle_bash("ch-1", "git status", interaction)

            # Should send !command to tmux
            engine.tmux.send_text.assert_called()
            call_args = engine.tmux.send_text.call_args
            assert "!git status" in str(call_args)

            reply = interaction.followup.send.call_args[0][0]
            assert "!git status" in reply

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
            # Return idle pane so _wait_for_idle resolves immediately
            engine.tmux.capture_pane_text = AsyncMock(return_value="output\n" + "\u2500" * 66 + "\n\u276f \n")
            # No existing sessions → bind immediately without picker
            engine.launcher.discover_sessions = MagicMock(return_value=[])
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            msg = IncomingMessage(
                platform="discord",
                channel_id="ch-1",
                user_id="user-1",
                text="fix the bug please",
            )
            await engine.handle_message(msg)

            # Yield to the event loop so the drainer task can run
            await asyncio.sleep(0.1)

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

    def test_marks_first_interaction_on_forward(self, engine, tmp_path):
        """handle_message must set first_interaction_at on the binding so
        JsonlMonitor's missing-session warning gate opens (task 50cp7c)."""
        async def _test():
            from gits.adapters.base import IncomingMessage

            # Bind directly via session_mgr (handle_bind has unrelated
            # launcher-mock requirements out of scope for this test).
            await engine.session_mgr.bind(
                platform="discord", channel_id="ch-fi",
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
            )
            binding = engine.session_mgr.get_binding("ch-fi")
            assert binding is not None
            assert binding.first_interaction_at is None  # fresh bind

            msg = IncomingMessage(
                platform="discord", channel_id="ch-fi", user_id="u1", text="hi",
            )
            await engine.handle_message(msg)

            assert isinstance(binding.first_interaction_at, float)

        asyncio.run(_test())

    def test_cli_forward_also_marks_first_interaction(self, engine, tmp_path):
        """/<cmd> forward (handle_cli_forward) likewise opens the gate."""
        async def _test():
            await engine.session_mgr.bind(
                platform="discord", channel_id="ch-cf",
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
            )
            binding = engine.session_mgr.get_binding("ch-cf")
            assert binding is not None
            assert binding.first_interaction_at is None

            await engine.handle_cli_forward("ch-cf", "/model gpt", FakeInteraction())
            assert isinstance(binding.first_interaction_at, float)

        asyncio.run(_test())


# ------------------------------------------------------------------
# Utterance reference relay (task [[utrref]])
# ------------------------------------------------------------------


class TestUtteranceRefRelay:
    """The session must receive a parseable pointer to the message it is
    reading, so an agent can cite it without asking anyone for evidence."""

    @staticmethod
    def _forward(engine, tmp_path, **msg_kwargs) -> str:
        """Bind a channel, forward one message, return the tmux payload."""
        from gits.adapters.base import IncomingMessage

        async def _test() -> str:
            await engine.session_mgr.bind(
                platform=msg_kwargs.get("platform", "discord"),
                channel_id=msg_kwargs["channel_id"],
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
            )
            engine.tmux.send_text.reset_mock()
            await engine.handle_message(IncomingMessage(**msg_kwargs))
            assert engine.tmux.send_text.call_count == 1
            return engine.tmux.send_text.call_args.args[1]

        return asyncio.run(_test())

    def test_forwarded_text_carries_parseable_ref(self, engine, tmp_path):
        """The relayed payload carries a ref that reads back to the facts.

        Parsed with the format's own parser rather than a second hand-written
        regex: the arity of the path grew a guild segment in task [[gldref]],
        and a local regex here would have to be kept in step by hand. The
        two-segment legacy form is pinned in ``tests/test_utterance_ref.py``
        against ``parse_ref`` -- that is where this tripwire moved to, not
        away.
        """
        from gits.core.utterance_ref import parse_ref, permalink

        payload = self._forward(
            engine, tmp_path,
            platform="discord", channel_id="ch-ref", user_id="u-authority",
            text="可以合", guild_id="g-7", message_id="m-42",
        )

        assert "可以合" in payload  # operator's words are untouched
        parsed = parse_ref(payload)
        assert parsed is not None, payload
        assert parsed.platform == "discord"
        assert parsed.guild_id == "g-7"
        assert parsed.channel_id == "ch-ref"
        assert parsed.message_id == "m-42"
        assert parsed.user_id == "u-authority"
        # The point of the guild segment: the relayed ref is now clickable.
        assert permalink(parsed) == "https://discord.com/channels/g-7/ch-ref/m-42"

    def test_missing_message_id_still_forwards_text(self, engine, tmp_path):
        """Delivery beats citability: a message with no id is forwarded
        verbatim and handle_message does not raise (Success story #2)."""
        from gits.adapters.base import IncomingMessage
        from gits.core.engine import _format_utterance_ref

        payload = self._forward(
            engine, tmp_path,
            platform="discord", channel_id="ch-noid", user_id="u1",
            text="ship it", message_id=None,
        )
        # Forwarded, unchanged, with no ref grafted on and no exception
        # escaping handle_message (the helper above would have propagated it).
        assert payload == "ship it"
        assert "[ref:" not in payload
        assert _format_utterance_ref(IncomingMessage(
            platform="discord", channel_id="ch-noid", user_id="u1",
            text="ship it", message_id=None,
        )) is None
        assert _format_utterance_ref(IncomingMessage(
            platform="discord", channel_id="ch-noid", user_id="u1",
            text="ship it", message_id="",
        )) is None

    def test_non_discord_adapter_unaffected(self, engine, tmp_path):
        """Adapters that never set message_id (wechat, desktop) keep working."""
        payload = self._forward(
            engine, tmp_path,
            platform="weixin", channel_id="wxid_abc@im.wechat", user_id="wxid_abc",
            text="hello",
        )
        assert payload == "hello"

    def test_command_payloads_stay_verbatim(self, engine, tmp_path):
        """`!`/`/` payloads are parsed by the CLI — a ref would be an arg."""
        for text in ("!git status", "/compact"):
            payload = self._forward(
                engine, tmp_path,
                platform="discord", channel_id=f"ch-cmd-{text[0]}", user_id="u1",
                text=text, message_id="m-1",
            )
            assert payload == text

    def test_image_only_message_does_not_raise(self, engine, tmp_path):
        from gits.adapters.base import IncomingMessage

        async def _test():
            await engine.session_mgr.bind(
                platform="discord", channel_id="ch-img",
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
            )
            engine.tmux.send_text.reset_mock()
            await engine.handle_message(IncomingMessage(
                platform="discord", channel_id="ch-img", user_id="u1",
                image_paths=["/tmp/a.png"], message_id="m-9",
            ))
            sent = [c.args[1] for c in engine.tmux.send_text.call_args_list]
            assert sent == ["@/tmp/a.png"]  # path stays a clean @-reference

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
                last_message=f"Last message for session {i}",
                first_message=f"First message for session {i}",
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
        # Sessions are now in select_options, not in text
        assert msg.select_options is not None
        labels = [opt.label for opt in msg.select_options]
        # index 0 is "New Session", sessions start at index 1
        assert any("First message for session 0" in lbl for lbl in labels)
        assert any("First message for session 2" in lbl for lbl in labels)

    def test_message_has_select_options(self, engine):
        sessions = _make_sessions(2)
        msg = engine._build_session_picker_message(sessions, "/tmp/project", "ch-1")

        # Session picker now uses a select menu, not buttons
        assert msg.select_options is not None
        # 2 sessions + 1 "New Session" option = 3 total
        assert len(msg.select_options) == 3

        # First option should be "New Session"
        assert "New Session" in msg.select_options[0].label

    def test_callback_data_format(self, engine):
        sessions = _make_sessions(1)
        msg = engine._build_session_picker_message(sessions, "/tmp/project", "ch-1")

        # index 0 = "New Session", index 1 = first session
        resume_opt = msg.select_options[1]
        assert resume_opt.value == f"bind_resume_id:ch-1:{sessions[0].session_id}"

        new_opt = msg.select_options[0]
        assert new_opt.value == "bind_new:ch-1"

    def test_max_sessions_in_select(self, engine):
        sessions = _make_sessions(8)
        msg = engine._build_session_picker_message(sessions, "/tmp/project", "ch-1")

        # All 8 sessions fit on one page (page_size=24), plus "New Session" = 9 options
        assert msg.select_options is not None
        resume_opts = [o for o in msg.select_options if o.value.startswith("bind_resume_id")]
        assert len(resume_opts) == 8

    def test_callback_data_within_100_chars(self, engine):
        sessions = _make_sessions(5)
        # Use a long channel_id
        msg = engine._build_session_picker_message(
            sessions, "/tmp/project", "1234567890" * 5
        )
        for opt in msg.select_options:
            assert len(opt.value) <= 100


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
            assert picker_msg.select_options is not None
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
# /thread tests
# ------------------------------------------------------------------


class TestHandleThread:
    def test_thread_creates_child_binding(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            adapter.create_thread = AsyncMock(return_value="thread-1")
            engine.set_adapter(adapter)

            # Bind parent
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            await engine.handle_thread("ch-1", "fix the login bug", interaction)

            # Thread should be created
            adapter.create_thread.assert_called_once()

            # Child binding should exist
            b = engine.session_mgr.get_binding("thread-1")
            assert b is not None
            assert b.parent_channel_id == "ch-1"
            assert b.work_dir == str(project_dir)

        asyncio.run(_test())

    def test_thread_unbound_channel(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_thread("ch-999", "hello", interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "not bound" in reply.lower()

        asyncio.run(_test())

    def test_thread_title_from_message(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            adapter.create_thread = AsyncMock(return_value="thread-1")
            engine.set_adapter(adapter)

            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())
            await engine.handle_thread("ch-1", "fix the login bug", FakeInteraction())

            # Thread title should be derived from message
            title_arg = adapter.create_thread.call_args[0][1]
            assert "fix the login bug" in title_arg

        asyncio.run(_test())


class TestHandleThreadAuto:
    def test_auto_creates_session_for_bound_parent(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            engine.set_adapter(adapter)

            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            await engine.handle_thread_auto("thread-1", "ch-1", "do something")

            b = engine.session_mgr.get_binding("thread-1")
            assert b is not None
            assert b.parent_channel_id == "ch-1"
            assert b.work_dir == str(project_dir)

        asyncio.run(_test())

    def test_auto_ignores_unbound_parent(self, engine):
        async def _test():
            await engine.handle_thread_auto("thread-1", "ch-999", "hello")

            # No binding should be created
            assert engine.session_mgr.get_binding("thread-1") is None

        asyncio.run(_test())


# ------------------------------------------------------------------
# /fork (worktree) tests
# ------------------------------------------------------------------


class TestHandleFork:
    def test_fork_requires_git_repo(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            with patch("gits.core.engine._is_git_repo", return_value=False):
                await engine.handle_fork("ch-1", "refactor", interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "git repository" in reply.lower()

        asyncio.run(_test())

    def test_fork_creates_worktree(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()
            wt_path = str(project_dir / ".worktrees" / "gits-refactor")

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            adapter.create_thread = AsyncMock(return_value="thread-1")
            engine.set_adapter(adapter)

            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            interaction = FakeInteraction()
            with (
                patch("gits.core.engine._is_git_repo", return_value=True),
                patch("gits.core.engine._create_worktree", return_value=wt_path),
            ):
                await engine.handle_fork("ch-1", "refactor", interaction)

            # Thread and binding should be created
            adapter.create_thread.assert_called_once()
            b = engine.session_mgr.get_binding("thread-1")
            assert b is not None
            assert b.work_dir == wt_path
            assert b.parent_channel_id == "ch-1"

        asyncio.run(_test())

    def test_fork_unbound(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_fork("ch-999", "refactor", interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "not bound" in reply.lower()

        asyncio.run(_test())


# ------------------------------------------------------------------
# Worktree utility tests
# ------------------------------------------------------------------


class TestWorktreeUtils:
    def test_create_and_remove_worktree(self, tmp_path):
        """Integration test: create a real git repo and worktree."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
            capture_output=True,
        )

        assert _is_git_repo(str(repo))

        wt = _create_worktree(str(repo), "test-branch")
        assert wt is not None
        assert Path(wt).exists()
        assert _is_worktree(wt)

        # Clean worktree
        assert _worktree_dirty_files(wt) == []

        # Make it dirty
        (Path(wt) / "newfile.txt").write_text("hello")
        dirty = _worktree_dirty_files(wt)
        assert len(dirty) > 0

        # Remove
        assert _remove_worktree(wt)
        assert not Path(wt).exists()

    def test_is_git_repo_false(self, tmp_path):
        assert not _is_git_repo(str(tmp_path))

    def test_is_worktree_false_for_regular_dir(self, tmp_path):
        assert not _is_worktree(str(tmp_path))

    def test_dirty_files_non_git(self, tmp_path):
        assert _worktree_dirty_files(str(tmp_path)) == []


# ------------------------------------------------------------------
# Kill with children tests
# ------------------------------------------------------------------


class TestHandleDoneWithChildren:
    def test_done_parent_closes_children(self, engine, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            adapter = MagicMock()
            adapter.send_message = AsyncMock(return_value="msg-1")
            adapter.archive_thread = AsyncMock()
            engine.set_adapter(adapter)

            # Bind parent directly
            await engine.session_mgr.bind(
                platform="discord",
                channel_id="ch-1",
                window_id="@1",
                window_name="parent",
                work_dir=str(project_dir),
                coding_cli="claude",
            )
            # Bind child thread
            await engine.session_mgr.bind(
                platform="discord",
                channel_id="thread-1",
                window_id="@2",
                window_name="child",
                work_dir=str(project_dir),
                coding_cli="claude",
                parent_channel_id="ch-1",
            )

            interaction = FakeInteraction()
            with patch("gits.core.engine._is_worktree", return_value=False):
                await engine.handle_done("ch-1", interaction)

            # Both parent and child should be unbound
            assert engine.session_mgr.get_binding("ch-1") is None
            assert engine.session_mgr.get_binding("thread-1") is None

        asyncio.run(_test())


# ------------------------------------------------------------------
# Regression: _kill_single must respect remove_worktree (task 23do0p)
# ------------------------------------------------------------------


def _make_repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Create a real git repo + a worktree off it. Returns (repo, worktree)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init"],
        capture_output=True,
        check=True,
    )
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "wt-branch", str(wt)],
        capture_output=True,
        check=True,
    )
    return repo, wt


class TestKillSingleWorktreeGate:
    """`_kill_single` must only delete the bound worktree when the caller
    explicitly passes `remove_worktree=True`. The previous OR-with-_is_worktree
    silently destroyed user worktrees on thread archive — see task 23do0p."""

    def test_kill_single_default_preserves_worktree(self, engine, tmp_path):
        """Regression: archive-path call (no kwargs) must NOT delete the worktree."""
        async def _test():
            _, wt = _make_repo_with_worktree(tmp_path)
            assert _is_worktree(str(wt))  # sanity: it really is a worktree

            await engine.session_mgr.bind(
                platform="discord",
                channel_id="ch-archive",
                window_id="@1",
                window_name="archived",
                work_dir=str(wt),
                coding_cli="claude",
            )

            await engine._kill_single("ch-archive")

            assert wt.exists(), "worktree was deleted despite remove_worktree=False default"
            assert engine.session_mgr.get_binding("ch-archive") is None

        asyncio.run(_test())

    def test_kill_single_remove_false_preserves_worktree(self, engine, tmp_path):
        """Explicit remove_worktree=False must also preserve the worktree."""
        async def _test():
            _, wt = _make_repo_with_worktree(tmp_path)

            await engine.session_mgr.bind(
                platform="discord",
                channel_id="ch-explicit",
                window_id="@1",
                window_name="explicit",
                work_dir=str(wt),
                coding_cli="claude",
            )

            await engine._kill_single("ch-explicit", remove_worktree=False)

            assert wt.exists()

        asyncio.run(_test())

    def test_kill_single_remove_true_deletes_worktree(self, engine, tmp_path):
        """remove_worktree=True (the /done + kill_wt_yes path) still deletes."""
        async def _test():
            _, wt = _make_repo_with_worktree(tmp_path)

            await engine.session_mgr.bind(
                platform="discord",
                channel_id="ch-done",
                window_id="@1",
                window_name="done",
                work_dir=str(wt),
                coding_cli="claude",
            )

            await engine._kill_single("ch-done", remove_worktree=True)

            assert not wt.exists(), "remove_worktree=True should have deleted the worktree"

        asyncio.run(_test())


# ------------------------------------------------------------------
# E2E tests — full flow with mocked adapter + tmux
# ------------------------------------------------------------------


def _make_git_repo(path: Path) -> None:
    """Create a real git repo with an initial commit."""
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True,
        check=True,
    )


def _make_e2e_engine(settings, tmp_path):
    """Create an engine with mocked tmux + adapter for E2E tests."""
    e = Engine(settings)

    window_counter = {"n": 0}

    def _new_window(**kwargs):
        window_counter["n"] += 1
        wid = f"@{window_counter['n']}"
        return WindowInfo(
            window_id=wid,
            name=kwargs.get("name", "win"),
            cwd=kwargs.get("cwd", str(tmp_path)),
        )

    e.tmux = MagicMock()
    e.tmux.ensure_session = AsyncMock()
    e.tmux.create_window = AsyncMock(side_effect=_new_window)
    e.tmux.kill_window = AsyncMock(return_value=True)
    e.tmux.send_text = AsyncMock()
    e.tmux.send_keys = AsyncMock()
    e.tmux.window_exists = AsyncMock(return_value=True)
    e.tmux.capture_pane_ansi = AsyncMock(return_value="test")
    e.tmux.capture_pane_text = AsyncMock(return_value="output\n" + "\u2500" * 66 + "\n❯ \n")
    e.tmux.pane_current_command = AsyncMock(return_value="claude")

    adapter = MagicMock()
    adapter.send_message = AsyncMock(return_value="msg-1")
    adapter.create_thread = AsyncMock(return_value="auto-thread-id")
    adapter.archive_thread = AsyncMock()
    e.set_adapter(adapter)

    return e, adapter


class TestE2EThread:
    """E2E: /thread creates thread → session starts → initial prompt sent."""

    def test_thread_full_flow(self, settings, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            engine, adapter = _make_e2e_engine(settings, tmp_path)

            # Step 1: Bind parent channel
            interaction = FakeInteraction()
            await engine.handle_bind("ch-1", str(project_dir), interaction)
            assert engine.session_mgr.get_binding("ch-1") is not None

            # Step 2: Create thread with message
            adapter.create_thread.return_value = "thread-42"
            interaction2 = FakeInteraction()
            await engine.handle_thread("ch-1", "fix the login bug", interaction2)

            # Verify: Discord thread created
            adapter.create_thread.assert_called_once()
            thread_title = adapter.create_thread.call_args[0][1]
            assert "fix the login bug" in thread_title

            # Verify: tmux window created for thread (2nd call, 1st was parent)
            assert engine.tmux.create_window.call_count == 2
            thread_win_call = engine.tmux.create_window.call_args
            assert thread_win_call.kwargs["cwd"] == str(project_dir)

            # Verify: child binding exists with correct parent
            child = engine.session_mgr.get_binding("thread-42")
            assert child is not None
            assert child.parent_channel_id == "ch-1"
            assert child.work_dir == str(project_dir)
            assert child.coding_cli == "claude"

            # Verify: confirmation sent to thread
            thread_msgs = [
                c for c in adapter.send_message.call_args_list
                if c[0][0] == "thread-42"
            ]
            assert len(thread_msgs) >= 1
            assert "Session started" in thread_msgs[0][0][1].text

            # Verify: initial prompt will be sent (via async task)
            # Give the background task a moment to fire
            await asyncio.sleep(2.5)
            prompt_calls = [
                c for c in engine.tmux.send_text.call_args_list
                if "fix the login bug" in str(c)
            ]
            assert len(prompt_calls) >= 1

        asyncio.run(_test())

    def test_thread_auto_detect_full_flow(self, settings, tmp_path):
        async def _test():
            project_dir = tmp_path / "proj"
            project_dir.mkdir()

            engine, adapter = _make_e2e_engine(settings, tmp_path)

            # Bind parent
            await engine.handle_bind("ch-1", str(project_dir), FakeInteraction())

            # Simulate Discord thread auto-creation (no slash command)
            await engine.handle_thread_auto(
                "thread-99", "ch-1", "refactor the database layer"
            )

            # Child binding
            child = engine.session_mgr.get_binding("thread-99")
            assert child is not None
            assert child.parent_channel_id == "ch-1"
            assert child.work_dir == str(project_dir)

            # Confirmation in thread
            thread_msgs = [
                c for c in adapter.send_message.call_args_list
                if c[0][0] == "thread-99"
            ]
            assert len(thread_msgs) >= 1
            # Per commit 8823bf0, auto-bind now uses the shared bind-report
            # block (same as manual /bind) — was previously "Auto-session …".
            first = thread_msgs[0][0][1].text
            assert "Bound" in first
            assert "refactor the database layer" in first

            # Wait for initial prompt
            await asyncio.sleep(2.5)
            prompt_calls = [
                c for c in engine.tmux.send_text.call_args_list
                if "refactor the database" in str(c)
            ]
            assert len(prompt_calls) >= 1

        asyncio.run(_test())


class TestE2EForkWorktree:
    """E2E: /fork creates worktree → session in isolated dir → done cleans up."""

    def test_fork_full_flow(self, settings, tmp_path):
        async def _test():
            repo_dir = tmp_path / "repo"
            _make_git_repo(repo_dir)

            engine, adapter = _make_e2e_engine(settings, tmp_path)

            # Bind parent
            await engine.handle_bind("ch-1", str(repo_dir), FakeInteraction())

            # Fork with worktree
            adapter.create_thread.return_value = "fork-thread-1"
            interaction = FakeInteraction()
            await engine.handle_fork("ch-1", "auth-refactor", interaction)

            # Verify: worktree was created on disk
            child = engine.session_mgr.get_binding("fork-thread-1")
            assert child is not None
            assert child.parent_channel_id == "ch-1"
            assert ".worktrees" in child.work_dir
            assert Path(child.work_dir).exists()
            assert _is_worktree(child.work_dir)

            # Verify: tmux window cwd is the worktree path
            fork_win_call = engine.tmux.create_window.call_args_list[-1]
            assert fork_win_call.kwargs["cwd"] == child.work_dir

            # Verify: confirmation mentions worktree
            reply = interaction.followup.send.call_args[0][0]
            assert "worktree" in reply.lower()

            # Step 2: Done — should clean up worktree
            wt_path = child.work_dir
            kill_interaction = FakeInteraction()
            await engine.handle_done("fork-thread-1", kill_interaction)

            # Worktree should be gone
            assert not Path(wt_path).exists()
            assert engine.session_mgr.get_binding("fork-thread-1") is None

        asyncio.run(_test())

    def test_fork_dirty_worktree_asks_confirmation(self, settings, tmp_path):
        async def _test():
            repo_dir = tmp_path / "repo"
            _make_git_repo(repo_dir)

            engine, adapter = _make_e2e_engine(settings, tmp_path)

            # Bind + fork
            await engine.handle_bind("ch-1", str(repo_dir), FakeInteraction())
            adapter.create_thread.return_value = "fork-thread-2"
            await engine.handle_fork("ch-1", "dirty-test", FakeInteraction())

            child = engine.session_mgr.get_binding("fork-thread-2")
            assert child is not None

            # Make worktree dirty
            (Path(child.work_dir) / "dirty.txt").write_text("uncommitted")

            # Try to done — should show confirmation, NOT delete yet
            kill_interaction = FakeInteraction()
            await engine.handle_done("fork-thread-2", kill_interaction)

            # Binding should still exist (waiting for confirmation)
            assert engine.session_mgr.get_binding("fork-thread-2") is not None
            assert Path(child.work_dir).exists()

            # Confirmation message should have been sent with buttons
            confirm_msgs = [
                c for c in adapter.send_message.call_args_list
                if "uncommitted" in str(c).lower() or "dirty" in str(c).lower()
            ]
            assert len(confirm_msgs) >= 1

            # Simulate user clicking "Yes, delete worktree"
            await engine.handle_button_click(
                "fork-thread-2", "user-1", f"kill_wt_yes:fork-thread-2"
            )

            # Now it should be gone
            assert engine.session_mgr.get_binding("fork-thread-2") is None
            assert not Path(child.work_dir).exists()

        asyncio.run(_test())

    def test_done_parent_cascades_to_fork_children(self, settings, tmp_path):
        async def _test():
            repo_dir = tmp_path / "repo"
            _make_git_repo(repo_dir)

            engine, adapter = _make_e2e_engine(settings, tmp_path)

            # Bind parent
            await engine.handle_bind("ch-1", str(repo_dir), FakeInteraction())

            # Create a fork child
            adapter.create_thread.return_value = "fork-child"
            await engine.handle_fork("ch-1", "feature-x", FakeInteraction())

            child = engine.session_mgr.get_binding("fork-child")
            assert child is not None
            wt_path = child.work_dir

            # Done on parent — should cascade
            kill_interaction = FakeInteraction()
            await engine.handle_done("ch-1", kill_interaction)

            # Both gone
            assert engine.session_mgr.get_binding("ch-1") is None
            assert engine.session_mgr.get_binding("fork-child") is None
            assert not Path(wt_path).exists()

        asyncio.run(_test())


# ------------------------------------------------------------------
# _ensure_window_alive tests
# ------------------------------------------------------------------


class TestEnsureWindowAlive:
    def test_window_alive_no_op(self, engine, tmp_path):
        """Window exists → returns False, no create_window call."""
        async def _test():
            await engine.session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
            )
            engine.tmux.window_exists = AsyncMock(return_value=True)
            create_before = engine.tmux.create_window.call_count

            binding = engine.session_mgr.get_binding("ch-1")
            result = await engine._ensure_window_alive(binding)

            assert result is False
            assert engine.tmux.create_window.call_count == create_before

        asyncio.run(_test())

    def test_window_dead_recreates_window(self, engine, tmp_path):
        """Window missing → creates new window and updates binding."""
        async def _test():
            await engine.session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
            )
            new_win = WindowInfo(window_id="@99", name="test-window", cwd=str(tmp_path))
            engine.tmux.window_exists = AsyncMock(return_value=False)
            engine.tmux.create_window = AsyncMock(return_value=new_win)

            binding = engine.session_mgr.get_binding("ch-1")
            result = await engine._ensure_window_alive(binding)

            assert result is True
            assert binding.window_id == "@99"
            engine.tmux.create_window.assert_called_once()
            assert engine.session_mgr.get_binding("ch-1").window_id == "@99"

        asyncio.run(_test())

    def test_window_dead_create_fails_returns_false(self, engine, tmp_path):
        """create_window raises → returns False without crashing."""
        async def _test():
            await engine.session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
            )
            engine.tmux.window_exists = AsyncMock(return_value=False)
            engine.tmux.create_window = AsyncMock(side_effect=RuntimeError("tmux gone"))

            binding = engine.session_mgr.get_binding("ch-1")
            result = await engine._ensure_window_alive(binding)

            assert result is False

        asyncio.run(_test())

    def test_handle_message_triggers_ensure_window(self, engine, tmp_path):
        """handle_message calls _ensure_window_alive before forwarding."""
        async def _test():
            from gits.adapters.base import IncomingMessage

            await engine.session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
            )
            engine.tmux.window_exists = AsyncMock(return_value=True)
            engine.tmux.pane_current_command = AsyncMock(return_value="claude")

            msg = IncomingMessage(
                platform="discord", channel_id="ch-1", user_id="u1", text="hello"
            )
            await engine.handle_message(msg)

            engine.tmux.window_exists.assert_called()

        asyncio.run(_test())


# ------------------------------------------------------------------
# /info session summary alignment tests
# ------------------------------------------------------------------


class TestHandleStatusSessionSummary:
    def test_summary_shown_when_session_found(self, engine, tmp_path):
        """Session summary from discover_sessions appears in /info output."""
        async def _test():
            await engine.session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
                cli_session_id="sess-abc-123",
            )
            fake_session = CLISession(
                session_id="sess-abc-123",
                summary="Add dark mode toggle",
                last_message="make it dark",
                message_count=5,
                file_path="/tmp/fake.jsonl",
                mtime=time.time(),
            )
            engine.launcher.discover_all_sessions = MagicMock(return_value=[fake_session])

            interaction = FakeInteraction()
            await engine.handle_status("ch-1", interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "Add dark mode toggle" in reply
            assert "Session summary" in reply

        asyncio.run(_test())

    def test_summary_absent_when_no_session_match(self, engine, tmp_path):
        """No session match → Session summary line is omitted."""
        async def _test():
            await engine.session_mgr.bind(
                platform="discord", channel_id="ch-1",
                window_id="@1", window_name="test-window",
                work_dir=str(tmp_path), coding_cli="claude",
                cli_session_id="sess-unknown-xyz",
            )
            engine.launcher.discover_all_sessions = MagicMock(return_value=[])

            interaction = FakeInteraction()
            await engine.handle_status("ch-1", interaction)

            reply = interaction.followup.send.call_args[0][0]
            assert "Session summary" not in reply

        asyncio.run(_test())


def _reply_texts(interaction):
    """All text replies sent through an interaction's followup, in order."""
    return [
        c.args[0]
        for c in interaction.followup.send.call_args_list
        if c.args
    ]


def _relaunch_cmd(engine):
    """The relaunch command sent into the pane (the resume send_text), if any."""
    for c in engine.tmux.send_text.call_args_list:
        text = c.args[1] if len(c.args) > 1 else c.kwargs.get("text", "")
        if "--resume" in text or "CLAUDE_CONFIG_DIR" in text:
            return text
    return None


class TestHandleRestart:
    """`/restart` — graceful in-pane resume that re-reads fresh credentials."""

    async def _bind(self, engine, tmp_path, **kwargs):
        defaults = dict(
            platform="discord",
            channel_id="ch-1",
            window_id="@1",
            window_name="test-window",
            work_dir=str(tmp_path),
            coding_cli="claude",
            cli_session_id="sess-abc-1234567890",
        )
        defaults.update(kwargs)
        return await engine.session_mgr.bind(**defaults)

    def test_restart_happy_path(self, engine, tmp_path):
        async def _test():
            await self._bind(engine, tmp_path)
            interaction = FakeInteraction()

            await engine.handle_restart("ch-1", interaction)

            # Graceful interrupt issued.
            engine.tmux.send_keys.assert_any_call("@1", "C-c")
            # Resumes the SAME session id in the same pane.
            cmd = _relaunch_cmd(engine)
            assert cmd is not None
            assert "--resume" in cmd
            assert "sess-abc-1234567890" in cmd
            # Binding unchanged.
            b = engine.session_mgr.get_binding("ch-1")
            assert b.cli_session_id == "sess-abc-1234567890"
            assert b.window_id == "@1"
            # Confirmation sent.
            assert any("Resumed" in t for t in _reply_texts(interaction))

        asyncio.run(_test())

    def test_restart_credential_refresh_account_dir(self, engine, tmp_path):
        """CEO-required: resumed process's credential source is the account dir."""
        async def _test():
            await self._bind(engine, tmp_path, claude_account="sharongoogle")
            interaction = FakeInteraction()

            await engine.handle_restart("ch-1", interaction)

            cmd = _relaunch_cmd(engine)
            assert cmd is not None
            assert "CLAUDE_CONFIG_DIR=" in cmd
            assert ".claude-sharongoogle" in cmd
            # Confirmation names the account so the operator sees the cred source.
            assert any("sharongoogle" in t for t in _reply_texts(interaction))

        asyncio.run(_test())

    def test_restart_default_account_native_path(self, engine, tmp_path):
        """Default account → no CLAUDE_CONFIG_DIR injected (native ~/.claude)."""
        async def _test():
            await self._bind(engine, tmp_path, claude_account=None)
            interaction = FakeInteraction()

            await engine.handle_restart("ch-1", interaction)

            cmd = _relaunch_cmd(engine)
            assert cmd is not None
            assert "CLAUDE_CONFIG_DIR" not in cmd
            assert any("default" in t for t in _reply_texts(interaction))

        asyncio.run(_test())

    def test_restart_preserves_permission_mode(self, engine, tmp_path):
        """A bypassPermissions binding restarts with the YOLO flag re-applied."""
        async def _test():
            await self._bind(
                engine, tmp_path, permission_mode="bypassPermissions"
            )
            interaction = FakeInteraction()

            await engine.handle_restart("ch-1", interaction)

            cmd = _relaunch_cmd(engine)
            assert cmd is not None
            assert "--dangerously-skip-permissions" in cmd
            # Mode preserved on the binding (not dropped).
            assert engine.session_mgr.get_binding("ch-1").permission_mode == (
                "bypassPermissions"
            )

        asyncio.run(_test())

    def test_restart_no_session_id(self, engine, tmp_path):
        """No session id → 'nothing to resume', does NOT start fresh."""
        async def _test():
            await self._bind(engine, tmp_path, cli_session_id=None)
            interaction = FakeInteraction()

            await engine.handle_restart("ch-1", interaction)

            texts = _reply_texts(interaction)
            assert any("Nothing to resume" in t for t in texts)
            assert any("/new" in t for t in texts)
            # No relaunch attempted.
            assert _relaunch_cmd(engine) is None
            engine.tmux.send_keys.assert_not_called()

        asyncio.run(_test())

    def test_restart_not_bound(self, engine):
        async def _test():
            interaction = FakeInteraction()
            await engine.handle_restart("ch-999", interaction)
            assert any(
                "Not bound" in t for t in _reply_texts(interaction)
            )

        asyncio.run(_test())

    def test_restart_graceful_only_no_kill(self, engine, tmp_path):
        """Only send_keys/send_text — never kill_window / kill -9."""
        async def _test():
            await self._bind(engine, tmp_path)
            interaction = FakeInteraction()

            await engine.handle_restart("ch-1", interaction)

            engine.tmux.kill_window.assert_not_called()
            # Graceful quit uses C-c + exit, not a forced kill.
            engine.tmux.send_keys.assert_any_call("@1", "C-c")
            assert any(
                (c.args[1] if len(c.args) > 1 else "") == "exit"
                for c in engine.tmux.send_text.call_args_list
            )

        asyncio.run(_test())

    def test_restart_window_gone(self, engine, tmp_path):
        """Missing pane → clean reply, no exception, dance not attempted."""
        async def _test():
            await self._bind(engine, tmp_path)
            engine.tmux.window_exists = AsyncMock(return_value=False)
            interaction = FakeInteraction()

            await engine.handle_restart("ch-1", interaction)

            assert any(
                "window is gone" in t.lower() for t in _reply_texts(interaction)
            )
            # Did not attempt the graceful quit on a dead pane.
            engine.tmux.send_keys.assert_not_called()

        asyncio.run(_test())

    def test_restart_relaunch_failure_reported(self, engine, tmp_path):
        """Relaunch send failure → clear failure reply, no silent guess."""
        async def _test():
            await self._bind(engine, tmp_path)
            engine.tmux.send_text = AsyncMock(side_effect=RuntimeError("pane dead"))
            interaction = FakeInteraction()

            await engine.handle_restart("ch-1", interaction)

            assert any(
                "Restart failed" in t for t in _reply_texts(interaction)
            )

        asyncio.run(_test())
