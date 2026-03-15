"""Tests for SkillLoader — parsing Tool and Skill Markdown definitions."""

from __future__ import annotations

from pathlib import Path

import pytest

from gits.core.skill_loader import (
    GuardConfig,
    Skill,
    SkillLoader,
    SkillStep,
    Tool,
    _parse_skill,
    _parse_tool,
    _parse_trigger,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tool_file(tools_dir: Path, name: str, content: str) -> Path:
    p = tools_dir / f"{name}.md"
    p.write_text(content)
    return p


def make_skill_file(skills_dir: Path, name: str, content: str) -> Path:
    p = skills_dir / f"{name}.md"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Trigger parsing
# ---------------------------------------------------------------------------

class TestLoopTriggerCron:
    """Task 2.1 — loop trigger with cron schedule."""

    def test_loop_cron_schedule(self):
        trigger_text = 'loop:\n  schedule: "0 5 * * 1-5"'
        loop, reactive = _parse_trigger(trigger_text)
        assert loop is not None
        assert reactive is None
        assert loop.schedule == "0 5 * * 1-5"
        assert loop.interval_seconds is None

    def test_loop_interval_seconds(self):
        trigger_text = "loop:\n  interval_seconds: 3600"
        loop, reactive = _parse_trigger(trigger_text)
        assert loop is not None
        assert loop.interval_seconds == 3600
        assert loop.schedule is None


class TestReactiveTriggerPolling:
    """Task 2.2 — reactive trigger with polling intervals."""

    def test_reactive_polling(self):
        trigger_text = (
            "reactive:\n"
            "  polling:\n"
            '    peak_seconds: 60\n'
            '    peak_start: "09:30"\n'
            '    peak_end: "16:00"\n'
            "    off_seconds: 1800"
        )
        loop, reactive = _parse_trigger(trigger_text)
        assert loop is None
        assert reactive is not None
        assert reactive.always_on is False
        assert reactive.peak_seconds == 60
        assert reactive.off_seconds == 1800
        assert reactive.peak_start == "09:30"
        assert reactive.peak_end == "16:00"

    def test_reactive_polling_custom_times(self):
        trigger_text = (
            "reactive:\n"
            "  polling:\n"
            "    peak_seconds: 30\n"
            '    peak_start: "08:00"\n'
            '    peak_end: "17:30"\n'
            "    off_seconds: 900"
        )
        _, reactive = _parse_trigger(trigger_text)
        assert reactive is not None
        assert reactive.peak_seconds == 30
        assert reactive.peak_start == "08:00"
        assert reactive.peak_end == "17:30"
        assert reactive.off_seconds == 900


class TestAlwaysOnTrigger:
    """Task 2.3 — always-on reactive trigger."""

    def test_always_on(self):
        trigger_text = "reactive:\n  always_on: true"
        loop, reactive = _parse_trigger(trigger_text)
        assert loop is None
        assert reactive is not None
        assert reactive.always_on is True

    def test_always_on_defaults_retained(self):
        """always_on skill should still have sensible defaults for other fields."""
        trigger_text = "reactive:\n  always_on: true"
        _, reactive = _parse_trigger(trigger_text)
        assert reactive is not None
        assert reactive.peak_seconds == 60   # default
        assert reactive.off_seconds == 1800  # default


# ---------------------------------------------------------------------------
# Step parsing
# ---------------------------------------------------------------------------

class TestInlineStep:
    """Task 2.4 — inline step (command not matching any tool)."""

    def test_inline_command_step(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        make_skill_file(
            skills_dir,
            "my-skill",
            '# Skill: my-skill\n\nDoes things.\n\n## Trigger\nloop:\n  interval_seconds: 60\n\n## Steps\n- echo "done"\n',
        )
        loader = SkillLoader(
            tools_dir=tmp_path / "tools",
            skills_dir=skills_dir,
            config_file=tmp_path / "config.md",
        )
        skills = loader.load_skills()
        assert "my-skill" in skills
        skill = skills["my-skill"]
        assert len(skill.steps) == 1
        step = skill.steps[0]
        assert step.tool_name is None
        assert step.command == 'echo "done"'


class TestToolReferenceStep:
    """Task 2.5 — tool reference step (command matches a tool file stem)."""

    def test_tool_reference_resolved(self, tmp_path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        make_tool_file(
            tools_dir,
            "discord-run",
            "# Tool: discord-run\n\nRuns Discord.\n\n## Command\nnpx tsx run.ts\n\n## Working Directory\n~/projects/app\n",
        )
        make_skill_file(
            skills_dir,
            "my-skill",
            "# Skill: my-skill\n\nA skill.\n\n## Trigger\nloop:\n  interval_seconds: 60\n\n## Steps\n- discord-run\n",
        )

        loader = SkillLoader(
            tools_dir=tools_dir,
            skills_dir=skills_dir,
            config_file=tmp_path / "config.md",
        )
        tools = loader.load_tools()
        skills = loader.load_skills(tools)

        assert "discord-run" in tools
        assert "my-skill" in skills
        step = skills["my-skill"].steps[0]
        assert step.tool_name == "discord-run"
        assert step.command is None


class TestMissingToolInlinesFallback:
    """Task 2.6 — missing tool falls back to inline command."""

    def test_unknown_step_becomes_inline(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        make_skill_file(
            skills_dir,
            "digest-skill",
            "# Skill: digest-skill\n\nDigest.\n\n## Trigger\nloop:\n  schedule: \"0 5 * * 1-5\"\n\n## Steps\n- nonexistent-tool\n- echo hello\n",
        )

        loader = SkillLoader(
            tools_dir=tmp_path / "tools",   # empty / nonexistent
            skills_dir=skills_dir,
            config_file=tmp_path / "config.md",
        )
        skills = loader.load_skills()
        skill = skills["digest-skill"]
        assert len(skill.steps) == 2
        # Both steps are inline because no tool map was loaded
        for step in skill.steps:
            assert step.tool_name is None
            assert step.command is not None


# ---------------------------------------------------------------------------
# load_ops_session
# ---------------------------------------------------------------------------

class TestLoadOpsSession:
    """Task 2.7 — load_ops_session reads from config.md."""

    def test_reads_ops_session_from_config(self, tmp_path):
        config_file = tmp_path / "config.md"
        config_file.write_text("## Guard\nops_session: my-custom-session\n")
        loader = SkillLoader(
            tools_dir=tmp_path / "tools",
            skills_dir=tmp_path / "skills",
            config_file=config_file,
        )
        assert loader.load_ops_session() == "my-custom-session"

    def test_defaults_to_ghost_ops_when_no_config(self, tmp_path):
        loader = SkillLoader(
            tools_dir=tmp_path / "tools",
            skills_dir=tmp_path / "skills",
            config_file=tmp_path / "config.md",  # does not exist
        )
        assert loader.load_ops_session() == "ghost-ops"

    def test_defaults_to_ghost_ops_when_no_guard_section(self, tmp_path):
        config_file = tmp_path / "config.md"
        config_file.write_text("# Config\n\nNo guard section here.\n")
        loader = SkillLoader(
            tools_dir=tmp_path / "tools",
            skills_dir=tmp_path / "skills",
            config_file=config_file,
        )
        assert loader.load_ops_session() == "ghost-ops"

    def test_defaults_to_ghost_ops_when_no_ops_session_key(self, tmp_path):
        config_file = tmp_path / "config.md"
        config_file.write_text("## Guard\nsome_other_key: value\n")
        loader = SkillLoader(
            tools_dir=tmp_path / "tools",
            skills_dir=tmp_path / "skills",
            config_file=config_file,
        )
        assert loader.load_ops_session() == "ghost-ops"


# ---------------------------------------------------------------------------
# Tool parsing integration
# ---------------------------------------------------------------------------

class TestToolParsing:
    def test_full_tool_file(self, tmp_path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        make_tool_file(
            tools_dir,
            "discord-run",
            (
                "# Tool: discord-run\n\n"
                "Runs the Discord bot.\n\n"
                "## Command\nnpx tsx run-scheduled.ts\n\n"
                "## Working Directory\n~/projects/myapp/cli/discord\n\n"
                "## Environment\nNODE_ENV=production\nAPI_KEY=secret\n"
            ),
        )
        loader = SkillLoader(
            tools_dir=tools_dir,
            skills_dir=tmp_path / "skills",
            config_file=tmp_path / "config.md",
        )
        tools = loader.load_tools()
        assert "discord-run" in tools
        tool = tools["discord-run"]
        assert tool.command == "npx tsx run-scheduled.ts"
        assert tool.env == {"NODE_ENV": "production", "API_KEY": "secret"}
        assert tool.description == "Runs the Discord bot."

    def test_tool_missing_command_returns_none(self, tmp_path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        make_tool_file(tools_dir, "broken-tool", "# Tool: broken-tool\n\nNo command here.\n")
        loader = SkillLoader(
            tools_dir=tools_dir,
            skills_dir=tmp_path / "skills",
            config_file=tmp_path / "config.md",
        )
        tools = loader.load_tools()
        assert "broken-tool" not in tools

    def test_empty_tools_dir(self, tmp_path):
        loader = SkillLoader(
            tools_dir=tmp_path / "nonexistent",
            skills_dir=tmp_path / "skills",
            config_file=tmp_path / "config.md",
        )
        assert loader.load_tools() == {}


# ---------------------------------------------------------------------------
# Skill guard parsing
# ---------------------------------------------------------------------------

class TestGuardParsing:
    def test_guard_disabled_on_never(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        make_skill_file(
            skills_dir,
            "no-guard-skill",
            (
                "# Skill: no-guard-skill\n\n"
                "## Trigger\nloop:\n  interval_seconds: 60\n\n"
                "## Steps\n- echo hi\n\n"
                "## Guard\non: never\n"
            ),
        )
        loader = SkillLoader(
            tools_dir=tmp_path / "tools",
            skills_dir=skills_dir,
            config_file=tmp_path / "config.md",
        )
        skills = loader.load_skills()
        assert skills["no-guard-skill"].guard.enabled is False

    def test_guard_custom_session(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        make_skill_file(
            skills_dir,
            "guarded-skill",
            (
                "# Skill: guarded-skill\n\n"
                "## Trigger\nloop:\n  interval_seconds: 60\n\n"
                "## Steps\n- echo hi\n\n"
                "## Guard\non: failure\nsession: my-ops\n"
            ),
        )
        loader = SkillLoader(
            tools_dir=tmp_path / "tools",
            skills_dir=skills_dir,
            config_file=tmp_path / "config.md",
        )
        skills = loader.load_skills()
        guard = skills["guarded-skill"].guard
        assert guard.enabled is True
        assert guard.session == "my-ops"
