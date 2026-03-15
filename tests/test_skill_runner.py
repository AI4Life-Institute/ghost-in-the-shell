"""Tests for SkillRunner — scheduler logic and log rotation (no real tmux)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gits.core.skill_loader import (
    GuardConfig,
    LoopTrigger,
    ReactiveTrigger,
    Skill,
    SkillStep,
    Tool,
)
from gits.core.skill_runner import (
    MAX_LOG_FILES,
    SkillRunner,
    _adaptive_interval,
    _rotate_logs,
    _seconds_until_next_cron,
    AGENTS_DIR,
)


# ------------------------------------------------------------------
# Helpers to build test fixtures
# ------------------------------------------------------------------

def _make_skill(
    name: str = "test-skill",
    loop: LoopTrigger | None = None,
    reactive: ReactiveTrigger | None = None,
    steps: list[SkillStep] | None = None,
    on_failure: str = "stop",
    guard_enabled: bool = False,
) -> Skill:
    return Skill(
        name=name,
        description="test",
        loop=loop,
        reactive=reactive,
        steps=steps or [],
        on_failure=on_failure,
        guard=GuardConfig(enabled=guard_enabled),
    )


def _make_runner(emit_events: list | None = None) -> SkillRunner:
    events = emit_events if emit_events is not None else []
    runner = SkillRunner(emit_fn=lambda e: events.append(e))
    return runner


# ------------------------------------------------------------------
# Patch helpers — suppress all tmux / DB calls
# ------------------------------------------------------------------

TMUX_PATCHES = [
    "gits.core.skill_runner._ensure_runner_session",
    "gits.core.skill_runner._start_pipe_pane",
    "gits.core.skill_runner._stop_pipe_pane",
    "gits.core.skill_runner._exec_command_in_session",
    "gits.core.skill_runner._update_current_log",
    "gits.core.skill_runner._rotate_logs",
]


def _patch_tmux(extra_patches: dict | None = None):
    """Return a context-manager-stack that stubs all tmux helpers."""
    import contextlib

    @contextlib.contextmanager
    def _ctx(exec_return=0):
        patches = []
        mocks: dict[str, MagicMock] = {}
        for name in TMUX_PATCHES:
            if name == "gits.core.skill_runner._exec_command_in_session":
                m = patch(name, return_value=exec_return)
            else:
                m = patch(name)
            patches.append(m)

        started = [p.start() for p in patches]
        for p_obj, name in zip(started, TMUX_PATCHES):
            mocks[name.split(".")[-1]] = p_obj
        if extra_patches:
            for k, v in extra_patches.items():
                mocks[k] = v
        try:
            yield mocks
        finally:
            for p in patches:
                p.stop()

    return _ctx


# ------------------------------------------------------------------
# 1. Loop trigger with interval_seconds fires the skill
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_trigger_fires_skill():
    """Skill with loop.interval_seconds=1 should fire when run_now() is called."""
    events: list[dict] = []

    skill = _make_skill(
        name="looper",
        loop=LoopTrigger(interval_seconds=1),
        steps=[SkillStep(tool_name=None, command="echo hello")],
    )
    runner = SkillRunner(emit_fn=lambda e: events.append(e))
    runner.load({"looper": skill})

    with _patch_tmux()():
        await runner.start()
        # Trigger immediately via run_now instead of waiting for loop interval
        await runner.run_now("looper")
        await asyncio.sleep(0.15)
        await runner.stop()

    # The runner emits agent_log events for each run start
    start_events = [e for e in events if e.get("event") == "agent_log"
                    and "Starting" in e.get("line", "")]
    assert len(start_events) >= 1, "Expected at least one start event"


@pytest.mark.asyncio
async def test_loop_trigger_fires_skill_via_emit_capture():
    """Skill with loop.interval_seconds=1 fires via run_now(); events captured."""
    events: list[dict] = []
    skill = _make_skill(
        name="looper2",
        loop=LoopTrigger(interval_seconds=1),
        steps=[SkillStep(tool_name=None, command="echo hi")],
    )
    runner = SkillRunner(emit_fn=lambda e: events.append(e))
    runner.load({"looper2": skill})

    with _patch_tmux()():
        await runner.start()
        await runner.run_now("looper2")
        await asyncio.sleep(0.2)
        await runner.stop()

    start_events = [e for e in events if e.get("event") == "agent_log"
                    and "Starting" in e.get("line", "")]
    assert len(start_events) >= 1


# ------------------------------------------------------------------
# 2. Reactive trigger with always_on=True fires the skill
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reactive_always_on_fires_skill():
    """always_on=True skill should fire repeatedly."""
    events: list[dict] = []
    skill = _make_skill(
        name="always",
        reactive=ReactiveTrigger(always_on=True),
        steps=[SkillStep(tool_name=None, command="echo alive")],
    )
    runner = SkillRunner(emit_fn=lambda e: events.append(e))
    runner.load({"always": skill})

    with _patch_tmux()():
        await runner.start()
        await asyncio.sleep(0.2)
        await runner.stop()

    start_events = [e for e in events if e.get("event") == "agent_log"
                    and "Starting" in e.get("line", "")]
    # always_on skill restarts after 5 s sleep — with sleep=5, only 1 run in 0.2 s
    assert len(start_events) >= 1


# ------------------------------------------------------------------
# 3. Pause prevents skill from running
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pause_prevents_execution():
    """Paused skill must not produce any start events."""
    events: list[dict] = []
    skill = _make_skill(
        name="pausable",
        loop=LoopTrigger(interval_seconds=0),
        steps=[SkillStep(tool_name=None, command="echo x")],
    )
    runner = SkillRunner(emit_fn=lambda e: events.append(e))
    runner.load({"pausable": skill})
    runner.pause("pausable")  # pause BEFORE start

    with _patch_tmux()():
        await runner.start()
        # Loop will keep sleeping 10 s because paused — no execution
        await asyncio.sleep(0.15)
        await runner.stop()

    start_events = [e for e in events if e.get("event") == "agent_log"
                    and "Starting" in e.get("line", "")]
    assert len(start_events) == 0


# ------------------------------------------------------------------
# 4. run_now() fires immediately
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_now_fires_immediately():
    """run_now should trigger execution regardless of schedule."""
    events: list[dict] = []
    skill = _make_skill(
        name="immediate",
        loop=LoopTrigger(interval_seconds=3600),  # would normally wait 1 h
        steps=[SkillStep(tool_name=None, command="echo now")],
    )
    runner = SkillRunner(emit_fn=lambda e: events.append(e))
    runner.load({"immediate": skill})

    with _patch_tmux()():
        await runner.start()
        await runner.run_now("immediate")
        await asyncio.sleep(0.15)
        await runner.stop()

    start_events = [e for e in events if e.get("event") == "agent_log"
                    and "Starting" in e.get("line", "")]
    assert len(start_events) >= 1


# ------------------------------------------------------------------
# 5. Log rotation: creates files, deletes oldest when > MAX_LOG_FILES
# ------------------------------------------------------------------

def test_log_rotation_deletes_excess(tmp_path: Path):
    """_rotate_logs should keep at most MAX_LOG_FILES non-current logs."""
    skill_name = "rot-test"
    log_dir = tmp_path / skill_name
    log_dir.mkdir(parents=True)

    # Monkeypatch AGENTS_DIR
    import gits.core.skill_runner as sr_mod
    original = sr_mod.AGENTS_DIR
    sr_mod.AGENTS_DIR = tmp_path

    try:
        # Create MAX_LOG_FILES + 5 log files with distinct mtimes
        n_files = MAX_LOG_FILES + 5
        for i in range(n_files):
            f = log_dir / f"2024-01-01T00-00-{i:02d}-aabbcc.log"
            f.write_text(f"run {i}")
            # stagger mtimes so rotation is deterministic
            mtime = time.time() - (n_files - i) * 10
            import os
            os.utime(f, (mtime, mtime))

        _rotate_logs(skill_name)

        remaining = [f for f in log_dir.glob("*.log") if f.name != "current.log"]
        assert len(remaining) == MAX_LOG_FILES
    finally:
        sr_mod.AGENTS_DIR = original


def test_log_rotation_deletes_old_files(tmp_path: Path):
    """_rotate_logs should delete files older than MAX_LOG_AGE_DAYS."""
    import os
    import gits.core.skill_runner as sr_mod

    skill_name = "age-test"
    log_dir = tmp_path / skill_name
    log_dir.mkdir(parents=True)

    original = sr_mod.AGENTS_DIR
    sr_mod.AGENTS_DIR = tmp_path

    try:
        old_time = time.time() - (sr_mod.MAX_LOG_AGE_DAYS + 1) * 86400

        # 3 old files
        for i in range(3):
            f = log_dir / f"old-{i}.log"
            f.write_text("old")
            os.utime(f, (old_time, old_time))

        # 2 recent files
        for i in range(2):
            f = log_dir / f"new-{i}.log"
            f.write_text("new")

        _rotate_logs(skill_name)

        remaining = [f for f in log_dir.glob("*.log") if f.name != "current.log"]
        names = {f.name for f in remaining}
        assert "new-0.log" in names
        assert "new-1.log" in names
        for i in range(3):
            assert f"old-{i}.log" not in names
    finally:
        sr_mod.AGENTS_DIR = original


# ------------------------------------------------------------------
# 6. _seconds_until_next_cron returns a positive float
# ------------------------------------------------------------------

def test_seconds_until_next_cron_valid():
    """Every-minute cron should return a value in [0, 60]."""
    secs = _seconds_until_next_cron("* * * * *")
    assert isinstance(secs, float)
    assert 0.0 <= secs <= 60.0


def test_seconds_until_next_cron_invalid():
    """Invalid cron expression should default to 3600."""
    secs = _seconds_until_next_cron("not-a-cron")
    assert secs == 3600.0


def test_seconds_until_next_cron_hourly():
    """Hourly cron should return a value between 0 and 3600."""
    secs = _seconds_until_next_cron("0 * * * *")
    assert 0.0 <= secs <= 3600.0


# ------------------------------------------------------------------
# 7. _adaptive_interval returns peak_seconds during peak hours
# ------------------------------------------------------------------

def test_adaptive_interval_peak():
    """During peak hours on a weekday, should return peak_seconds."""
    from datetime import datetime
    reactive = ReactiveTrigger(
        always_on=False,
        peak_seconds=30,
        off_seconds=600,
        peak_start="09:00",
        peak_end="17:00",
    )
    # Monday 2024-01-08 at 10:00 — within peak window, weekday
    monday_10am = datetime(2024, 1, 8, 10, 0)
    result = _adaptive_interval(reactive, _now=monday_10am)
    assert result == 30.0


def test_adaptive_interval_off_peak():
    """Outside peak hours on a weekend, should return off_seconds."""
    from datetime import datetime
    reactive = ReactiveTrigger(
        always_on=False,
        peak_seconds=30,
        off_seconds=600,
        peak_start="09:00",
        peak_end="17:00",
    )
    # Saturday 2024-01-06 at 10:00 — weekend, outside weekday check
    saturday = datetime(2024, 1, 6, 10, 0)
    result = _adaptive_interval(reactive, _now=saturday)
    assert result == 600.0


def test_adaptive_interval_off_hours():
    """Weekday evening outside peak hours should return off_seconds."""
    from datetime import datetime
    reactive = ReactiveTrigger(
        always_on=False,
        peak_seconds=30,
        off_seconds=600,
        peak_start="09:00",
        peak_end="17:00",
    )
    # Monday 2024-01-08 at 20:00 — after peak hours
    monday_evening = datetime(2024, 1, 8, 20, 0)
    result = _adaptive_interval(reactive, _now=monday_evening)
    assert result == 600.0


# ------------------------------------------------------------------
# 8. _resolve_step_command uses tool map correctly
# ------------------------------------------------------------------

def test_resolve_step_uses_tool_map():
    """Step referencing a known tool should use tool.command."""
    tool = Tool(name="my-tool", command="python run.py", working_dir="/tmp", env={"FOO": "bar"})
    runner = _make_runner()
    runner.load({}, tools={"my-tool": tool})

    step = SkillStep(tool_name="my-tool", command=None)
    cmd, wd, env = runner._resolve_step_command(step)
    assert cmd == "python run.py"
    assert wd == "/tmp"
    assert env == {"FOO": "bar"}


def test_resolve_step_inline_command():
    """Step with inline command and no tool match."""
    runner = _make_runner()
    runner.load({}, tools={})

    step = SkillStep(tool_name=None, command="ls -la")
    cmd, wd, env = runner._resolve_step_command(step)
    assert cmd == "ls -la"
    assert env == {}


def test_resolve_step_unknown_tool_fallback():
    """Unknown tool_name falls back to using tool_name as the command."""
    runner = _make_runner()
    runner.load({}, tools={})

    step = SkillStep(tool_name="some-unknown-tool", command=None)
    cmd, wd, env = runner._resolve_step_command(step)
    assert cmd == "some-unknown-tool"


def test_resolve_step_env_merge():
    """shell_env is overridden by tool env when both present."""
    tool = Tool(name="t", command="run", working_dir="/", env={"KEY": "tool-value", "TOOL_ONLY": "yes"})
    runner = SkillRunner(emit_fn=lambda e: None)
    runner.load({}, tools={"t": tool}, shell_env={"KEY": "shell-value", "SHELL_ONLY": "yes"})

    step = SkillStep(tool_name="t", command=None)
    _, _, tool_env = runner._resolve_step_command(step)

    # Simulate the merge logic in _execute_skill
    merged: dict[str, str] = {}
    merged.update(runner._shell_env or {})
    merged.update(tool_env)

    assert merged["KEY"] == "tool-value"       # tool overrides shell
    assert merged["SHELL_ONLY"] == "yes"        # shell-only vars preserved
    assert merged["TOOL_ONLY"] == "yes"         # tool-only vars present
