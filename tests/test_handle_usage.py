"""Tests for /usage handler + pure trim/format helper (task [[s8wq7p]])."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gits.config import Settings
from gits.core.engine import Engine
from gits.core.usage_panel import format_usage_panel, trim_usage_panel


FIXTURE_RAW = Path(__file__).parent / "fixtures" / "usage_panel_raw.txt"


class FakeInteraction:
    def __init__(self):
        self.followup = MagicMock()
        self.followup.send = AsyncMock()
        self.channel = MagicMock()
        self.channel_id = "555"


# ─────────────────────────────────────────────────────────────────────
# Pure trim/format tests — drive the function directly, no engine.
# ─────────────────────────────────────────────────────────────────────


class TestTrimUsagePanel:
    def test_happy_path_drops_banner_and_footer(self):
        raw = FIXTURE_RAW.read_text()
        trimmed = trim_usage_panel(raw)

        # First retained line is the "Current session" header.
        first = trimmed.splitlines()[0]
        assert first.lstrip() == "Current session"

        # Last retained line is the "Usage credits are off …" hint.
        last = trimmed.splitlines()[-1]
        assert "Usage credits are off" in last

        # No spawn banner lines.
        assert "Claude Code v" not in trimmed
        assert "▐▛███▜▌" not in trimmed
        # No tab-bar / zeros block.
        assert "Settings  Status" not in trimmed
        assert "Total cost:" not in trimmed
        # Footer dropped.
        assert "Esc to cancel" not in trimmed

        # No runs of 2+ blank lines.
        assert "\n\n\n" not in trimmed
        # No trailing blank line.
        assert not trimmed.endswith("\n")

    def test_disclaimer_preserved(self):
        raw = FIXTURE_RAW.read_text()
        trimmed = trim_usage_panel(raw)
        # The "local-machine estimate" disclaimer must survive — it's the
        # whole reason the operator needs to see the panel verbatim.
        assert "Approximate, based on local sessions on this machine" in trimmed

    def test_degraded_returns_empty(self):
        # No "Current session" anywhere — e.g. claude rendered a login
        # prompt because creds expired. Trim returns empty; engine maps
        # to the "Capture timed out" error reply.
        raw = "some login prompt\nlogin? y/n\n"
        assert trim_usage_panel(raw) == ""

    def test_no_esc_to_cancel_keeps_through_end(self):
        raw = "header\n\nCurrent session\nbody1\nbody2\n"
        trimmed = trim_usage_panel(raw)
        assert trimmed == "Current session\nbody1\nbody2"

    def test_collapses_blank_runs(self):
        raw = "Current session\nline\n\n\n\nlast\n"
        trimmed = trim_usage_panel(raw)
        assert trimmed == "Current session\nline\n\nlast"


class TestFormatUsagePanel:
    def test_inline_fits_under_2000(self):
        raw = FIXTURE_RAW.read_text()
        result = format_usage_panel(raw, "sharongoogle", datetime(2026, 5, 24, 12, 34))
        assert result.inline is True
        full = f"{result.header}\n```\n{result.body}\n```"
        assert len(full) < 2000
        assert "sharongoogle" in result.header
        assert "12:34" in result.header
        assert "local-machine estimate" in result.header

    def test_oversize_flips_inline_flag(self):
        big_body_raw = "Current session\n" + ("x" * 2500)
        result = format_usage_panel(big_body_raw, "myacct", datetime(2026, 5, 24, 12, 0))
        assert result.inline is False
        assert result.body.startswith("Current session")

    def test_empty_body_signals_capture_failure(self):
        result = format_usage_panel("login prompt", "myacct", datetime(2026, 5, 24, 12, 0))
        assert result.body == ""


# ─────────────────────────────────────────────────────────────────────
# Engine-level handle_usage tests — mocked tmux subprocess + sleep.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def settings(tmp_path):
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
def engine(settings, tmp_path):
    e = Engine(settings)
    e.tmux = MagicMock()
    e.tmux.ensure_session = AsyncMock()
    e.tmux.send_text = AsyncMock()
    e.tmux.send_keys = AsyncMock()
    return e


async def _bind(engine, channel_id: str, tmp_path: Path, claude_account: str | None = None):
    """Create a binding by direct session_mgr poke (cheap; no tmux create)."""
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    await engine.session_mgr.bind(
        platform="discord",
        channel_id=channel_id,
        window_id="@1",
        window_name="test",
        work_dir=str(project),
        coding_cli="claude",
        claude_account=claude_account,
    )


def _patch_engine_io():
    """Patch subprocess.run + asyncio.sleep used inside handle_usage.

    Returns the subprocess.run mock so callers can configure side effects
    and inspect calls.
    """
    raw_capture = FIXTURE_RAW.read_text()

    def _run_side_effect(cmd, **kwargs):
        # capture-pane → return the fixture as stdout
        if "capture-pane" in cmd:
            return MagicMock(stdout=raw_capture, stderr="", returncode=0)
        # everything else (new-session / send-keys / kill-session) is benign
        return MagicMock(stdout="", stderr="", returncode=0)

    run_mock = MagicMock(side_effect=_run_side_effect)
    sleep_mock = AsyncMock()
    return (
        patch("gits.core.engine.subprocess.run", run_mock),
        patch("gits.core.engine.asyncio.sleep", sleep_mock),
        run_mock,
    )


class TestHandleUsage:
    def test_unbound_channel_replies_without_spawning(self, engine):
        async def _run():
            interaction = FakeInteraction()
            with patch("gits.core.engine.subprocess.run") as run_mock:
                await engine.handle_usage("nope-ch", interaction)
            interaction.followup.send.assert_awaited_once()
            assert "Not bound" in interaction.followup.send.await_args.args[0]
            run_mock.assert_not_called()

        asyncio.run(_run())

    def test_default_account_skips_env(self, engine, tmp_path):
        """When binding's account == manifest default, CLAUDE_CONFIG_DIR not set."""
        async def _run():
            # Manifest claims "myacct" is the default → effective_account
            # returns None for that name → engine drops CLAUDE_CONFIG_DIR.
            manifest = MagicMock()
            manifest.default = "myacct"
            engine.account_vault.load = MagicMock(return_value=manifest)
            await _bind(engine, "ch-default", tmp_path, claude_account="myacct")

            run_p, sleep_p, run_mock = _patch_engine_io()
            with run_p, sleep_p:
                await engine.handle_usage("ch-default", FakeInteraction())

            # Find the new-session call and inspect its env kwarg.
            new_session_call = next(
                c for c in run_mock.call_args_list
                if "new-session" in c.args[0]
            )
            env = new_session_call.kwargs["env"]
            assert "CLAUDE_CONFIG_DIR" not in env

        asyncio.run(_run())

    def test_non_default_account_sets_env(self, engine, tmp_path):
        async def _run():
            # Different account name than default → eff != None → env set.
            manifest = MagicMock()
            manifest.default = "other"
            engine.account_vault.load = MagicMock(return_value=manifest)

            # account_dir must exist on disk; redirect layout to tmp_path.
            account_dir = tmp_path / ".claude-myacct"
            account_dir.mkdir()
            engine.account_layout.account_dir = MagicMock(return_value=account_dir)

            await _bind(engine, "ch-nondef", tmp_path, claude_account="myacct")

            run_p, sleep_p, run_mock = _patch_engine_io()
            with run_p, sleep_p:
                await engine.handle_usage("ch-nondef", FakeInteraction())

            new_session_call = next(
                c for c in run_mock.call_args_list
                if "new-session" in c.args[0]
            )
            env = new_session_call.kwargs["env"]
            assert env.get("CLAUDE_CONFIG_DIR") == str(account_dir)

        asyncio.run(_run())

    def test_account_dir_missing_short_circuits(self, engine, tmp_path):
        async def _run():
            manifest = MagicMock()
            manifest.default = "other"
            engine.account_vault.load = MagicMock(return_value=manifest)
            missing = tmp_path / ".claude-ghost"
            engine.account_layout.account_dir = MagicMock(return_value=missing)
            await _bind(engine, "ch-miss", tmp_path, claude_account="ghost")

            interaction = FakeInteraction()
            with patch("gits.core.engine.subprocess.run") as run_mock:
                await engine.handle_usage("ch-miss", interaction)

            interaction.followup.send.assert_awaited_once()
            msg = interaction.followup.send.await_args.args[0]
            assert "ghost" in msg and "not configured locally" in msg
            run_mock.assert_not_called()

        asyncio.run(_run())

    def test_cleanup_on_exception(self, engine, tmp_path):
        """If send-keys raises, kill-session must still run in finally."""
        async def _run():
            manifest = MagicMock()
            manifest.default = "other"
            engine.account_vault.load = MagicMock(return_value=manifest)
            account_dir = tmp_path / ".claude-myacct"
            account_dir.mkdir()
            engine.account_layout.account_dir = MagicMock(return_value=account_dir)
            await _bind(engine, "ch-boom", tmp_path, claude_account="myacct")

            def _side_effect(cmd, **kwargs):
                if "send-keys" in cmd:
                    raise RuntimeError("simulated tmux failure")
                return MagicMock(stdout="", stderr="", returncode=0)

            run_mock = MagicMock(side_effect=_side_effect)
            sleep_mock = AsyncMock()
            with patch("gits.core.engine.subprocess.run", run_mock), \
                 patch("gits.core.engine.asyncio.sleep", sleep_mock):
                with pytest.raises(RuntimeError):
                    await engine.handle_usage("ch-boom", FakeInteraction())

            # kill-session was still issued in the finally block.
            assert any(
                "kill-session" in c.args[0] for c in run_mock.call_args_list
            ), f"kill-session missing from calls: {run_mock.call_args_list}"

        asyncio.run(_run())

    def test_capture_timeout_reply_when_panel_missing(self, engine, tmp_path):
        """Capture stdout that doesn't contain 'Current session' → timeout reply."""
        async def _run():
            manifest = MagicMock()
            manifest.default = "other"
            engine.account_vault.load = MagicMock(return_value=manifest)
            account_dir = tmp_path / ".claude-myacct"
            account_dir.mkdir()
            engine.account_layout.account_dir = MagicMock(return_value=account_dir)
            await _bind(engine, "ch-login", tmp_path, claude_account="myacct")

            def _side_effect(cmd, **kwargs):
                if "capture-pane" in cmd:
                    return MagicMock(stdout="login prompt\n> ", stderr="", returncode=0)
                return MagicMock(stdout="", stderr="", returncode=0)

            interaction = FakeInteraction()
            with patch("gits.core.engine.subprocess.run", side_effect=_side_effect), \
                 patch("gits.core.engine.asyncio.sleep", AsyncMock()):
                await engine.handle_usage("ch-login", interaction)

            msg = interaction.followup.send.await_args.args[0]
            assert "Capture timed out" in msg
            assert "myacct" in msg

        asyncio.run(_run())

    def test_happy_path_posts_inline_code_fence(self, engine, tmp_path):
        async def _run():
            manifest = MagicMock()
            manifest.default = "other"
            engine.account_vault.load = MagicMock(return_value=manifest)
            account_dir = tmp_path / ".claude-myacct"
            account_dir.mkdir()
            engine.account_layout.account_dir = MagicMock(return_value=account_dir)
            await _bind(engine, "ch-ok", tmp_path, claude_account="myacct")

            interaction = FakeInteraction()
            run_p, sleep_p, _ = _patch_engine_io()
            with run_p, sleep_p:
                await engine.handle_usage("ch-ok", interaction)

            msg = interaction.followup.send.await_args.args[0]
            assert "/usage" in msg and "myacct" in msg
            assert "```" in msg
            assert "Current session" in msg
            assert "Approximate, based on local sessions" in msg

        asyncio.run(_run())

    def test_session_name_includes_channel_and_hex(self, engine, tmp_path):
        async def _run():
            manifest = MagicMock()
            manifest.default = "other"
            engine.account_vault.load = MagicMock(return_value=manifest)
            account_dir = tmp_path / ".claude-myacct"
            account_dir.mkdir()
            engine.account_layout.account_dir = MagicMock(return_value=account_dir)
            await _bind(engine, "ch-name", tmp_path, claude_account="myacct")

            run_p, sleep_p, run_mock = _patch_engine_io()
            with run_p, sleep_p:
                await engine.handle_usage("ch-name", FakeInteraction())

            new_session = next(
                c for c in run_mock.call_args_list
                if "new-session" in c.args[0]
            )
            name = new_session.args[0][new_session.args[0].index("-s") + 1]
            assert name.startswith("gits-usage-ch-name-")
            # 6 hex chars after the channel id.
            suffix = name.rsplit("-", 1)[-1]
            assert len(suffix) == 6
            int(suffix, 16)  # valid hex

        asyncio.run(_run())
