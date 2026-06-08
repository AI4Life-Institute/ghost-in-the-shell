"""Tests for the account ``SessionStart`` hook self-heal (task 3ead61).

Covers:

* ``gits account add`` installs the hook into a fresh account.
* ``gits account fix-hooks`` installs the hook into an account missing it.
* ``gits account fix-hooks`` is a no-op (rc 0, "already installed") when the
  hook is already present.
* ``gits account fix-hooks <name>`` targets only the named account(s).
* a ``_propagate_hooks`` failure surfaces loudly in ``cmd_add`` (stderr +
  non-zero exit).
* the engine boot path now iterates the account vault.
"""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from gits.__main__ import _is_hook_installed
from gits.cli_account import cmd_add, cmd_fix_hooks
from gits.core.account import MARKER_FILENAME, AccountLayout
from gits.core.account_vault import AccountEntry, AccountVault

# ─── fixtures / helpers ─────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A patched home with an empty account vault wired into ``cli_account``."""
    home = tmp_path
    state_dir = home / ".gits"
    layout = AccountLayout(home=home)
    vault = AccountVault(state_dir, layout=layout)

    monkeypatch.setattr(
        "gits.cli_account.Settings", lambda: SimpleNamespace(state_dir=state_dir)
    )
    monkeypatch.setattr("gits.cli_account.AccountLayout", lambda: layout)

    return SimpleNamespace(home=home, state_dir=state_dir, layout=layout, vault=vault)


_HOOKED_SETTINGS = {
    "theme": "dark",
    "hooks": {
        "SessionStart": [
            {"hooks": [{"type": "command", "command": "/x/gits hook", "timeout": 5}]}
        ],
        # A fully-installed account also carries the PreToolUse impl-preflight
        # guard (Ghost task j5pn2w) — fix-hooks treats an account missing it as
        # incomplete and repairs it.
        "PreToolUse": [
            {
                "matcher": "Edit|Write|NotebookEdit|Bash",
                "hooks": [{"type": "command", "command": "/x/gits guard", "timeout": 5}],
            }
        ],
    },
}


def _make_account(env, name: str, *, with_hook: bool = False, broken: bool = False):
    """Register an account in the vault with a config dir + settings.json."""
    acct_dir = env.layout.account_dir(name)
    acct_dir.mkdir(parents=True, exist_ok=True)
    if broken:
        # A settings.json that parses to a JSON array, not an object.
        (acct_dir / "settings.json").write_text("[]")
    elif with_hook:
        (acct_dir / "settings.json").write_text(json.dumps(_HOOKED_SETTINGS))
    else:
        (acct_dir / "settings.json").write_text(json.dumps({"theme": "dark"}))
    env.vault.add(AccountEntry(name=name, config_dir=str(acct_dir)))
    return acct_dir


def _settings_of(env, name: str) -> dict:
    return json.loads((env.layout.account_dir(name) / "settings.json").read_text())


# ─── account add installs the hook ─────────────────────────────────────


def test_account_add_installs_hook_in_fresh_account(env):
    """The pre-loaded-credentials add path ends with a hooked account."""
    name = "alpha"
    acct_dir = env.layout.account_dir(name)
    acct_dir.mkdir(parents=True)
    (acct_dir / MARKER_FILENAME).touch()
    (acct_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"subscriptionType": "max"}})
    )

    cmd_add(argparse.Namespace(name=name, capture_current=False))

    assert env.vault.get(name) is not None
    assert _is_hook_installed(_settings_of(env, name))


# ─── fix-hooks repairs a missing hook ───────────────────────────────────


def test_fix_hooks_installs_into_account_missing_it(env, capsys):
    _make_account(env, "hashook", with_hook=True)
    _make_account(env, "nohook", with_hook=False)

    cmd_fix_hooks(argparse.Namespace(names=[]))

    assert _is_hook_installed(_settings_of(env, "nohook"))
    assert _is_hook_installed(_settings_of(env, "hashook"))
    out = capsys.readouterr().out
    assert "nohook: installed hook in" in out
    assert "hashook: hook already installed in" in out
    assert "[ok] 1 repaired, 1 already-ok" in out


