"""Backward-compatibility tests (Phase 0.13).

When ``~/.gits/accounts/manifest.json`` does not exist, ghost MUST behave
exactly as it did before this change: a single ``~/.claude/`` directory,
no ``CLAUDE_CONFIG_DIR`` injection, no OAuth Usage API calls, no creation
of any ``~/.claude-{name}/`` directories.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gits.core.account import AccountLayout
from gits.core.account_vault import AccountVault
from gits.core.launcher import CodingCLILauncher
from gits.core.session import SessionManager


# ----------------------------------------------------------------------
# Vault uninitialized = vault dormant
# ----------------------------------------------------------------------


def test_vault_not_initialized_when_manifest_missing(tmp_path):
    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    assert not vault.is_initialized()
    assert vault.list() == []
    # Loading is a no-op that returns an empty manifest.
    m = vault.load()
    assert m.default is None
    assert m.accounts == []


def test_no_claude_account_dirs_created_spontaneously(tmp_path):
    """Just instantiating AccountVault / AccountLayout MUST NOT create dirs."""
    layout = AccountLayout(home=tmp_path)
    AccountVault(tmp_path / ".gits", layout=layout)
    # Nothing under home except what tests create
    children = list(tmp_path.iterdir())
    assert children == []


# ----------------------------------------------------------------------
# Launcher: no claude_account → no CLAUDE_CONFIG_DIR injection
# ----------------------------------------------------------------------


def test_launcher_no_account_means_legacy_behavior(tmp_path):
    layout = AccountLayout(home=tmp_path)
    launcher = CodingCLILauncher(
        session_map_path=tmp_path / "session_map.json",
        account_layout=layout,
    )
    # Without claude_account: bare command identical to pre-change behavior.
    assert launcher.build_launch_command(cli="claude", session_id="abc") == "claude --resume abc"
    assert launcher.build_launch_command(cli="claude") == "claude"
    assert launcher.build_launch_command(cli="codex") == "codex"


def test_launcher_resolved_cli_no_account_no_overrides(tmp_path):
    layout = AccountLayout(home=tmp_path)
    launcher = CodingCLILauncher(
        session_map_path=tmp_path / "session_map.json",
        account_layout=layout,
    )
    resolved = launcher.resolve_cli("claude")
    assert resolved.session_path is None
    assert resolved.config_dir is None


# ----------------------------------------------------------------------
# SessionBinding: legacy state.json shape works
# ----------------------------------------------------------------------


def test_session_manager_loads_legacy_state(tmp_path):
    """state.json with no claude_account field on bindings loads fine."""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "bindings": {
            "ch-1": {
                "platform": "discord", "channel_id": "ch-1",
                "window_id": "@1", "window_name": "w", "work_dir": "/d",
                "coding_cli": "claude",
            }
        }
    }))
    mgr = SessionManager(state_dir=tmp_path)
    b = mgr.get_binding("ch-1")
    assert b is not None
    assert b.claude_account is None  # default
    assert b.respawn_failed is False  # default


def test_session_manager_persistence_legacy_diff_clean(tmp_path):
    """A binding with claude_account=None saves WITHOUT the field in state.json."""
    import asyncio

    async def _run():
        mgr = SessionManager(state_dir=tmp_path)
        await mgr.bind(
            platform="discord", channel_id="ch-1",
            window_id="@1", window_name="w", work_dir="/d",
        )
    asyncio.run(_run())
    data = json.loads((tmp_path / "state.json").read_text())
    binding = data["bindings"]["ch-1"]
    assert "claude_account" not in binding
    assert "respawn_failed" not in binding


# ----------------------------------------------------------------------
# Rollback: removing ~/.gits/accounts/ fully reverts to legacy
# ----------------------------------------------------------------------


def test_rollback_remove_accounts_dir(tmp_path):
    """After removing the accounts dir, vault behaves as un-initialized again."""
    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)

    # Set up a realistic state
    from gits.core.account_vault import AccountEntry
    vault.add(AccountEntry(name="alice", config_dir="/x"))
    assert vault.is_initialized()

    # Simulate user wipe
    import shutil
    shutil.rmtree(tmp_path / ".gits" / "accounts")
    # New vault instance reads the missing-state correctly
    vault2 = AccountVault(tmp_path / ".gits", layout=layout)
    assert not vault2.is_initialized()
    assert vault2.list() == []


# ----------------------------------------------------------------------
# JsonlMonitor: defensive handling of pre-account bindings
# ----------------------------------------------------------------------


def test_monitor_handles_legacy_bindings(tmp_path):
    """Pre-account bindings (claude_account=None or missing) use ~/.claude/projects."""
    from gits.core.jsonl_monitor import JsonlMonitor
    from unittest.mock import MagicMock

    session_mgr = MagicMock()
    session_mgr.list_bindings.return_value = []
    monitor = JsonlMonitor(session_mgr=session_mgr, poll_interval=0.05)

    # Legacy projects dir
    legacy = tmp_path / ".claude" / "projects" / "-w"
    legacy.mkdir(parents=True)
    target = legacy / "sess.jsonl"
    target.write_text("{}\n")
    monitor._projects_path = tmp_path / ".claude" / "projects"

    # Binding without claude_account attribute at all (very old state.json)
    binding = MagicMock(spec=["channel_id", "cli_session_id", "work_dir", "coding_cli", "suspended"])
    binding.channel_id = "ch"
    binding.cli_session_id = "sess"
    binding.work_dir = "/w"
    binding.coding_cli = "claude"
    binding.suspended = False
    # Note: spec list doesn't include claude_account → getattr returns the default

    result = monitor._find_jsonl_file(binding)
    assert result == target


# ----------------------------------------------------------------------
# Combined: with no manifest, the engine's account-aware paths fall back
# ----------------------------------------------------------------------


def test_account_layout_paths_for_none_resolve_to_legacy(tmp_path):
    """All path methods fall back to ~/.claude/ when claude_account=None."""
    layout = AccountLayout(home=tmp_path)
    assert layout.projects_dir(None) == tmp_path / ".claude" / "projects"
    assert layout.settings_file(None) == tmp_path / ".claude" / "settings.json"
    assert layout.credentials_file(None) == tmp_path / ".claude" / ".credentials.json"
