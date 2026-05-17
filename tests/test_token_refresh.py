"""Tests for token_refresh — OAuth keepalive for non-default accounts."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from gits.core.account import AccountLayout
from gits.core.account_vault import AccountEntry, AccountVault
from gits.core.token_refresh import (
    REFRESH_PROMPT,
    RefreshResult,
    refresh_account,
    refresh_all_non_default,
)


@pytest.fixture
def layout(tmp_path):
    return AccountLayout(home=tmp_path)


@pytest.fixture
def vault(tmp_path, layout):
    return AccountVault(tmp_path / ".gits", layout=layout)


# ─── refresh_account ────────────────────────────────────────────────────


def test_refresh_account_success(layout):
    completed = subprocess.CompletedProcess(
        args=["claude", "--print", "ping"], returncode=0,
        stdout="pong\n", stderr="",
    )
    with patch("gits.core.token_refresh.subprocess.run", return_value=completed) as run, \
         patch("gits.core.token_refresh.shutil.which", return_value="/usr/bin/claude"):
        result = refresh_account("work", layout)
    assert result.success
    assert result.exit_code == 0
    assert result.account == "work"
    # CLAUDE_CONFIG_DIR was injected for non-default account
    call_kwargs = run.call_args.kwargs
    env = call_kwargs["env"]
    assert env["CLAUDE_CONFIG_DIR"] == str(layout.account_dir("work"))


def test_refresh_account_default_strips_config_dir(layout, monkeypatch):
    # Pre-set a CLAUDE_CONFIG_DIR in env to simulate inherited value
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/should/be/stripped")
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("gits.core.token_refresh.subprocess.run", return_value=completed) as run, \
         patch("gits.core.token_refresh.shutil.which", return_value="/usr/bin/claude"):
        refresh_account("personal", layout, is_default=True)
    env = run.call_args.kwargs["env"]
    assert "CLAUDE_CONFIG_DIR" not in env


def test_refresh_account_uses_correct_command(layout):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("gits.core.token_refresh.subprocess.run", return_value=completed) as run, \
         patch("gits.core.token_refresh.shutil.which", return_value="/usr/bin/claude"):
        refresh_account("work", layout)
    args = run.call_args.args[0]
    assert args == ["/usr/bin/claude", "--print", REFRESH_PROMPT]


def test_refresh_account_no_claude_binary(layout):
    with patch("gits.core.token_refresh.shutil.which", return_value=None):
        result = refresh_account("work", layout)
    assert not result.success
    assert "not found" in result.skipped_reason


def test_refresh_account_nonzero_exit(layout):
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="",
        stderr="Error: refresh token expired\nPlease re-authenticate\n",
    )
    with patch("gits.core.token_refresh.subprocess.run", return_value=completed), \
         patch("gits.core.token_refresh.shutil.which", return_value="/usr/bin/claude"):
        result = refresh_account("work", layout)
    assert not result.success
    assert result.exit_code == 1
    assert "refresh token expired" in result.stderr_tail


def test_refresh_account_timeout(layout):
    def raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=60)

    with patch("gits.core.token_refresh.subprocess.run", side_effect=raise_timeout), \
         patch("gits.core.token_refresh.shutil.which", return_value="/usr/bin/claude"):
        result = refresh_account("work", layout, timeout_s=60)
    assert not result.success
    assert "timeout" in result.stderr_tail


def test_refresh_account_os_error(layout):
    with patch("gits.core.token_refresh.subprocess.run", side_effect=OSError("EACCES")), \
         patch("gits.core.token_refresh.shutil.which", return_value="/usr/bin/claude"):
        result = refresh_account("work", layout)
    assert not result.success
    assert "OSError" in result.stderr_tail


# ─── refresh_all_non_default ────────────────────────────────────────────


def test_refresh_all_skips_default(vault, layout):
    vault.add(AccountEntry(name="personal", config_dir=str(layout.account_dir("personal"))))
    vault.add(AccountEntry(name="work", config_dir=str(layout.account_dir("work"))))
    # personal is auto-default (first add)
    assert vault.load().default == "personal"

    fake = RefreshResult(account="work", success=True, exit_code=0, duration_s=1.2)
    with patch("gits.core.token_refresh.refresh_account", return_value=fake) as ra:
        results = refresh_all_non_default(vault, layout)

    assert len(results) == 1
    assert results[0].account == "work"
    # Only work was invoked, personal (default) was skipped
    assert ra.call_count == 1
    assert ra.call_args.args[0] == "work"


def test_refresh_all_continues_after_failure(vault, layout):
    vault.add(AccountEntry(name="personal", config_dir=str(layout.account_dir("personal"))))
    vault.add(AccountEntry(name="work", config_dir=str(layout.account_dir("work"))))
    vault.add(AccountEntry(name="sandbox", config_dir=str(layout.account_dir("sandbox"))))

    fail = RefreshResult(account="work", success=False, exit_code=1, duration_s=0.5,
                         stderr_tail="boom")
    ok = RefreshResult(account="sandbox", success=True, exit_code=0, duration_s=1.0)

    with patch("gits.core.token_refresh.refresh_account", side_effect=[fail, ok]):
        results = refresh_all_non_default(vault, layout)

    assert len(results) == 2
    assert [r.account for r in results] == ["work", "sandbox"]
    assert results[0].success is False
    assert results[1].success is True


def test_refresh_all_no_non_default_accounts(vault, layout):
    vault.add(AccountEntry(name="personal", config_dir=str(layout.account_dir("personal"))))
    # Only default exists → nothing to refresh
    results = refresh_all_non_default(vault, layout)
    assert results == []


def test_refresh_all_empty_manifest(vault, layout):
    # Brand new vault, no accounts
    results = refresh_all_non_default(vault, layout)
    assert results == []


# ─── TokenRefreshScheduler ──────────────────────────────────────────────


import asyncio
import json
import time

from gits.core.token_refresh import TokenRefreshScheduler


def test_scheduler_persists_last_refresh(tmp_path, vault, layout):
    vault.add(AccountEntry(name="personal", config_dir=str(layout.account_dir("personal"))))
    vault.add(AccountEntry(name="work", config_dir=str(layout.account_dir("work"))))

    state_dir = tmp_path / "state"
    sched = TokenRefreshScheduler(
        vault=vault, layout=layout, state_dir=state_dir,
        interval_s=1, start_delay_s=0,
    )

    fake_result = RefreshResult(account="work", success=True, exit_code=0, duration_s=0.1)
    with patch("gits.core.token_refresh.refresh_account", return_value=fake_result):
        async def run_briefly():
            sched.start()
            # Let the loop fire once
            await asyncio.sleep(0.3)
            await sched.stop()
        asyncio.run(run_briefly())

    state_path = state_dir / "token_refresh_state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert "last_refresh_at" in data
    assert data["last_refresh_at"] > 0


def test_scheduler_skips_when_recent_refresh(tmp_path, vault, layout):
    """If last_refresh_at < interval ago, scheduler waits, doesn't fire immediately."""
    vault.add(AccountEntry(name="personal", config_dir=str(layout.account_dir("personal"))))
    vault.add(AccountEntry(name="work", config_dir=str(layout.account_dir("work"))))

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    # Pretend we refreshed 10 seconds ago
    state_path = state_dir / "token_refresh_state.json"
    state_path.write_text(json.dumps({"last_refresh_at": time.time() - 10}))

    sched = TokenRefreshScheduler(
        vault=vault, layout=layout, state_dir=state_dir,
        interval_s=3600, start_delay_s=60,
    )

    call_count = 0

    def counting_refresh(*a, **kw):
        nonlocal call_count
        call_count += 1
        return RefreshResult(account="work", success=True, exit_code=0, duration_s=0.0)

    with patch("gits.core.token_refresh.refresh_account", side_effect=counting_refresh):
        async def run_briefly():
            sched.start()
            await asyncio.sleep(0.2)  # nowhere near 60s start_delay
            await sched.stop()
        asyncio.run(run_briefly())

    assert call_count == 0  # didn't fire because next refresh is way in the future


def test_scheduler_start_idempotent(tmp_path, vault, layout):
    vault.add(AccountEntry(name="personal", config_dir=str(layout.account_dir("personal"))))
    sched = TokenRefreshScheduler(
        vault=vault, layout=layout, state_dir=tmp_path,
        interval_s=3600, start_delay_s=3600,
    )

    async def run():
        sched.start()
        first_task = sched._task
        sched.start()  # second call should be a no-op
        assert sched._task is first_task
        await sched.stop()

    asyncio.run(run())
