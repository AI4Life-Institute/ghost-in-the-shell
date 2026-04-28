"""Tests for CodingCLILauncher."""

import json
from pathlib import Path

import pytest

from gits.core.account import AccountLayout
from gits.core.launcher import RESUME_TEMPLATES, CLISession, CodingCLILauncher


@pytest.fixture
def launcher(tmp_path):
    return CodingCLILauncher(session_map_path=tmp_path / "session_map.json")


@pytest.fixture
def launcher_with_layout(tmp_path):
    """Launcher rooted at ``tmp_path`` so account dirs land under it."""
    layout = AccountLayout(home=tmp_path)
    return CodingCLILauncher(
        session_map_path=tmp_path / "session_map.json",
        account_layout=layout,
    )


class TestResumeTemplates:
    def test_all_clis_present(self):
        assert "claude" in RESUME_TEMPLATES
        assert "codex" in RESUME_TEMPLATES
        assert "copilot" in RESUME_TEMPLATES
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
        assert cmd == "opencode -s sess1"

    def test_opencode_without_session_id(self, launcher):
        cmd = launcher.build_launch_command(cli="opencode")
        assert cmd == "opencode"

    def test_copilot_with_session_id(self, launcher):
        cmd = launcher.build_launch_command(cli="copilot", session_id="cp1")
        assert cmd == "copilot --resume cp1"

    def test_copilot_without_session_id(self, launcher):
        cmd = launcher.build_launch_command(cli="copilot")
        assert cmd == "copilot"

    def test_unknown_cli(self, launcher):
        cmd = launcher.build_launch_command(cli="unknown-cli")
        assert cmd == "unknown-cli"


