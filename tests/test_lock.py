"""Tests for credential_lock."""

import asyncio
import multiprocessing
import os
import time

import pytest

from gits.utils.lock import CredentialLockTimeout, credential_lock, is_locked


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "switch.lock"


class TestCredentialLock:
    def test_acquire_and_release(self, lock_path):
        async def run():
            async with credential_lock(lock_path):
                assert is_locked(lock_path) is True
            assert is_locked(lock_path) is False

        asyncio.run(run())

    def test_lock_file_created_with_0600(self, lock_path):
        async def run():
            async with credential_lock(lock_path):
                pass

        asyncio.run(run())
        assert lock_path.exists()
        mode = lock_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "switch.lock"

        async def run():
            async with credential_lock(path):
                pass

        asyncio.run(run())
        assert path.exists()

    def test_serial_acquires_in_same_process(self, lock_path):
        async def run():
            async with credential_lock(lock_path):
                async with credential_lock(lock_path, timeout=0.5):
                    pass  # second acquire happens after first releases? No—nested.

        # fcntl.flock in same process is recursive within same fd; but we open
        # a new fd each time, so this should block on the second acquire.
        with pytest.raises(CredentialLockTimeout):
            asyncio.run(run())

    def test_timeout_when_held_by_another_process(self, lock_path):
        # Spawn a child process that holds the lock for a while.
        proc = multiprocessing.Process(target=_hold_lock, args=(str(lock_path), 1.0))
        proc.start()
        time.sleep(0.2)  # let child acquire

        async def run():
            async with credential_lock(lock_path, timeout=0.3):
                pass

        with pytest.raises(CredentialLockTimeout):
            asyncio.run(run())

        proc.join(timeout=3)

    def test_acquired_after_other_process_releases(self, lock_path):
        proc = multiprocessing.Process(target=_hold_lock, args=(str(lock_path), 0.4))
        proc.start()
        time.sleep(0.1)

        async def run():
            async with credential_lock(lock_path, timeout=2.0):
                return True

        assert asyncio.run(run()) is True
        proc.join(timeout=2)

    def test_kernel_releases_on_crash(self, lock_path):
        """Ensure SIGKILL on a holder releases the lock."""
        proc = multiprocessing.Process(target=_hold_lock, args=(str(lock_path), 30.0))
        proc.start()
        time.sleep(0.2)
        assert is_locked(lock_path) is True

        os.kill(proc.pid, 9)  # SIGKILL
        proc.join(timeout=2)

        # After holder dies, lock should be released.
        # Give kernel a moment to clean up.
        for _ in range(20):
            if not is_locked(lock_path):
                break
            time.sleep(0.05)
        assert is_locked(lock_path) is False


class TestIsLocked:
    def test_missing_file(self, tmp_path):
        assert is_locked(tmp_path / "nope.lock") is False

    def test_unlocked_file(self, lock_path):
        lock_path.touch()
        assert is_locked(lock_path) is False


def _hold_lock(path_str: str, duration: float) -> None:
    """Helper for multiprocessing: hold the lock for *duration* seconds."""
    import asyncio as _asyncio
    from pathlib import Path as _Path

    from gits.utils.lock import credential_lock as _cl

    async def hold():
        async with _cl(_Path(path_str)):
            await _asyncio.sleep(duration)

    _asyncio.run(hold())
