"""Tests for CodingCLILauncher."""

import json
from pathlib import Path

import pytest

from gits.core.launcher import RESUME_TEMPLATES, CLISession, CodingCLILauncher


@pytest.fixture
def launcher(tmp_path):
    return CodingCLILauncher(session_map_path=tmp_path / "session_map.json")


class TestResumeTemplates:
    def test_all_clis_present(self):
        assert "claude" in RESUME_TEMPLATES
        assert "codex" in RESUME_TEMPLATES
        assert "opencode" in RESUME_TEMPLATES

    def test_each_has_by_id_and_latest(self):
        for cli, templates in RESUME_TEMPLATES.items():
            assert "by_id" in templates, f"{cli} missing by_id"
            assert "latest" in templates, f"{cli} missing latest"
            assert "{id}" in templates["by_id"], f"{cli} by_id missing {{id}} placeholder"


class TestBuildLaunchCommand:
    def test_claude_with_session_id(self, launcher):
        cmd = launcher.build_launch_command(cli="claude", session_id="abc123")
        assert cmd == "claude --resume abc123"

    def test_claude_without_session_id(self, launcher):
        cmd = launcher.build_launch_command(cli="claude")
        assert cmd == "claude"

    def test_codex_with_session_id(self, launcher):
        cmd = launcher.build_launch_command(cli="codex", session_id="xyz")
        assert cmd == "codex resume xyz"

    def test_codex_without_session_id(self, launcher):
        cmd = launcher.build_launch_command(cli="codex")
        assert cmd == "codex"

    def test_opencode_with_session_id(self, launcher):
        cmd = launcher.build_launch_command(cli="opencode", session_id="sess1")
        assert cmd == "opencode --session sess1"

    def test_opencode_without_session_id(self, launcher):
        cmd = launcher.build_launch_command(cli="opencode")
        assert cmd == "opencode"

    def test_unknown_cli(self, launcher):
        cmd = launcher.build_launch_command(cli="unknown-cli")
        assert cmd == "unknown-cli"


class TestSessionMap:
    def test_set_and_get(self, launcher):
        launcher.set_session_id("win-1", "session-abc")
        assert launcher.get_session_id("win-1") == "session-abc"

    def test_get_missing(self, launcher):
        assert launcher.get_session_id("nonexistent") is None

    def test_persistence(self, tmp_path):
        path = tmp_path / "map.json"
        l1 = CodingCLILauncher(session_map_path=path)
        l1.set_session_id("win-x", "sess-y")

        # New instance should load from file
        l2 = CodingCLILauncher(session_map_path=path)
        assert l2.get_session_id("win-x") == "sess-y"

    def test_overwrite(self, launcher):
        launcher.set_session_id("win-1", "old")
        launcher.set_session_id("win-1", "new")
        assert launcher.get_session_id("win-1") == "new"


class TestDiscoverSessions:
    def test_discover_claude_sessions(self, tmp_path, launcher):
        """Test discovering Claude sessions from JSONL files."""
        # Create fake Claude project directory
        work_dir = "/data/projects/my-app"
        dir_hash = work_dir.replace("/", "-")
        project_dir = tmp_path / ".claude" / "projects" / dir_hash
        project_dir.mkdir(parents=True)

        # Create fake session JSONL files
        for i, (name, msg) in enumerate([
            ("sess-aaa", "Fix the auth bug"),
            ("sess-bbb", "Add unit tests"),
        ]):
            jsonl = project_dir / f"{name}.jsonl"
            line = json.dumps({
                "message": {
                    "content": [{"type": "text", "text": msg}]
                }
            })
            jsonl.write_text(line + "\n" + '{"other": true}\n')

        # Monkey-patch Path.home() for test
        import gits.core.launcher as launcher_mod
        original_home = Path.home

        try:
            Path.home = staticmethod(lambda: tmp_path)
            sessions = launcher.discover_sessions(work_dir, cli="claude")
        finally:
            Path.home = original_home

        assert len(sessions) == 2
        # Should be sorted by mtime desc
        for s in sessions:
            assert isinstance(s, CLISession)
            assert s.message_count == 2  # 2 lines in each file

    def test_discover_no_directory(self, launcher):
        sessions = launcher.discover_sessions("/nonexistent/path", cli="claude")
        assert sessions == []

    def test_discover_unknown_cli(self, launcher):
        sessions = launcher.discover_sessions("/some/path", cli="vim")
        assert sessions == []
