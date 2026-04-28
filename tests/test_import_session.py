"""Tests for ``import_session`` (Phase 0.9 / D13)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from gits.core.account import AccountLayout
from gits.core.account_vault import (
    AccountEntry,
    AccountVault,
    AccountVaultError,
    SessionAlreadyExistsError,
    SessionMultipleSourcesError,
    SessionNotFoundError,
    import_session,
)


@pytest.fixture
def setup_vault(tmp_path):
    """Vault with two accounts already registered."""
    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    vault.add(AccountEntry(name="alice", config_dir=str(tmp_path / ".claude-alice")))
    vault.add(AccountEntry(name="work", config_dir=str(tmp_path / ".claude-work")))
    return layout, vault


def _put_session(layout: AccountLayout, account: str | None, work_dir: str, session_id: str, content: str = "{}") -> Path:
    if account is None:
        projects = layout.legacy_claude_dir() / "projects"
    else:
        projects = layout.account_dir(account) / "projects"
    dir_hash = work_dir.replace("/", "-")
    project_dir = projects / dir_hash
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / f"{session_id}.jsonl"
    path.write_text(content)
    return path


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


def test_import_single_match(setup_vault):
    layout, vault = setup_vault
    src = _put_session(layout, "alice", "/data/p", "abc-123", content='{"hi":1}\n')
    result = import_session("abc-123", to="work", vault=vault, layout=layout)
    assert result.source_account == "alice"
    assert result.target_account == "work"
    assert result.source_path == src
    assert result.target_path == layout.projects_dir("work") / "-data-p" / "abc-123.jsonl"
    assert result.target_path.read_text() == '{"hi":1}\n'
    assert result.line_count == 1
    assert not result.overwrote_target

    # Manifest lastImport recorded
    manifest = vault.load()
    assert manifest.last_import == {
        "at": manifest.last_import["at"],  # ignore exact timestamp
        "session_id": "abc-123",
        "from": "alice",
        "to": "work",
    }


def test_import_creates_dir_hash_subdir(setup_vault):
    layout, vault = setup_vault
    _put_session(layout, "alice", "/some/dir", "s1")
    target_dir = layout.projects_dir("work") / "-some-dir"
    assert not target_dir.exists()
    import_session("s1", to="work", vault=vault, layout=layout)
    assert target_dir.exists()


# ----------------------------------------------------------------------
# Source resolution
# ----------------------------------------------------------------------


def test_import_from_legacy_claude_dir(setup_vault):
    layout, vault = setup_vault
    src = _put_session(layout, None, "/data/p", "leg-1")
    result = import_session("leg-1", to="work", vault=vault, layout=layout)
    assert result.source_account is None
    assert result.source_path == src


def test_import_explicit_from(setup_vault):
    layout, vault = setup_vault
    src1 = _put_session(layout, "alice", "/p", "dup-1", content="alice\n")
    src2 = _put_session(layout, "work", "/p", "dup-1", content="work\n")  # already in target

    # Source needs explicit selection because session_id is in both alice & work
    with pytest.raises(SessionMultipleSourcesError):
        import_session("dup-1", to="work", vault=vault, layout=layout)

    # Explicit source = alice → would conflict with target (work has same id)
    with pytest.raises(SessionAlreadyExistsError):
        import_session("dup-1", to="work", from_="alice", vault=vault, layout=layout)


def test_import_unknown_session(setup_vault):
    layout, vault = setup_vault
    with pytest.raises(SessionNotFoundError):
        import_session("ghost-id", to="work", vault=vault, layout=layout)


def test_import_unknown_target_account(setup_vault):
    layout, vault = setup_vault
    _put_session(layout, "alice", "/p", "abc")
    with pytest.raises(AccountVaultError, match="target account"):
        import_session("abc", to="nonexistent", vault=vault, layout=layout)


def test_import_source_equals_target_no_op(setup_vault):
    layout, vault = setup_vault
    src = _put_session(layout, "work", "/p", "self-1", content="X")
    result = import_session("self-1", to="work", vault=vault, layout=layout)
    assert result.source_path == result.target_path == src
    assert not result.overwrote_target


def test_import_multiple_matches_requires_from(setup_vault):
    layout, vault = setup_vault
    _put_session(layout, "alice", "/p", "dup-2")
    _put_session(layout, None, "/p", "dup-2")  # also in legacy
    with pytest.raises(SessionMultipleSourcesError) as excinfo:
        import_session("dup-2", to="work", vault=vault, layout=layout)
    assert len(excinfo.value.candidates) == 2


# ----------------------------------------------------------------------
# --force overwrite
# ----------------------------------------------------------------------


def test_import_target_exists_no_force_raises(setup_vault):
    layout, vault = setup_vault
    _put_session(layout, "alice", "/p", "x", content="src\n")
    _put_session(layout, "work", "/p", "x", content="target\n")
    with pytest.raises(SessionAlreadyExistsError):
        import_session("x", to="work", from_="alice", vault=vault, layout=layout)
    # Target unchanged
    target = layout.projects_dir("work") / "-p" / "x.jsonl"
    assert target.read_text() == "target\n"


def test_import_force_overwrites(setup_vault):
    layout, vault = setup_vault
    _put_session(layout, "alice", "/p", "x", content="src\n")
    _put_session(layout, "work", "/p", "x", content="target\n")
    result = import_session(
        "x", to="work", from_="alice", vault=vault, layout=layout, force=True
    )
    target = layout.projects_dir("work") / "-p" / "x.jsonl"
    assert target.read_text() == "src\n"
    assert result.overwrote_target
    # Backup cleaned up after successful copy
    assert not target.with_suffix(target.suffix + ".gits-bak").exists()


def test_import_force_failure_preserves_backup(setup_vault, monkeypatch):
    layout, vault = setup_vault
    _put_session(layout, "alice", "/p", "x", content="src\n")
    _put_session(layout, "work", "/p", "x", content="target\n")

    def boom(src, dst, *args, **kwargs):
        raise OSError("disk error")

    import shutil as _shutil
    monkeypatch.setattr(_shutil, "copy2", boom)

    with pytest.raises(OSError):
        import_session(
            "x", to="work", from_="alice", vault=vault, layout=layout, force=True
        )
    target = layout.projects_dir("work") / "-p" / "x.jsonl"
    backup = target.with_suffix(target.suffix + ".gits-bak")
    # Either backup remains, or original was restored. In either case the
    # target's pre-import content is preserved.
    if target.exists():
        assert target.read_text() == "target\n"
    elif backup.exists():
        assert backup.read_text() == "target\n"
    else:
        pytest.fail("both target and backup are gone — data lost")


# ----------------------------------------------------------------------
# File metadata
# ----------------------------------------------------------------------


def test_import_preserves_mtime(setup_vault):
    layout, vault = setup_vault
    src = _put_session(layout, "alice", "/p", "mt-1", content="X\n")
    # Stamp source mtime to a fixed past value
    fixed = time.time() - 3600
    os.utime(src, (fixed, fixed))
    result = import_session("mt-1", to="work", vault=vault, layout=layout)
    assert abs(result.target_path.stat().st_mtime - fixed) < 1.0
