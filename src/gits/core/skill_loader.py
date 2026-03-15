"""SkillLoader — parse Tool and Skill Markdown definitions."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TOOLS_DIR = Path.home() / ".gits" / "tools"
SKILLS_DIR = Path.home() / ".gits" / "skills"
CONFIG_FILE = Path.home() / ".gits" / "config.md"


@dataclass
class Tool:
    name: str           # filename stem, e.g. "discord-run"
    command: str        # shell command to run
    working_dir: str    # resolved path, defaults to ~
    env: dict[str, str] = field(default_factory=dict)
    description: str = ""


@dataclass
class LoopTrigger:
    schedule: str | None = None         # cron expression
    interval_seconds: int | None = None


@dataclass
class ReactiveTrigger:
    always_on: bool = False
    peak_seconds: int = 60
    off_seconds: int = 1800
    peak_start: str = "09:30"   # HH:MM
    peak_end: str = "16:00"     # HH:MM


@dataclass
class SkillStep:
    tool_name: str | None     # references Tool by filename stem
    command: str | None       # inline command (if no tool)
    working_dir: str | None = None


@dataclass
class GuardConfig:
    enabled: bool = True      # False if "on: never"
    session: str = "ghost-ops"


@dataclass
class Skill:
    name: str
    description: str
    loop: LoopTrigger | None
    reactive: ReactiveTrigger | None
    steps: list[SkillStep]
    on_failure: str           # "retry: max N" | "continue" | "restart" | "stop" | "notify"
    guard: GuardConfig


def _extract_section(text: str, section: str) -> str | None:
    """Extract text content of ## SectionName up to the next ## header."""
    pattern = rf"^##\s+{re.escape(section)}\s*\n(.*?)(?=^##\s|\Z)"
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def _parse_tool(path: Path) -> Tool | None:
    try:
        text = path.read_text()
    except OSError:
        logger.warning("Cannot read tool file: %s", path)
        return None

    name = path.stem
    cmd_section = _extract_section(text, "Command")
    if not cmd_section:
        logger.warning("Tool %s has no ## Command section", name)
        return None

    command = cmd_section.strip().splitlines()[0].strip()

    # Working Directory
    wd_section = _extract_section(text, "Working Directory")
    working_dir = str(Path(wd_section.strip()).expanduser()) if wd_section else str(Path.home())

    # Environment
    env: dict[str, str] = {}
    env_section = _extract_section(text, "Environment")
    if env_section:
        for line in env_section.splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()

    # Description: text before first ## section
    desc_match = re.match(r"^#[^#].*?\n(.*?)(?=^##|\Z)", text, re.MULTILINE | re.DOTALL)
    description = desc_match.group(1).strip() if desc_match else ""

    return Tool(name=name, command=command, working_dir=working_dir, env=env, description=description)


def _parse_trigger(trigger_text: str) -> tuple[LoopTrigger | None, ReactiveTrigger | None]:
    loop: LoopTrigger | None = None
    reactive: ReactiveTrigger | None = None

    if trigger_text.startswith("loop:"):
        loop = LoopTrigger()
        sched_m = re.search(r'schedule:\s*["\']?([^"\'\n]+)["\']?', trigger_text)
        if sched_m:
            loop.schedule = sched_m.group(1).strip()
        interval_m = re.search(r'interval_seconds:\s*(\d+)', trigger_text)
        if interval_m:
            loop.interval_seconds = int(interval_m.group(1))
    elif trigger_text.startswith("reactive:"):
        r = ReactiveTrigger()
        if re.search(r'always_on:\s*true', trigger_text):
            r.always_on = True
        peak_s = re.search(r'peak_seconds:\s*(\d+)', trigger_text)
        if peak_s:
            r.peak_seconds = int(peak_s.group(1))
        off_s = re.search(r'off_seconds:\s*(\d+)', trigger_text)
        if off_s:
            r.off_seconds = int(off_s.group(1))
        ps = re.search(r'peak_start:\s*["\']?(\d{1,2}:\d{2})["\']?', trigger_text)
        if ps:
            r.peak_start = ps.group(1)
        pe = re.search(r'peak_end:\s*["\']?(\d{1,2}:\d{2})["\']?', trigger_text)
        if pe:
            r.peak_end = pe.group(1)
        reactive = r

    return loop, reactive