# ─── fix-hooks is a no-op when the hook is present ──────────────────────


def test_fix_hooks_noop_when_hook_present(env, capsys):
    _make_account(env, "hashook", with_hook=True)

    cmd_fix_hooks(argparse.Namespace(names=[]))  # exit 0 → no SystemExit

    out = capsys.readouterr().out
    assert "hashook: hook already installed in" in out
    assert "[ok] 0 repaired, 1 already-ok" in out


# ─── fix-hooks targets only the named account ───────────────────────────


def test_fix_hooks_targets_named_account_only(env, capsys):
    _make_account(env, "first", with_hook=False)
    _make_account(env, "second", with_hook=False)

    cmd_fix_hooks(argparse.Namespace(names=["second"]))

    assert _is_hook_installed(_settings_of(env, "second"))
    assert not _is_hook_installed(_settings_of(env, "first"))  # untouched
    out = capsys.readouterr().out
    assert "second: installed hook in" in out
    assert "first" not in out
    assert "[ok] 1 repaired, 0 already-ok" in out


def test_fix_hooks_unknown_account_errors(env):
    _make_account(env, "real", with_hook=True)

    with pytest.raises(SystemExit) as exc:
        cmd_fix_hooks(argparse.Namespace(names=["ghost"]))
    assert exc.value.code == 1


def test_fix_hooks_reports_install_failure(env, capsys):
    """A corrupt settings.json counts as a failure → non-zero exit."""
    _make_account(env, "broken", broken=True)

    with pytest.raises(SystemExit) as exc:
        cmd_fix_hooks(argparse.Namespace(names=[]))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "broken: [error]" in captured.err
    assert "1 failed" in captured.err


def test_fix_hooks_no_accounts_is_friendly_noop(env, capsys):
    cmd_fix_hooks(argparse.Namespace(names=[]))  # vault empty → no SystemExit
    assert "No accounts configured" in capsys.readouterr().out


# ─── _propagate_hooks failure surfaces in cmd_add ───────────────────────


def test_propagate_hooks_failure_surfaces_in_add(env, capsys, monkeypatch):
    """When hook install fails, cmd_add warns loudly and exits non-zero."""
    name = "beta"
    acct_dir = env.layout.account_dir(name)
    acct_dir.mkdir(parents=True)
    (acct_dir / MARKER_FILENAME).touch()
    (acct_dir / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"subscriptionType": "max"}})
    )

    # Force hook propagation to fail.
    monkeypatch.setattr("gits.__main__._install_hook", lambda **kw: 1)

    with pytest.raises(SystemExit) as exc:
        cmd_add(argparse.Namespace(name=name, capture_current=False))
    assert exc.value.code == 1

    captured = capsys.readouterr()
    # Account was still registered (don't silently drop the auth work).
    assert env.vault.get(name) is not None
    # Failure is loud and points at the repair command.
    assert "fix-hooks" in captured.err
    assert name in captured.err


# ─── engine boot iterates the account vault ─────────────────────────────


def test_engine_boot_iterates_account_vault(env, monkeypatch):
    """Engine._ensure_hooks_installed now installs into each managed account."""
    from gits.core.engine import Engine

    _make_account(env, "acctc", with_hook=False)
    _make_account(env, "acctd", with_hook=True)

    calls: list[str | None] = []
    monkeypatch.setattr(
        "gits.__main__._install_hook",
        lambda config_dir=None, quiet=False: calls.append(config_dir) or 0,
    )
    monkeypatch.setattr("gits.__main__._install_opencode_plugin", lambda: 0)

    stub = SimpleNamespace(
        launcher=SimpleNamespace(_aliases={}),
        account_vault=env.vault,
    )
    Engine._ensure_hooks_installed(stub)

    assert str(env.layout.account_dir("acctc")) in calls
    assert str(env.layout.account_dir("acctd")) in calls
    assert None in calls  # legacy ~/.claude still installed