class TestAccountAwareLaunch:
    """Phase 0.5 — CLAUDE_CONFIG_DIR injection by claude_account."""

    def test_claude_with_account_injects_config_dir(self, launcher_with_layout, tmp_path):
        cmd = launcher_with_layout.build_launch_command(
            cli="claude", session_id="s1", claude_account="personal"
        )
        expected_dir = str(tmp_path / ".claude-personal")
        assert cmd.startswith(f"CLAUDE_CONFIG_DIR={expected_dir} "), cmd
        assert cmd.endswith("claude --resume s1")

    def test_claude_no_account_no_injection(self, launcher_with_layout):
        cmd = launcher_with_layout.build_launch_command(
            cli="claude", session_id="s1", claude_account=None
        )
        assert "CLAUDE_CONFIG_DIR" not in cmd
        assert cmd == "claude --resume s1"

    @pytest.mark.parametrize("base_cli", ["codex", "copilot", "opencode"])
    def test_non_claude_cli_no_injection(self, launcher_with_layout, base_cli):
        # claude_account ignored for non-claude bases
        cmd = launcher_with_layout.build_launch_command(
            cli=base_cli, session_id="s", claude_account="personal"
        )
        assert "CLAUDE_CONFIG_DIR" not in cmd

    def test_resolve_cli_account_overrides_config_dir(self, launcher_with_layout, tmp_path):
        resolved = launcher_with_layout.resolve_cli("claude", claude_account="work")
        assert resolved.config_dir == str(tmp_path / ".claude-work")
        assert resolved.session_path == str(tmp_path / ".claude-work" / "projects")

    def test_resolve_cli_no_account_no_override(self, launcher_with_layout):
        resolved = launcher_with_layout.resolve_cli("claude")
        assert resolved.config_dir is None
        assert resolved.session_path is None

    def test_alias_with_account_account_wins(self, tmp_path):
        # Set up an alias with explicit config_dir; account override should beat it.
        config = tmp_path / "config.json"
        config.write_text(json.dumps({
            "cli_aliases": {
                "myclaude": {
                    "type": "claude",
                    "cmd": "myclaude",
                    "config_dir": "/tmp/alias-config-dir",
                    "session_path": "/tmp/alias-projects",
                }
            }
        }))
        launcher = CodingCLILauncher(
            session_map_path=tmp_path / "session_map.json",
            config_path=config,
            account_layout=AccountLayout(home=tmp_path),
        )
        # Without account: alias config wins
        no_acct = launcher.resolve_cli("myclaude")
        assert no_acct.config_dir == "/tmp/alias-config-dir"
        # With account: account overrides
        with_acct = launcher.resolve_cli("myclaude", claude_account="work")
        assert with_acct.config_dir == str(tmp_path / ".claude-work")
        assert with_acct.session_path == str(tmp_path / ".claude-work" / "projects")

    def test_get_session_file_account_aware(self, launcher_with_layout, tmp_path):
        # Set up an account dir with a fake JSONL
        account_projects = tmp_path / ".claude-personal" / "projects"
        work_dir = "/data/projects/foo"
        dir_hash = work_dir.replace("/", "-")
        target_dir = account_projects / dir_hash
        target_dir.mkdir(parents=True)
        jsonl = target_dir / "session-X.jsonl"
        jsonl.write_text("{}\n")

        # With account → finds it
        found = launcher_with_layout.get_session_file(
            work_dir, "claude", "session-X", claude_account="personal"
        )
        assert found == str(jsonl)

        # Without account → looks in legacy and finds nothing
        not_found = launcher_with_layout.get_session_file(
            work_dir, "claude", "session-X", claude_account=None
        )
        assert not_found is None

    def test_active_env_file_no_longer_injected(self, tmp_path):
        # Phase 0.1 deprecation: even if active_env_file is provided, it
        # MUST NOT appear in the launch command.
        launcher = CodingCLILauncher(
            session_map_path=tmp_path / "sm.json",
            active_env_file=tmp_path / "active-env.sh",
        )
        cmd = launcher.build_launch_command(cli="claude", session_id="s")
        assert "active-env.sh" not in cmd
        assert "[ -f" not in cmd
        assert cmd == "claude --resume s"


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

    def test_discover_codex_sessions(self, tmp_path, launcher):
        """Test discovering Codex sessions from JSONL files."""
        work_dir = "/data/projects/my-app"
        codex_dir = tmp_path / ".codex" / "sessions" / "2026" / "03" / "14"
        codex_dir.mkdir(parents=True)

        # Create a matching session
        session_file = codex_dir / "rollout-2026-03-14T17-33-49-test-uuid.jsonl"
        lines = [
            json.dumps({
                "type": "session_meta",
                "payload": {"id": "test-uuid", "cwd": work_dir},
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Fix the bug"}],
                },
            }),
            json.dumps({"type": "event_msg", "payload": {}}),
        ]
        session_file.write_text("\n".join(lines) + "\n")

        # Create a non-matching session (different cwd)
        other_file = codex_dir / "rollout-other.jsonl"
        other_file.write_text(json.dumps({
            "type": "session_meta",
            "payload": {"id": "other", "cwd": "/other/path"},
        }) + "\n")

        original_home = Path.home
        try:
            Path.home = staticmethod(lambda: tmp_path)
            sessions = launcher.discover_sessions(work_dir, cli="codex")
        finally:
            Path.home = original_home

        assert len(sessions) == 1
        assert sessions[0].session_id == "test-uuid"
        assert "Fix the bug" in sessions[0].summary

    def test_discover_opencode_sessions(self, tmp_path, launcher):
        """Test discovering OpenCode sessions from JSON storage."""
        work_dir = "/data/projects/my-app"
        storage = tmp_path / ".local" / "share" / "opencode" / "storage"

        # Create project file
        proj_dir = storage / "project"
        proj_dir.mkdir(parents=True)
        proj_data = {"id": "proj123", "worktree": work_dir}
        (proj_dir / "proj123.json").write_text(json.dumps(proj_data))

        # Create session file
        sess_dir = storage / "session" / "proj123"
        sess_dir.mkdir(parents=True)
        sess_data = {
            "id": "ses_abc",
            "projectID": "proj123",
            "directory": work_dir,
            "title": "New session - test",
        }
        (sess_dir / "ses_abc.json").write_text(json.dumps(sess_data))

        # Create message dir (2 messages)
        msg_dir = storage / "message" / "ses_abc"
        msg_dir.mkdir(parents=True)
        (msg_dir / "msg1.json").write_text("{}")
        (msg_dir / "msg2.json").write_text("{}")

        original_home = Path.home
        try:
            Path.home = staticmethod(lambda: tmp_path)
            sessions = launcher.discover_sessions(work_dir, cli="opencode")
        finally:
            Path.home = original_home

        assert len(sessions) == 1
        assert sessions[0].session_id == "ses_abc"
        assert sessions[0].summary == "New session - test"
        assert sessions[0].message_count == 2
