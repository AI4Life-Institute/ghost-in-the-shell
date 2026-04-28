"""HealthMonitor must skip recovery while the credential lock is held."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gits.core.health import HealthMonitor


@pytest.fixture
def lock_path(tmp_path):
    p = tmp_path / "switch.lock"
    return p


@pytest.fixture
def health(lock_path):
    return HealthMonitor(
        tmux=MagicMock(
            is_server_alive=AsyncMock(return_value=False),
            is_session_alive=AsyncMock(return_value=False),
        ),
        session_mgr=MagicMock(list_bindings=MagicMock(return_value=[])),
        launcher=MagicMock(),
        check_interval=0.05,
        credential_lock_path=lock_path,
    )


class TestLockAwareness:
    def test_skips_when_lock_held(self, health, lock_path):
        from gits.utils.lock import credential_lock

        recover_called = AsyncMock()
        health._recover_all = recover_called  # type: ignore[method-assign]

        async def run():
            async with credential_lock(lock_path):
                await health._check_health()

        asyncio.run(run())
        recover_called.assert_not_awaited()

    def test_proceeds_when_lock_released(self, health):
        recover_called = AsyncMock()
        health._recover_all = recover_called  # type: ignore[method-assign]

        async def run():
            await health._check_health()

        asyncio.run(run())
        recover_called.assert_awaited()

    def test_no_lock_path_means_always_proceed(self, lock_path):
        h = HealthMonitor(
            tmux=MagicMock(
                is_server_alive=AsyncMock(return_value=False),
                is_session_alive=AsyncMock(return_value=False),
            ),
            session_mgr=MagicMock(list_bindings=MagicMock(return_value=[])),
            launcher=MagicMock(),
            credential_lock_path=None,
        )
        recover_called = AsyncMock()
        h._recover_all = recover_called  # type: ignore[method-assign]
        asyncio.run(h._check_health())
        recover_called.assert_awaited()
