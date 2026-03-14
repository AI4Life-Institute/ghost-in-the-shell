"""Tests for PaneMonitor — pane polling, output diffing, prompt detection."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gits.core.monitor import PaneMonitor
from gits.core.terminal_parser import PromptInfo


# -- Helpers ---------------------------------------------------------------

CHROME = (
    "\u2500" * 40 + "\n"
    "> \n"
    + "\u2500" * 40 + "\n"
    "  [Opus 4.6] Context: 34%"
)

PERMISSION_PROMPT = (
    "  Bash command\n"
    "    tail -30 /tmp/log\n"
    "\n"
    "  Do you want to proceed?\n"
    "\n"
    "  \u276f 1. Yes\n"
    "    2. Yes, always\n"
    "    3. No\n"
    "\n"
    "  Esc to cancel\n"
)


def _make_pane(body: str, with_chrome: bool = True) -> str:
    """Build a fake pane capture with optional chrome footer."""
    if with_chrome:
        return body + "\n" + CHROME
    return body


@pytest.fixture
def tmux():
    mock = MagicMock()
    mock.capture_pane_text = AsyncMock(return_value="")
    return mock


@pytest.fixture
def session_mgr():
    return MagicMock()


@pytest.fixture
def monitor(tmux, session_mgr):
    return PaneMonitor(tmux=tmux, session_mgr=session_mgr, interval=0.05)


# -- Start / Stop ---------------------------------------------------------


@pytest.mark.asyncio
async def test_start_polling_creates_task(monitor):
    """start_polling should create an asyncio task for the channel."""
    monitor.start_polling("ch1", "@1")
    assert "ch1" in monitor._tasks
    assert not monitor._tasks["ch1"].done()
    monitor.stop_all()


@pytest.mark.asyncio
async def test_stop_polling_cancels_task(monitor):
    """stop_polling should cancel the channel's task and clean up."""
    monitor.start_polling("ch1", "@1")
    monitor.stop_polling("ch1")
    assert "ch1" not in monitor._tasks
    assert "ch1" not in monitor._prev_content


@pytest.mark.asyncio
async def test_stop_all_cancels_everything(monitor):
    """stop_all should cancel all tasks."""
    monitor.start_polling("ch1", "@1")
    monitor.start_polling("ch2", "@2")
    monitor.stop_all()
    assert len(monitor._tasks) == 0
    assert len(monitor._prev_content) == 0


@pytest.mark.asyncio
async def test_restart_polling_replaces_task(monitor):
    """Calling start_polling again should replace the old task."""
    monitor.start_polling("ch1", "@1")
    old_task = monitor._tasks["ch1"]
    monitor.start_polling("ch1", "@2")
    # Task is in cancelling state (cancel() was called)
    assert old_task.cancelling() > 0
    assert "ch1" in monitor._tasks
    assert monitor._tasks["ch1"] is not old_task
    monitor.stop_all()


# -- Output callback -------------------------------------------------------


@pytest.mark.asyncio
async def test_new_output_triggers_callback(monitor, tmux):
    """New lines in the pane should trigger the output callback."""
    output_cb = AsyncMock()
    monitor.on_output(output_cb)

    # First poll — sets baseline, no callback
    tmux.capture_pane_text.return_value = _make_pane("line1\nline2")
    await monitor._poll_once("ch1", "@1")
    output_cb.assert_not_called()

    # Second poll — new content
    tmux.capture_pane_text.return_value = _make_pane("line1\nline2\nline3")
    await monitor._poll_once("ch1", "@1")
    output_cb.assert_called_once()
    args = output_cb.call_args[0]
    assert args[0] == "ch1"
    assert "line3" in args[1]


@pytest.mark.asyncio
async def test_empty_output_not_sent(monitor, tmux):
    """Whitespace-only diff should not trigger callback."""
    output_cb = AsyncMock()
    monitor.on_output(output_cb)

    tmux.capture_pane_text.return_value = _make_pane("line1")
    await monitor._poll_once("ch1", "@1")

    # Only whitespace change
    tmux.capture_pane_text.return_value = _make_pane("line1\n   \n  ")
    await monitor._poll_once("ch1", "@1")
    output_cb.assert_not_called()


@pytest.mark.asyncio
async def test_no_callback_when_content_unchanged(monitor, tmux):
    """Same content should not trigger callback."""
    output_cb = AsyncMock()
    monitor.on_output(output_cb)

    pane = _make_pane("stable content")
    tmux.capture_pane_text.return_value = pane
    await monitor._poll_once("ch1", "@1")
    await monitor._poll_once("ch1", "@1")
    output_cb.assert_not_called()


