"""Unit tests for ``ghost install-skills`` / ``ghost uninstall-skills``
(task G-4 irzsbr).

All tests use ``tmp_path`` for isolation — they NEVER touch the real
``~/.claude/skills/``. Coverage:

  - install adds expected folders + writes the manifest with the right shape
  - install is idempotent (second run = no-op summary, no duplicate state)
  - install drops a skill that's no longer in the source (and cleans up disk)
  - install does NOT touch non-ghost skills sitting in the dest dir
  - uninstall removes ONLY manifest-listed folders + the manifest itself;
    non-ghost folders survive
  - opt-out flag returns 0 without doing anything
  - opt-out env var (``GHOST_NO_INSTALL_SKILLS=1``) returns 0 without doing anything
  - source dir missing / empty = exit 0 (no-op)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gits import install_skills as mod

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _make_skill(src: Path, name: str, content: str = "# skill\n") -> Path:
    """Create a fake ``<src>/<name>/SKILL.md`` and return the folder path."""
    folder = src / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(content)
    return folder


@pytest.fixture
def src(tmp_path: Path) -> Path:
    """A source-skills dir with 'butler' and 'onboard-worktree'."""
    s = tmp_path / "src_skills"
    _make_skill(s, "butler", "# butler skill\n")
    _make_skill(s, "onboard-worktree", "# onboard skill\n")
    return s


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    """An empty dest-skills dir."""
    return tmp_path / "dest_skills"


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


def test_install_creates_dest_and_copies_skills(src: Path, dest: Path):
    """First-time install: dest is created, both skills appear, manifest written."""
    rc = mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None)
    assert rc == 0
    assert (dest / "butler" / "SKILL.md").is_file()
    assert (dest / "onboard-worktree" / "SKILL.md").is_file()
    # Content is byte-identical to source
    assert (dest / "butler" / "SKILL.md").read_text() == "# butler skill\n"

    manifest_path = dest / mod.MANIFEST_NAME
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text())
    assert set(manifest["skills"]) == {"butler", "onboard-worktree"}
    assert "ghost_version" in manifest
    assert "installed_at" in manifest
    # ISO 8601-ish timestamp
    assert manifest["installed_at"].endswith("Z")


def test_install_is_idempotent(src: Path, dest: Path):
    """Second run: same skills present, manifest still valid, no error."""
    assert mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None) == 0
    assert mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None) == 0
    manifest = json.loads((dest / mod.MANIFEST_NAME).read_text())
    assert set(manifest["skills"]) == {"butler", "onboard-worktree"}
    # Both folders still there, exactly once each
    assert sorted(p.name for p in dest.iterdir() if p.is_dir()) == [
        "butler",
        "onboard-worktree",
    ]


def test_install_updates_changed_skill_content(src: Path, dest: Path):
    """Re-running with changed source content overwrites the dest skill."""
    mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None)
    # Mutate source
    (src / "butler" / "SKILL.md").write_text("# butler v2\n")
    mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None)
    assert (dest / "butler" / "SKILL.md").read_text() == "# butler v2\n"


def test_install_does_not_touch_non_ghost_skills(src: Path, dest: Path):
    """A user-installed skill not in the manifest must survive install."""
    # Pre-populate a "user" skill — never in the manifest, never in source.
    user_skill = _make_skill(dest, "my-custom-skill", "# mine\n")
    assert user_skill.is_dir()

    mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None)

    # User skill is still there, untouched
    assert (dest / "my-custom-skill" / "SKILL.md").read_text() == "# mine\n"
    # And not claimed by the manifest
    manifest = json.loads((dest / mod.MANIFEST_NAME).read_text())
    assert "my-custom-skill" not in manifest["skills"]


def test_install_drops_skill_removed_from_source(src: Path, dest: Path):
    """If a skill stops shipping in source, install removes it from dest +
    drops it from the manifest."""
    # First install: both skills land.
    mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None)
    assert (dest / "onboard-worktree").is_dir()

    # Drop onboard-worktree from source.
    import shutil
    shutil.rmtree(src / "onboard-worktree")

    mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None)

    assert not (dest / "onboard-worktree").exists()
    assert (dest / "butler").is_dir()
    manifest = json.loads((dest / mod.MANIFEST_NAME).read_text())
    assert manifest["skills"] == ["butler"]


def test_install_opt_out_flag(src: Path, dest: Path):
    """opt_out=True → exit 0, nothing copied, no manifest written."""
    rc = mod.install_skills(
        src_dir=src, dest_dir=dest, opt_out=True, log=lambda *_: None
    )
    assert rc == 0
    assert not dest.exists() or not any(dest.iterdir())


def test_opt_out_env_var(monkeypatch, src: Path, dest: Path):
    """GHOST_NO_INSTALL_SKILLS=1 → _opt_out_active() returns True."""
    monkeypatch.setenv("GHOST_NO_INSTALL_SKILLS", "1")
    assert mod._opt_out_active(False) is True
    monkeypatch.setenv("GHOST_NO_INSTALL_SKILLS", "0")
    assert mod._opt_out_active(False) is False
    monkeypatch.delenv("GHOST_NO_INSTALL_SKILLS", raising=False)
    assert mod._opt_out_active(False) is False


def test_install_empty_source(tmp_path: Path, dest: Path):
    """Empty source dir = exit 0 no-op (don't error)."""
    empty_src = tmp_path / "empty"
    empty_src.mkdir()
    rc = mod.install_skills(src_dir=empty_src, dest_dir=dest, log=lambda *_: None)
    assert rc == 0
    # No manifest created when there's nothing to track
    assert not (dest / mod.MANIFEST_NAME).exists()


def test_install_skips_dirs_without_skill_md(tmp_path: Path, dest: Path):
    """Folders without SKILL.md are not skills — silently skipped."""
    s = tmp_path / "src"
    s.mkdir()
    (s / "not-a-skill").mkdir()
    (s / "not-a-skill" / "README.md").write_text("just a readme")
    _make_skill(s, "real-skill")
    rc = mod.install_skills(src_dir=s, dest_dir=dest, log=lambda *_: None)
    assert rc == 0
    assert (dest / "real-skill").is_dir()
    assert not (dest / "not-a-skill").exists()
    manifest = json.loads((dest / mod.MANIFEST_NAME).read_text())
    assert manifest["skills"] == ["real-skill"]


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_uninstall_removes_only_manifest_listed(src: Path, dest: Path):
    """Non-ghost skill must survive uninstall."""
    # Install ghost skills.
    mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None)
    # Add a non-ghost skill alongside.
    _make_skill(dest, "user-skill", "# user\n")

    rc = mod.uninstall_skills(dest_dir=dest, log=lambda *_: None)
    assert rc == 0

    # Ghost skills gone
    assert not (dest / "butler").exists()
    assert not (dest / "onboard-worktree").exists()
    # Manifest gone
    assert not (dest / mod.MANIFEST_NAME).exists()
    # User skill survives
    assert (dest / "user-skill" / "SKILL.md").read_text() == "# user\n"


