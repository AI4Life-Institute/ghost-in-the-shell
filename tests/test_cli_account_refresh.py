"""Tests for ``gits account refresh*`` CLI subcommands.

Covers refresh-install / refresh-uninstall (launchd plist) and the
migrate-default-native dry-run path. The actual refresh() handler is
covered by tests/test_token_refresh.py.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gits.cli_account import (
    LAUNCHD_LABEL,
    cmd_migrate_default_native,
    cmd_refresh_install,
    cmd_refresh_uninstall,
)


# ─── refresh-install ────────────────────────────────────────────────────


def test_refresh_install_macos_writes_plist(tmp_path):
    fake_home = tmp_path
    (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
    fake_gits = "/usr/local/bin/gits"

    with patch("gits.cli_account.Path.home", return_value=fake_home), \
         patch("gits.cli_account.sys.platform", "darwin"), \
         patch("gits.cli_account.shutil.which", return_value=fake_gits), \
         patch("gits.cli_account.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        cmd_refresh_install(argparse.Namespace())

    plist = fake_home / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    assert plist.exists()
    content = plist.read_text()
    assert fake_gits in content
    assert "<key>Label</key>" in content
    assert LAUNCHD_LABEL in content
    assert "<integer>4</integer>" in content  # 04:00 schedule
    assert "<string>account</string>" in content
    assert "<string>refresh</string>" in content
    # bootstrap was called (bootout precedes it, both via launchctl)
    assert any("bootstrap" in str(c.args[0]) for c in run.call_args_list)


def test_refresh_install_missing_gits_bin_fails(tmp_path):
    with patch("gits.cli_account.Path.home", return_value=tmp_path), \
         patch("gits.cli_account.sys.platform", "darwin"), \
         patch("gits.cli_account.shutil.which", return_value=None):
        with pytest.raises(SystemExit) as exc:
            cmd_refresh_install(argparse.Namespace())
    assert exc.value.code == 1


def test_refresh_install_linux_prints_cron_no_files(tmp_path, capsys):
    fake_gits = "/usr/local/bin/gits"
    with patch("gits.cli_account.Path.home", return_value=tmp_path), \
         patch("gits.cli_account.sys.platform", "linux"), \
         patch("gits.cli_account.shutil.which", return_value=fake_gits):
        cmd_refresh_install(argparse.Namespace())
    out = capsys.readouterr().out
    assert "crontab" in out.lower() or "cron" in out.lower()
    assert "gits account refresh" in out
    # No LaunchAgents dir was created
    assert not (tmp_path / "Library" / "LaunchAgents").exists()


def test_refresh_install_bootstrap_failure_exits(tmp_path):
    fake_home = tmp_path
    (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
    fake_gits = "/usr/local/bin/gits"

    def fake_run(cmd, **kw):
        if "bootstrap" in cmd:
            return subprocess.CompletedProcess(args=cmd, returncode=5, stdout="",
                                                stderr="permission denied")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("gits.cli_account.Path.home", return_value=fake_home), \
         patch("gits.cli_account.sys.platform", "darwin"), \
         patch("gits.cli_account.shutil.which", return_value=fake_gits), \
         patch("gits.cli_account.subprocess.run", side_effect=fake_run):
        with pytest.raises(SystemExit) as exc:
            cmd_refresh_install(argparse.Namespace())
    assert exc.value.code == 1


# ─── refresh-uninstall ──────────────────────────────────────────────────


def test_refresh_uninstall_removes_plist(tmp_path):
    fake_home = tmp_path
    plist_dir = fake_home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist = plist_dir / f"{LAUNCHD_LABEL}.plist"
    plist.write_text("<plist></plist>")

    with patch("gits.cli_account.Path.home", return_value=fake_home), \
         patch("gits.cli_account.sys.platform", "darwin"), \
         patch("gits.cli_account.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        cmd_refresh_uninstall(argparse.Namespace())

    assert not plist.exists()


def test_refresh_uninstall_idempotent_when_missing(tmp_path, capsys):
    with patch("gits.cli_account.Path.home", return_value=tmp_path), \
         patch("gits.cli_account.sys.platform", "darwin"):
        cmd_refresh_uninstall(argparse.Namespace())  # no plist installed
    out = capsys.readouterr().out
    assert "nothing to do" in out.lower() or "no refresh" in out.lower()


# ─── migrate-default-native ─────────────────────────────────────────────


def _setup_vault_with_default(tmp_path, default: str):
    """Mock Settings + AccountVault to use tmp_path and a chosen default."""
    from gits.core.account import AccountLayout
    from gits.core.account_vault import AccountEntry, AccountVault

    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    vault.add(AccountEntry(name=default, config_dir=str(layout.account_dir(default))))
    return layout, vault


def test_migrate_dry_run_when_native_newer(tmp_path, capsys, monkeypatch):
    layout, vault = _setup_vault_with_default(tmp_path, "personal")
    native = layout.legacy_claude_dir() / ".credentials.json"
    isolated = layout.account_dir("personal") / ".credentials.json"
    native.parent.mkdir(parents=True, exist_ok=True)
    isolated.parent.mkdir(parents=True, exist_ok=True)
    isolated.write_text("old")
    native.write_text("new")
    # Force native newer
    import os
    os.utime(isolated, (1000, 1000))
    os.utime(native, (2000, 2000))

    settings_mock = MagicMock()
    settings_mock.state_dir = tmp_path / ".gits"
    with patch("gits.cli_account.Settings", return_value=settings_mock), \
         patch("gits.cli_account.AccountLayout", return_value=layout), \
         patch("gits.cli_account.AccountVault", return_value=vault):
        cmd_migrate_default_native(argparse.Namespace(apply=False))
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "native is newer" in out
    # Files unchanged
    assert isolated.read_text() == "old"
    assert native.read_text() == "new"


def test_migrate_no_action_when_identical_mtime(tmp_path, capsys):
    layout, vault = _setup_vault_with_default(tmp_path, "personal")
    native = layout.legacy_claude_dir() / ".credentials.json"
    isolated = layout.account_dir("personal") / ".credentials.json"
    native.parent.mkdir(parents=True, exist_ok=True)
    isolated.parent.mkdir(parents=True, exist_ok=True)
    isolated.write_text("a")
    native.write_text("a")
    import os
    os.utime(isolated, (1500, 1500))
    os.utime(native, (1500, 1500))

    settings_mock = MagicMock()
    settings_mock.state_dir = tmp_path / ".gits"
    with patch("gits.cli_account.Settings", return_value=settings_mock), \
         patch("gits.cli_account.AccountLayout", return_value=layout), \
         patch("gits.cli_account.AccountVault", return_value=vault):
        cmd_migrate_default_native(argparse.Namespace(apply=False))
    out = capsys.readouterr().out
    assert "identical mtimes" in out


def test_migrate_no_default_set(tmp_path, capsys):
    from gits.core.account import AccountLayout
    from gits.core.account_vault import AccountVault

    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    # Empty vault → no default

    settings_mock = MagicMock()
    settings_mock.state_dir = tmp_path / ".gits"
    with patch("gits.cli_account.Settings", return_value=settings_mock), \
         patch("gits.cli_account.AccountLayout", return_value=layout), \
         patch("gits.cli_account.AccountVault", return_value=vault):
        cmd_migrate_default_native(argparse.Namespace(apply=False))
    out = capsys.readouterr().out
    assert "no default" in out.lower()