# -- Prompt callback -------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_triggers_callback(monitor, tmux):
    """Detected interactive prompt should trigger prompt callback."""
    prompt_cb = AsyncMock()
    monitor.on_prompt(prompt_cb)

    tmux.capture_pane_text.return_value = _make_pane(PERMISSION_PROMPT)
    await monitor._poll_once("ch1", "@1")

    prompt_cb.assert_called_once()
    args = prompt_cb.call_args[0]
    assert args[0] == "ch1"  # channel_id
    assert args[1] == "@1"   # window_id
    assert isinstance(args[2], PromptInfo)
    assert len(args[2].options) == 3


@pytest.mark.asyncio
async def test_same_prompt_not_sent_twice(monitor, tmux):
    """Same prompt content should only trigger callback once."""
    prompt_cb = AsyncMock()
    monitor.on_prompt(prompt_cb)

    pane = _make_pane(PERMISSION_PROMPT)
    tmux.capture_pane_text.return_value = pane

    await monitor._poll_once("ch1", "@1")
    await monitor._poll_once("ch1", "@1")

    assert prompt_cb.call_count == 1


@pytest.mark.asyncio
async def test_different_prompt_triggers_again(monitor, tmux):
    """A different prompt should trigger a new callback."""
    prompt_cb = AsyncMock()
    monitor.on_prompt(prompt_cb)

    tmux.capture_pane_text.return_value = _make_pane(PERMISSION_PROMPT)
    await monitor._poll_once("ch1", "@1")

    # Different prompt content
    different_prompt = (
        "  Do you want to make this edit?\n"
        "\n"
        "  \u276f 1. Yes\n"
        "    2. No\n"
        "\n"
        "  Esc to cancel\n"
    )
    tmux.capture_pane_text.return_value = _make_pane(different_prompt)
    await monitor._poll_once("ch1", "@1")

    assert prompt_cb.call_count == 2


@pytest.mark.asyncio
async def test_prompt_clearing_resets_tracking(monitor, tmux):
    """When prompt disappears, tracking is cleared so it can re-trigger."""
    prompt_cb = AsyncMock()
    monitor.on_prompt(prompt_cb)

    # Prompt appears
    tmux.capture_pane_text.return_value = _make_pane(PERMISSION_PROMPT)
    await monitor._poll_once("ch1", "@1")
    assert prompt_cb.call_count == 1

    # Prompt disappears (user answered)
    tmux.capture_pane_text.return_value = _make_pane("Working on it...")
    await monitor._poll_once("ch1", "@1")
    assert "ch1" not in monitor._prev_prompt_key

    # Same prompt reappears — should trigger again
    tmux.capture_pane_text.return_value = _make_pane(PERMISSION_PROMPT)
    await monitor._poll_once("ch1", "@1")
    assert prompt_cb.call_count == 2


# -- Error handling --------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_handles_capture_error(monitor, tmux):
    """Polling should survive tmux capture errors."""
    output_cb = AsyncMock()
    monitor.on_output(output_cb)

    tmux.capture_pane_text.side_effect = RuntimeError("tmux gone")
    # Should not raise
    await monitor._poll_once("ch1", "@1")
    output_cb.assert_not_called()


@pytest.mark.asyncio
async def test_poll_handles_empty_pane(monitor, tmux):
    """Empty pane text should be handled gracefully."""
    output_cb = AsyncMock()
    monitor.on_output(output_cb)

    tmux.capture_pane_text.return_value = ""
    await monitor._poll_once("ch1", "@1")
    output_cb.assert_not_called()


@pytest.mark.asyncio
async def test_output_callback_error_doesnt_crash(monitor, tmux):
    """If output callback raises, polling should continue."""
    output_cb = AsyncMock(side_effect=RuntimeError("callback broken"))
    monitor.on_output(output_cb)

    tmux.capture_pane_text.return_value = _make_pane("line1")
    await monitor._poll_once("ch1", "@1")

    tmux.capture_pane_text.return_value = _make_pane("line1\nline2")
    # Should not raise despite callback error
    await monitor._poll_once("ch1", "@1")


# -- Diff logic ------------------------------------------------------------


def test_compute_new_lines_simple():
    """Basic diff: new lines appended."""
    prev = "line1\nline2"
    curr = "line1\nline2\nline3\nline4"
    result = PaneMonitor._compute_new_lines(prev, curr)
    assert "line3" in result
    assert "line4" in result
    assert "line1" not in result


def test_compute_new_lines_no_overlap():
    """When content completely changes (big scroll), return empty to avoid noise."""
    prev = "old stuff"
    curr = "totally new"
    result = PaneMonitor._compute_new_lines(prev, curr)
    assert result == ""


def test_compute_new_lines_strips_whitespace():
    """Result should have leading/trailing blank lines stripped."""
    prev = "line1"
    curr = "line1\n\n  content  \n\n"
    result = PaneMonitor._compute_new_lines(prev, curr)
    assert result.strip() == "content"