def test_uninstall_no_dest_dir_is_noop(tmp_path: Path):
    """Uninstall when dest doesn't exist = exit 0."""
    rc = mod.uninstall_skills(dest_dir=tmp_path / "nonexistent", log=lambda *_: None)
    assert rc == 0


def test_uninstall_no_manifest_is_noop(dest: Path):
    """Uninstall with no manifest = exit 0 (nothing ghost-managed)."""
    dest.mkdir()
    _make_skill(dest, "user-skill")
    rc = mod.uninstall_skills(dest_dir=dest, log=lambda *_: None)
    assert rc == 0
    assert (dest / "user-skill").is_dir()


def test_uninstall_missing_managed_skill_succeeds(src: Path, dest: Path):
    """If a manifest-listed folder was manually deleted before uninstall,
    we should still succeed (and still delete the manifest)."""
    mod.install_skills(src_dir=src, dest_dir=dest, log=lambda *_: None)
    import shutil
    shutil.rmtree(dest / "butler")  # user nuked it manually

    rc = mod.uninstall_skills(dest_dir=dest, log=lambda *_: None)
    assert rc == 0
    assert not (dest / "onboard-worktree").exists()
    assert not (dest / mod.MANIFEST_NAME).exists()


# ---------------------------------------------------------------------------
# argparse wiring smoke check
# ---------------------------------------------------------------------------


def test_install_parser_registers_both_commands():
    """install_parser() exposes both verbs on the top-level argparser."""
    import argparse
    parser = argparse.ArgumentParser(prog="ghost")
    sub = parser.add_subparsers(dest="command")
    mod.install_parser(sub)
    # Parse each — should not error.
    args = parser.parse_args(["install-skills"])
    assert args.command == "install-skills"
    assert hasattr(args, "no_install_skills")
    assert args.no_install_skills is False
    args = parser.parse_args(["install-skills", "--no-install-skills"])
    assert args.no_install_skills is True
    args = parser.parse_args(["uninstall-skills"])
    assert args.command == "uninstall-skills"
