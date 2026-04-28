"""Tests for ``gits.core.account.AccountLayout`` and related helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gits.core.account import (
    MARKER_FILENAME,
    SHARED_SUBITEMS,
    AccountLayout,
    AccountLayoutError,
    is_ghost_managed,
    validate_account_name,
)


# ----------------------------------------------------------------------
# validate_account_name
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a", "abc", "abc-def", "abc_def", "a1", "personal", "work-2", "x" * 32])
def test_validate_name_accepts_valid(name: str) -> None:
    validate_account_name(name)  # should not raise


@pytest.mark.parametrize(
    "name",
    [
        "",
        "X",  # uppercase
        "Abc",  # uppercase
        "1abc",  # OK actually — first char alphanumeric incl digit
        "-abc",  # leading hyphen
        "_abc",  # leading underscore
        "abc!",  # bad char
        "abc def",  # space
        "x" * 33,  # too long
        "../etc",
    ],
)
def test_validate_name_rejects_invalid(name: str) -> None:
    if name == "1abc":
        # actually valid per our spec; remove from the parametrize? No — let's
        # assert it's accepted so the parametrize stays expressive. Fix below.
        validate_account_name(name)
        return
    with pytest.raises(ValueError):
        validate_account_name(name)


def test_validate_name_rejects_reserved() -> None:
    with pytest.raises(ValueError, match="reserved"):
        validate_account_name("shared")


def test_validate_name_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        validate_account_name(123)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Path resolution
# ----------------------------------------------------------------------


def test_legacy_claude_dir(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    assert layout.legacy_claude_dir() == tmp_path / ".claude"


def test_account_dir(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    assert layout.account_dir("work") == tmp_path / ".claude-work"


def test_account_dir_invalid_name_raises(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    with pytest.raises(ValueError):
        layout.account_dir("BAD")


def test_marker_path(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    assert layout.marker_path("work") == tmp_path / ".claude-work" / MARKER_FILENAME


def test_projects_dir_none_resolves_legacy(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    assert layout.projects_dir(None) == tmp_path / ".claude" / "projects"


def test_projects_dir_with_account(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    assert layout.projects_dir("work") == tmp_path / ".claude-work" / "projects"


def test_settings_and_credentials(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    assert layout.settings_file(None) == tmp_path / ".claude" / "settings.json"
    assert layout.settings_file("x") == tmp_path / ".claude-x" / "settings.json"
    assert layout.credentials_file(None) == tmp_path / ".claude" / ".credentials.json"
    assert layout.credentials_file("x") == tmp_path / ".claude-x" / ".credentials.json"


def test_all_active_projects_dirs(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    paths = layout.all_active_projects_dirs(["work", "personal"])
    assert paths == [
        tmp_path / ".claude" / "projects",
        tmp_path / ".claude-work" / "projects",
        tmp_path / ".claude-personal" / "projects",
    ]


def test_all_active_projects_dirs_skip_legacy(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    paths = layout.all_active_projects_dirs(["work"], include_legacy=False)
    assert paths == [tmp_path / ".claude-work" / "projects"]


# ----------------------------------------------------------------------
# is_ghost_managed
# ----------------------------------------------------------------------


def test_is_ghost_managed_true_when_marker_present(tmp_path: Path) -> None:
    d = tmp_path / ".claude-work"
    d.mkdir()
    (d / MARKER_FILENAME).touch()
    assert is_ghost_managed(d)


def test_is_ghost_managed_false_when_marker_absent(tmp_path: Path) -> None:
    d = tmp_path / ".claude-work"
    d.mkdir()
    assert not is_ghost_managed(d)


def test_is_ghost_managed_false_when_dir_missing(tmp_path: Path) -> None:
    assert not is_ghost_managed(tmp_path / ".claude-missing")


# ----------------------------------------------------------------------
# create_account_dir
# ----------------------------------------------------------------------


def test_create_account_dir_basic(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    target = layout.create_account_dir("work")
    assert target == tmp_path / ".claude-work"
    assert target.is_dir()
    marker = target / MARKER_FILENAME
    assert marker.is_file()
    # 0700 dir mode + 0644 marker mode (ignore symlinked filesystems where
    # mode bits may differ; just check it's not world-writable)
    st_dir = target.stat()
    assert (st_dir.st_mode & 0o077) == 0
    st_marker = marker.stat()
    assert (st_marker.st_mode & 0o777) == 0o644


def test_create_account_dir_capture_current(tmp_path: Path) -> None:
    # Build a fake ~/.claude/ source
    src = tmp_path / ".claude"
    src.mkdir()
    (src / ".credentials.json").write_text('{"claudeAiOauth": {"accessToken": "X"}}')
    (src / "settings.json").write_text("{}")
    projects = src / "projects" / "abc-hash"
    projects.mkdir(parents=True)
    (projects / "session1.jsonl").write_text("line\n")

    layout = AccountLayout(home=tmp_path)
    target = layout.create_account_dir("personal", capture_current=True)

    assert (target / ".credentials.json").read_text() == '{"claudeAiOauth": {"accessToken": "X"}}'
    assert (target / "settings.json").read_text() == "{}"
    assert (target / "projects" / "abc-hash" / "session1.jsonl").read_text() == "line\n"
    assert (target / MARKER_FILENAME).is_file()


def test_create_account_dir_refuses_existing_non_managed(tmp_path: Path) -> None:
    pre_existing = tmp_path / ".claude-foo"
    pre_existing.mkdir()
    (pre_existing / "some-data").write_text("data")

    layout = AccountLayout(home=tmp_path)
    with pytest.raises(AccountLayoutError, match="not ghost-managed"):
        layout.create_account_dir("foo")
    # Existing data untouched
    assert (pre_existing / "some-data").read_text() == "data"


def test_create_account_dir_refuses_existing_managed(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    layout.create_account_dir("foo")
    with pytest.raises(AccountLayoutError, match="already exists"):
        layout.create_account_dir("foo")


def test_create_account_dir_invalid_name(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    with pytest.raises(ValueError):
        layout.create_account_dir("BAD")


def test_create_account_dir_rejects_reserved(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        layout.create_account_dir("shared")


def test_capture_current_no_source_cleans_up(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    # No ~/.claude/ created in tmp_path
    with pytest.raises(AccountLayoutError, match="source"):
        layout.create_account_dir("work", capture_current=True)
    # Partial dir was removed
    assert not (tmp_path / ".claude-work").exists()


def test_capture_current_rsync_failure_cleans_up(tmp_path: Path) -> None:
    src = tmp_path / ".claude"
    src.mkdir()
    layout = AccountLayout(home=tmp_path)

    def fake_run(cmd, check):  # noqa: ARG001
        raise subprocess.CalledProcessError(returncode=23, cmd=cmd)

    with patch("gits.core.account.subprocess.run", side_effect=fake_run):
        with pytest.raises(AccountLayoutError, match="rsync exited"):
            layout.create_account_dir("work", capture_current=True)
    assert not (tmp_path / ".claude-work").exists()


def test_capture_current_keyboard_interrupt_cleans_up(tmp_path: Path) -> None:
    src = tmp_path / ".claude"
    src.mkdir()
    layout = AccountLayout(home=tmp_path)

    def fake_run(cmd, check):  # noqa: ARG001
        raise KeyboardInterrupt

    with patch("gits.core.account.subprocess.run", side_effect=fake_run):
        with pytest.raises(KeyboardInterrupt):
            layout.create_account_dir("work", capture_current=True)
    assert not (tmp_path / ".claude-work").exists()


def test_capture_current_rsync_missing_cleans_up(tmp_path: Path) -> None:
    src = tmp_path / ".claude"
    src.mkdir()
    layout = AccountLayout(home=tmp_path)

    with patch("gits.core.account.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(AccountLayoutError, match="rsync executable"):
            layout.create_account_dir("work", capture_current=True)
    assert not (tmp_path / ".claude-work").exists()


def test_capture_current_with_explicit_source(tmp_path: Path) -> None:
    custom = tmp_path / "custom-source"
    custom.mkdir()
    (custom / "marker").write_text("custom")

    layout = AccountLayout(home=tmp_path)
    target = layout.create_account_dir("foo", capture_current=True, source=custom)
    assert (target / "marker").read_text() == "custom"


# ----------------------------------------------------------------------
# discover_account_dirs
# ----------------------------------------------------------------------


def test_discover_returns_managed_dirs_only(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path)
    layout.create_account_dir("alpha")
    layout.create_account_dir("beta")
    # Non-managed sibling
    (tmp_path / ".claude-stranger").mkdir()
    # Reserved name (skipped even if it matches the prefix)
    (tmp_path / ".claude-shared").mkdir()
    (tmp_path / ".claude-shared" / MARKER_FILENAME).touch()  # would be tampered

    found = layout.discover_account_dirs()
    names = sorted(p.name for p in found)
    assert names == [".claude-alpha", ".claude-beta"]


def test_discover_returns_empty_when_home_unreadable(tmp_path: Path) -> None:
    layout = AccountLayout(home=tmp_path / "does-not-exist")
    assert layout.discover_account_dirs() == []


# ----------------------------------------------------------------------
# Module constants sanity
# ----------------------------------------------------------------------


def test_shared_subitems_includes_expected() -> None:
    assert "projects" in SHARED_SUBITEMS
    assert "settings.json" in SHARED_SUBITEMS
    assert "todos" in SHARED_SUBITEMS
    assert "plugins" in SHARED_SUBITEMS


def test_marker_filename() -> None:
    assert MARKER_FILENAME == ".gits-managed"
