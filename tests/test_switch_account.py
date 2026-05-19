"""Tests for ``Engine.switch_account`` (Phase 0.7) including auto-import (D16).

These exercise the per-binding switch primitive directly. Heavy mocking is
used to isolate from tmux / process / filesystem realities — the cp step
runs against real ``tmp_path`` dirs to verify the import flow end-to-end.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gits.core.account import AccountLayout, SwitchResult
from gits.core.account_vault import AccountEntry, AccountVault


@pytest.fixture
def fake_engine(tmp_path):
    """Build a minimal Engine-like object stubbing tmux + launcher + session_mgr.

    We don't construct the real Engine because that pulls in the entire
    dependency tree (tmux server, screenshot, etc.). Instead we stub just
    what ``switch_account`` calls and bind in the real AccountVault /
    AccountLayout / asyncio.Lock plumbing under test.
    """
    from gits.core.engine import Engine

    eng = MagicMock(spec=Engine)
    eng._switch_locks = {}
    eng.account_layout = AccountLayout(home=tmp_path)
    eng.account_vault = AccountVault(tmp_path / ".gits", layout=eng.account_layout)
    eng.session_mgr = MagicMock()
    eng.session_mgr.update_claude_account = AsyncMock()
    eng.session_mgr.mark_respawn_failed = AsyncMock()
    eng.session_mgr.get_binding = MagicMock()
    eng.tmux = MagicMock()
    eng.tmux.send_keys = AsyncMock()
    eng.tmux.send_text = AsyncMock()
    eng.tmux.pane_pid = AsyncMock(return_value=12345)
    eng.tmux.window_exists = AsyncMock(return_value=True)
    eng.launcher = MagicMock()

    # Bind real methods under test
    eng._binding_lock = lambda cid: Engine._binding_lock(eng, cid)
    eng.switch_account = lambda *a, **kw: Engine.switch_account(eng, *a, **kw)
    eng._do_switch = lambda *a, **kw: Engine._do_switch(eng, *a, **kw)
    eng._auto_import_session = lambda *a, **kw: Engine._auto_import_session(eng, *a, **kw)
    # _do_switch's respawn step now goes through _send_relaunch_in_pane
    # (added in commit e3ad188 — stale-CWD guard). Bind the real helper so
    # the call propagates to tmux.send_text; otherwise the spec-mock
    # AsyncMock swallows it and the two respawn-path tests below see
    # neither the send_text await nor the configured side_effect.
    eng._send_relaunch_in_pane = lambda *a, **kw: Engine._send_relaunch_in_pane(eng, *a, **kw)
    eng._ensure_window_alive = AsyncMock(return_value=False)
    return eng


def _make_binding(
    *,
    channel_id="ch-1",
    window_id="@1",
    work_dir="/data/proj",
    cli_session_id="abc-123",
    claude_account=None,
):
    b = MagicMock()
    b.channel_id = channel_id
    b.window_id = window_id
    b.work_dir = work_dir
    b.cli_session_id = cli_session_id
    b.claude_account = claude_account
    b.coding_cli = "claude"
    b.permission_mode = None
    b.window_name = "w"
    return b


# ----------------------------------------------------------------------
# Validation paths (no lock acquired)
# ----------------------------------------------------------------------


def test_switch_unknown_binding(fake_engine):
    fake_engine.session_mgr.get_binding.return_value = None
    result = asyncio.run(fake_engine.switch_account("unknown", "work"))
    assert not result.success
    assert "no binding" in result.error


def test_switch_same_account_is_noop(fake_engine):
    binding = _make_binding(claude_account="work")
    fake_engine.session_mgr.get_binding.return_value = binding
    result = asyncio.run(fake_engine.switch_account("ch-1", "work"))
    assert result.success
    assert result.import_status == "same_account"
    # No tmux/process calls
    fake_engine.tmux.send_keys.assert_not_called()


def test_switch_unknown_target(fake_engine):
    binding = _make_binding(claude_account="alice")
    fake_engine.session_mgr.get_binding.return_value = binding
    # Vault is empty — target won't be found
    result = asyncio.run(fake_engine.switch_account("ch-1", "ghost"))
    assert not result.success
    assert "unknown account" in result.error


# ----------------------------------------------------------------------
# Successful switch (no auto_import)
# ----------------------------------------------------------------------


def test_switch_success_no_import(fake_engine, tmp_path):
    # Register both accounts in the vault
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir=str(tmp_path / ".claude-alice")))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir=str(tmp_path / ".claude-work")))
    binding = _make_binding(claude_account="alice")
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.launcher.build_launch_command.return_value = "CLAUDE_CONFIG_DIR=... claude --resume abc-123"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work", auto_import=False))

    assert result.success
    assert result.previous == "alice"
    assert result.target == "work"
    assert result.import_status == "skipped_no_import"
    # Field updated
    fake_engine.session_mgr.update_claude_account.assert_awaited_once_with("ch-1", "work")
    # respawn occurred
    fake_engine.tmux.send_text.assert_awaited()


# ----------------------------------------------------------------------
# Kill timeout
# ----------------------------------------------------------------------


def test_kill_timeout_aborts(fake_engine, tmp_path):
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    binding = _make_binding(claude_account="alice")
    fake_engine.session_mgr.get_binding.return_value = binding

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[99999])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={99999: False})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work"))

    assert not result.success
    assert "failed to kill" in result.error
    # claude_account was NOT updated
    fake_engine.session_mgr.update_claude_account.assert_not_awaited()


# ----------------------------------------------------------------------
# Respawn failure
# ----------------------------------------------------------------------


def test_respawn_failure_marks_partial(fake_engine, tmp_path):
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    binding = _make_binding(claude_account="alice")
    # The post-update get_binding still returns our binding
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.tmux.send_text.side_effect = OSError("tmux dead")
    fake_engine.launcher.build_launch_command.return_value = "x"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work"))

    assert not result.success
    assert result.respawn_failed
    assert "respawn failed" in result.error
    fake_engine.session_mgr.mark_respawn_failed.assert_awaited()


# ----------------------------------------------------------------------
# Auto-import paths (D16)
# ----------------------------------------------------------------------


def _setup_session_file(layout: AccountLayout, account: str, work_dir: str, session_id: str) -> Path:
    """Create a JSONL file under the account's projects dir; returns its path."""
    projects = layout.projects_dir(account)
    dir_hash = work_dir.replace("/", "-")
    target_dir = projects / dir_hash
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{session_id}.jsonl"
    path.write_text('{"role":"user","content":"hi"}\n')
    return path


