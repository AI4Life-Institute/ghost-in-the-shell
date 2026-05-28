"""HealthMonitor._recover_all must rebuild only the tmux session and let
Engine.handle_message recover individual bindings lazily on first inbound
message. See 2026-05-24-3aw0v9 for the incident that motivated this.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gits.core.health import HealthMonitor, RecoveryResult


def _make_binding(channel_id: str, window_id: str = "@1") -> MagicMock:
    b = MagicMock()
    b.channel_id = channel_id
    b.window_id = window_id
    b.window_name = f"win-{channel_id}"
    b.work_dir = "/tmp"
    b.coding_cli = "claude"
    b.cli_session_id = "sess-deadbeef"
    b.suspended = False
    return b


def _make_health(bindings: list, *, ensure_raises: bool = False) -> HealthMonitor:
    tmux = MagicMock()
    tmux.is_server_alive = AsyncMock(return_value=False)
    tmux.is_session_alive = AsyncMock(return_value=False)
    if ensure_raises:
        tmux.ensure_session = AsyncMock(side_effect=RuntimeError("tmux dead"))
    else:
        tmux.ensure_session = AsyncMock(return_value=None)
    tmux.create_window = AsyncMock()
    tmux.send_text = AsyncMock()

    session_mgr = MagicMock()
    session_mgr.list_bindings = MagicMock(return_value=bindings)
    session_mgr.update_window_id = AsyncMock()

    launcher = MagicMock()
    launcher.build_launch_command = MagicMock(return_value="claude --resume X")

    return HealthMonitor(
        tmux=tmux,
        session_mgr=session_mgr,
        launcher=launcher,
        check_interval=0.05,
        max_retries=2,
    )


class TestLazyRecoverAll:
    def test_recover_all_lazy_after_server_death(self):
        """After tmux server death, _recover_all must rebuild only the
        session — no per-binding create_window / send_text."""
        bindings = [_make_binding(f"chan-{i}", f"@{i}") for i in range(10)]
        h = _make_health(bindings)

        result = asyncio.run(h._recover_all())

        h.tmux.ensure_session.assert_awaited_once()
        h.tmux.create_window.assert_not_called()
        h.tmux.send_text.assert_not_called()
        h.launcher.build_launch_command.assert_not_called()

        assert result.total == 0
        assert result.recovered == 0
        assert result.failed == 0
        assert len(result.details) == 1
        assert result.details[0].startswith("Lazy recovery")

    def test_recover_all_does_not_touch_bindings(self):
        """state.json must be untouched — no update_window_id, binding
        objects unmodified by identity."""
        bindings = [_make_binding(f"chan-{i}", f"@{i}") for i in range(5)]
        before_ids = [b.window_id for b in bindings]
        h = _make_health(bindings)

        asyncio.run(h._recover_all())

        h.session_mgr.update_window_id.assert_not_called()
        assert [b.window_id for b in bindings] == before_ids

    def test_callback_fires_with_lazy_result(self):
        """on_recovery callbacks must still fire after lazy recovery so the
        operator-facing Discord notification gets emitted."""
        bindings = [_make_binding("chan-0")]
        h = _make_health(bindings)

        received: list[RecoveryResult] = []

        async def cb(result: RecoveryResult) -> None:
            received.append(result)

        h.on_recovery(cb)
        asyncio.run(h._recover_all())

        assert len(received) == 1
        r = received[0]
        assert r.total == 0
        assert r.recovered == 0
        assert r.failed == 0
        assert r.details[0].startswith("Lazy recovery")

    def test_recover_all_with_zero_bindings(self):
        """Even with no bindings, the tmux session must be rebuilt so a
        future /bind can succeed."""
        h = _make_health([])

        result = asyncio.run(h._recover_all())

        h.tmux.ensure_session.assert_awaited_once()
        assert result.failed == 0
        assert result.details[0].startswith("Lazy recovery")

    def test_recover_all_ensure_session_failure(self):
        """If ensure_session fails every retry, surface failure clearly so
        operator sees a real error (not a silent lazy no-op)."""
        bindings = [_make_binding(f"chan-{i}") for i in range(3)]
        h = _make_health(bindings, ensure_raises=True)

        received: list[RecoveryResult] = []

        async def cb(result: RecoveryResult) -> None:
            received.append(result)

        h.on_recovery(cb)
        result = asyncio.run(h._recover_all())

        assert h.tmux.ensure_session.await_count == h.max_retries
        h.tmux.create_window.assert_not_called()
        assert result.failed == 1
        assert any("Failed to rebuild tmux session" in d for d in result.details)
        # Callback must still fire on failure
        assert len(received) == 1
        assert received[0].failed == 1


@pytest.fixture
def sleepless(monkeypatch):
    """ensure_session retry loop sleeps 2s between attempts — short-circuit
    that in tests so the failure-case test completes promptly."""
    async def _no_sleep(_seconds):
        return None
    monkeypatch.setattr("gits.core.health.asyncio.sleep", _no_sleep)
    yield


class TestEnsureSessionRetryTiming:
    """Smoke check that retry sleeps are mocked correctly for the failure test."""

    def test_failure_path_does_not_block(self, sleepless):
        h = _make_health([], ensure_raises=True)
        # If asyncio.sleep weren't mocked, this would take 2 * max_retries seconds.
        result = asyncio.run(h._recover_all())
        assert result.failed == 1
