"""Tests for kill_claude_process helper."""

import asyncio
import os
import subprocess
import time

import pytest

from gits.utils.process import (
    _is_alive,
    find_claude_children,
    kill_claude_process,
)


def _spawn_sleeper(duration: float = 30.0, ignore_sigterm: bool = False) -> subprocess.Popen:
    """Spawn a child process that sleeps for *duration* seconds.

    With ``ignore_sigterm=True`` the child traps SIGTERM via shell ``trap`` so
    the helper has to escalate to SIGKILL.
    """
    if ignore_sigterm:
        # Shell traps SIGTERM at startup; even an early SIGTERM is ignored.
        p = subprocess.Popen(
            ["sh", "-c", f'trap "" TERM; sleep {duration} & wait'],
        )
        time.sleep(0.2)  # let trap install
        return p
    return subprocess.Popen(["sleep", str(duration)])


class TestIsAlive:
    def test_running_process(self):
        p = _spawn_sleeper(5)
        try:
            assert _is_alive(p.pid) is True
        finally:
            p.kill()
            p.wait()

    def test_dead_process(self):
        p = _spawn_sleeper(0.1)
        p.wait()
        # zombie reaped via wait(); pid no longer addressable
        assert _is_alive(p.pid) is False

    def test_nonexistent_pid(self):
        assert _is_alive(99999999) is False


class TestKillClaudeProcess:
    def test_empty_input_returns_empty_dict(self):
        result = asyncio.run(kill_claude_process([]))
        assert result == {}

    def test_kills_running_process_with_sigterm(self):
        p = _spawn_sleeper(30)
        start = time.time()
        result = asyncio.run(kill_claude_process([p.pid], grace_seconds=5.0))
        elapsed = time.time() - start
        p.wait(timeout=2)
        assert result == {p.pid: True}
        # Should kill via SIGTERM quickly, well under the 5s grace
        assert elapsed < 2.0

    def test_escalates_to_sigkill_when_sigterm_ignored(self):
        p = _spawn_sleeper(30, ignore_sigterm=True)
        try:
            start = time.time()
            result = asyncio.run(
                kill_claude_process([p.pid], grace_seconds=0.5, reap_after_kill=1.0)
            )
            elapsed = time.time() - start
            p.wait(timeout=2)
            assert result == {p.pid: True}
            # SIGTERM grace + reap window → at least 0.5s, well under 5s
            assert 0.4 < elapsed < 3.0
        finally:
            if p.poll() is None:
                p.kill()

    def test_already_dead_pid_succeeds(self):
        p = _spawn_sleeper(0.05)
        p.wait()
        result = asyncio.run(kill_claude_process([p.pid]))
        assert result == {p.pid: True}

    def test_mixed_pids(self):
        alive = _spawn_sleeper(30)
        dead = _spawn_sleeper(0.05)
        dead.wait()
        try:
            result = asyncio.run(
                kill_claude_process([alive.pid, dead.pid], grace_seconds=1.0)
            )
            assert result[alive.pid] is True
            assert result[dead.pid] is True
        finally:
            if alive.poll() is None:
                alive.kill()
                alive.wait()


class TestFindClaudeChildren:
    def test_no_children(self):
        # PID 1 (init) on macOS would have many children — pick a leaf instead
        leaf = _spawn_sleeper(5)
        try:
            children = asyncio.run(find_claude_children(leaf.pid))
            assert children == []
        finally:
            leaf.kill()
            leaf.wait()

    def test_finds_direct_children(self):
        # Spawn a shell that spawns a child and stays alive
        parent = subprocess.Popen(
            ["sh", "-c", "sleep 10 & wait"],
        )
        try:
            time.sleep(0.3)  # give shell time to fork
            children = asyncio.run(find_claude_children(parent.pid))
            assert len(children) >= 1
        finally:
            parent.kill()
            parent.wait()

    def test_invalid_pid(self):
        result = asyncio.run(find_claude_children(0))
        assert result == []
        result = asyncio.run(find_claude_children(-1))
        assert result == []