def _parse_skill(path: Path, tool_map: dict[str, Tool]) -> Skill | None:
    try:
        text = path.read_text()
    except OSError:
        logger.warning("Cannot read skill file: %s", path)
        return None

    name = path.stem

    # Description
    desc_match = re.match(r"^#[^#].*?\n(.*?)(?=^##|\Z)", text, re.MULTILINE | re.DOTALL)
    description = desc_match.group(1).strip() if desc_match else ""

    # Trigger
    trigger_text = _extract_section(text, "Trigger") or ""
    loop, reactive = _parse_trigger(trigger_text)

    # Steps
    steps_text = _extract_section(text, "Steps") or ""
    steps: list[SkillStep] = []
    for line in steps_text.splitlines():
        line = re.sub(r'^[-*]\s*', '', line).strip()
        if not line:
            continue
        # Check if it references a known tool
        if line in tool_map:
            steps.append(SkillStep(tool_name=line, command=None))
        else:
            # Inline command
            steps.append(SkillStep(tool_name=None, command=line))

    # On Failure
    on_failure_text = _extract_section(text, "On Failure") or "retry: max 3"
    on_failure = on_failure_text.strip().splitlines()[0].strip()

    # Guard
    guard_text = _extract_section(text, "Guard") or ""
    guard = GuardConfig()
    if re.search(r'on:\s*never', guard_text):
        guard.enabled = False
    session_m = re.search(r'session:\s*(\S+)', guard_text)
    if session_m:
        guard.session = session_m.group(1)

    return Skill(
        name=name,
        description=description,
        loop=loop,
        reactive=reactive,
        steps=steps,
        on_failure=on_failure,
        guard=guard,
    )


class SkillLoader:
    """Loads Tools and Skills from ~/.gits/ Markdown files."""

    def __init__(
        self,
        tools_dir: Path = TOOLS_DIR,
        skills_dir: Path = SKILLS_DIR,
        config_file: Path = CONFIG_FILE,
    ) -> None:
        self.tools_dir = tools_dir
        self.skills_dir = skills_dir
        self.config_file = config_file
        self._shell_env: dict[str, str] | None = None

    def load_tools(self) -> dict[str, Tool]:
        """Parse all *.md files in tools_dir. Returns {stem: Tool}."""
        tools: dict[str, Tool] = {}
        if not self.tools_dir.exists():
            return tools
        for md in sorted(self.tools_dir.glob("*.md")):
            tool = _parse_tool(md)
            if tool:
                tools[tool.name] = tool
        return tools

    def load_skills(self, tools: dict[str, Tool] | None = None) -> dict[str, Skill]:
        """Parse all *.md files in skills_dir. Returns {stem: Skill}."""
        if tools is None:
            tools = self.load_tools()
        skills: dict[str, Skill] = {}
        if not self.skills_dir.exists():
            return skills
        for md in sorted(self.skills_dir.glob("*.md")):
            skill = _parse_skill(md, tools)
            if skill:
                skills[skill.name] = skill
        return skills

    def load_ops_session(self) -> str:
        """Read ops_session from ~/.gits/config.md. Defaults to 'ghost-ops'."""
        if not self.config_file.exists():
            return "ghost-ops"
        text = self.config_file.read_text()
        guard_section = _extract_section(text, "Guard")
        if guard_section:
            m = re.search(r'ops_session:\s*(\S+)', guard_section)
            if m:
                return m.group(1)
        return "ghost-ops"

    def get_shell_env(self) -> dict[str, str]:
        """Capture user's login shell env (cached). Fallback: current os.environ."""
        if self._shell_env is not None:
            return self._shell_env
        self._shell_env = _capture_shell_env()
        return self._shell_env


def _capture_shell_env() -> dict[str, str]:
    """Run `zsh -c env` (or bash) to get the full login shell environment."""
    for shell in ("zsh", "bash"):
        try:
            result = subprocess.run(
                [shell, "-c", "env"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                env: dict[str, str] = {}
                for line in result.stdout.splitlines():
                    if "=" in line:
                        k, _, v = line.partition("=")
                        env[k] = v
                if env:
                    logger.debug("Captured shell env (%d vars) via %s", len(env), shell)
                    return env
        except (OSError, subprocess.TimeoutExpired):
            continue
    logger.warning("Could not capture shell env; using os.environ")
    return dict(os.environ)