def test_auto_import_imports_when_target_missing(fake_engine, tmp_path):
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    work_dir = "/data/proj"
    session_id = "abc-123"
    src_path = _setup_session_file(fake_engine.account_layout, "alice", work_dir, session_id)
    binding = _make_binding(claude_account="alice", work_dir=work_dir, cli_session_id=session_id)
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.launcher.get_session_file.return_value = str(src_path)
    fake_engine.launcher.build_launch_command.return_value = "x"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work", auto_import=True))

    assert result.success
    assert result.import_status == "imported"
    # File copied to target
    target_path = fake_engine.account_layout.projects_dir("work") / "-data-proj" / f"{session_id}.jsonl"
    assert target_path.exists()
    assert target_path.read_text() == src_path.read_text()
    # lastImport recorded
    manifest = fake_engine.account_vault.load()
    assert manifest.last_import is not None
    assert manifest.last_import["session_id"] == session_id
    assert manifest.last_import["from"] == "alice"
    assert manifest.last_import["to"] == "work"


def test_auto_import_preserves_newer_target(fake_engine, tmp_path):
    """Target file newer than source → preserved (mtime-based rule)."""
    import os
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    work_dir = "/data/proj"
    sid = "abc-123"
    src_path = _setup_session_file(fake_engine.account_layout, "alice", work_dir, sid)
    target_path = _setup_session_file(fake_engine.account_layout, "work", work_dir, sid)
    target_path.write_text('{"role":"user","content":"target newer"}\n')
    target_marker = target_path.read_text()

    # Source older than target by 1 hour
    src_mtime = target_path.stat().st_mtime - 3600
    os.utime(src_path, (src_mtime, src_mtime))

    binding = _make_binding(claude_account="alice", work_dir=work_dir, cli_session_id=sid)
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.launcher.get_session_file.return_value = str(src_path)
    fake_engine.launcher.build_launch_command.return_value = "x"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work", auto_import=True))

    assert result.success
    assert result.import_status == "target_existed"
    # Target's history is unchanged because it was newer
    assert target_path.read_text() == target_marker


def test_auto_import_overwrites_older_target(fake_engine, tmp_path):
    """Target file older than source → auto-overwrite (new mtime-based behavior)."""
    import os
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    work_dir = "/data/proj"
    sid = "abc-123"
    src_path = _setup_session_file(fake_engine.account_layout, "alice", work_dir, sid)
    src_path.write_text('{"role":"user","content":"alice newer"}\n')
    target_path = _setup_session_file(fake_engine.account_layout, "work", work_dir, sid)
    target_path.write_text('{"role":"user","content":"work older"}\n')

    # Target older than source by 1 hour
    target_mtime = src_path.stat().st_mtime - 3600
    os.utime(target_path, (target_mtime, target_mtime))

    binding = _make_binding(claude_account="alice", work_dir=work_dir, cli_session_id=sid)
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.launcher.get_session_file.return_value = str(src_path)
    fake_engine.launcher.build_launch_command.return_value = "x"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work", auto_import=True))

    assert result.success
    assert result.import_status == "imported_overwrote"
    # Target now has source's content
    assert target_path.read_text() == '{"role":"user","content":"alice newer"}\n'
    # Backup has been cleaned up
    assert not target_path.with_suffix(target_path.suffix + ".gits-bak").exists()
    # lastImport recorded
    manifest = fake_engine.account_vault.load()
    assert manifest.last_import is not None
    assert manifest.last_import["session_id"] == sid


def test_auto_import_equal_mtime_preserves_target(fake_engine, tmp_path):
    """Target mtime == source mtime → preserve (treat as 'no newer')."""
    import os
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    work_dir = "/data/proj"
    sid = "abc-123"
    src_path = _setup_session_file(fake_engine.account_layout, "alice", work_dir, sid)
    src_path.write_text("alice\n")
    target_path = _setup_session_file(fake_engine.account_layout, "work", work_dir, sid)
    target_path.write_text("work\n")

    same_mtime = src_path.stat().st_mtime
    os.utime(target_path, (same_mtime, same_mtime))

    binding = _make_binding(claude_account="alice", work_dir=work_dir, cli_session_id=sid)
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.launcher.get_session_file.return_value = str(src_path)
    fake_engine.launcher.build_launch_command.return_value = "x"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work", auto_import=True))

    assert result.success
    assert result.import_status == "target_existed"
    assert target_path.read_text() == "work\n"  # unchanged


def test_auto_import_overwrite_failure_preserves_backup(fake_engine, tmp_path, monkeypatch):
    """If shutil.copy2 fails mid-overwrite, target is restored from .gits-bak."""
    import os
    import shutil as _shutil
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    work_dir = "/data/proj"
    sid = "abc-123"
    src_path = _setup_session_file(fake_engine.account_layout, "alice", work_dir, sid)
    src_path.write_text("alice\n")
    target_path = _setup_session_file(fake_engine.account_layout, "work", work_dir, sid)
    target_path.write_text("work-original\n")
    target_mtime = src_path.stat().st_mtime - 3600
    os.utime(target_path, (target_mtime, target_mtime))

    def boom(src, dst, *args, **kwargs):
        raise OSError("disk error")

    monkeypatch.setattr(_shutil, "copy2", boom)

    binding = _make_binding(claude_account="alice", work_dir=work_dir, cli_session_id=sid)
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.launcher.get_session_file.return_value = str(src_path)
    fake_engine.launcher.build_launch_command.return_value = "x"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work", auto_import=True))

    # Best-effort restore: target's previous content is preserved
    assert target_path.exists()
    assert target_path.read_text() == "work-original\n"
    # Switch overall succeeds (copy failure is best-effort, doesn't abort switch)
    assert result.import_status == "no_source"


def test_auto_import_no_source(fake_engine, tmp_path):
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    binding = _make_binding(claude_account="alice", cli_session_id="ghost-id")
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.launcher.get_session_file.return_value = None
    fake_engine.launcher.build_launch_command.return_value = "x"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work", auto_import=True))

    assert result.success
    assert result.import_status == "no_source"


def test_auto_import_no_session(fake_engine, tmp_path):
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    binding = _make_binding(claude_account="alice", cli_session_id=None)
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.launcher.build_launch_command.return_value = "x"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work", auto_import=True))

    assert result.success
    assert result.import_status == "no_session"


def test_cli_path_no_auto_import(fake_engine, tmp_path):
    """auto_import=False (CLI path) skips the cp regardless of file presence."""
    fake_engine.account_vault.add(AccountEntry(name="alice", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="work", config_dir="/y"))
    work_dir = "/data/proj"
    sid = "abc-123"
    src_path = _setup_session_file(fake_engine.account_layout, "alice", work_dir, sid)
    binding = _make_binding(claude_account="alice", work_dir=work_dir, cli_session_id=sid)
    fake_engine.session_mgr.get_binding.return_value = binding
    fake_engine.launcher.build_launch_command.return_value = "x"

    with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
         patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
        result = asyncio.run(fake_engine.switch_account("ch-1", "work", auto_import=False))

    assert result.success
    assert result.import_status == "skipped_no_import"
    target_path = fake_engine.account_layout.projects_dir("work") / "-data-proj" / f"{sid}.jsonl"
    assert not target_path.exists()


# ----------------------------------------------------------------------
# Concurrency: same binding serialises, different bindings parallel
# ----------------------------------------------------------------------


def test_concurrent_different_bindings_run_in_parallel(fake_engine, tmp_path):
    fake_engine.account_vault.add(AccountEntry(name="a", config_dir="/x"))
    fake_engine.account_vault.add(AccountEntry(name="b", config_dir="/y"))
    fake_engine.launcher.build_launch_command.return_value = "x"

    b1 = _make_binding(channel_id="ch-1", claude_account="a", cli_session_id=None)
    b2 = _make_binding(channel_id="ch-2", claude_account="a", cli_session_id=None)

    def get_binding(cid):
        return {"ch-1": b1, "ch-2": b2}.get(cid)

    fake_engine.session_mgr.get_binding.side_effect = get_binding

    async def run_both():
        with patch("gits.utils.process.find_claude_children", AsyncMock(return_value=[])), \
             patch("gits.utils.process.kill_claude_process", AsyncMock(return_value={})):
            r1, r2 = await asyncio.gather(
                fake_engine.switch_account("ch-1", "b"),
                fake_engine.switch_account("ch-2", "b"),
            )
            return r1, r2

    r1, r2 = asyncio.run(run_both())
    assert r1.success and r2.success
    # Locks for distinct bindings exist independently
    assert "ch-1" in fake_engine._switch_locks
    assert "ch-2" in fake_engine._switch_locks
    assert fake_engine._switch_locks["ch-1"] is not fake_engine._switch_locks["ch-2"]
