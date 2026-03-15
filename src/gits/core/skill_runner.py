"""SkillRunner — schedules and executes Skills as tmux-based Runner Agents."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

AGENTS_DIR = Path.home() / ".gits" / "agents"
GITS_PROMPT = "GITS_PROMPT> "
MAX_LOG_FILES = 30
MAX_LOG_AGE_DAYS = 7

# Type aliases
EmitFn = Callable[[dict], None]
GuardFn = Callable[[str, str, str, str], Awaitable[str]]  # (skill_name, run_id, step_cmd, log_tail) -> action


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S") + "-" + uuid.uuid4().hex[:6]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class SkillRunner:
    """Schedules and executes Skills as Runner Agents.

    Each Skill gets its own asyncio task that fires on Loop/Reactive triggers.
    Steps run sequentially in a named tmux session.
    """

    def __init__(
        self,
        *,
        emit_fn: EmitFn | None = None,
        guard_fn: GuardFn | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._emit = emit_fn or (lambda _: None)
        self._guard_fn = guard_fn
        self._db_path = db_path
        self._tasks: dict[str, asyncio.Task] = {}   # skill_name -> scheduler task
        self._paused: set[str] = set()
        self._skills: dict[str, Any] = {}           # skill_name -> Skill
        self._tools: dict[str, Any] = {}            # tool_name -> Tool
        self._shell_env: dict[str, str] | None = None

    def load(
        self,
        skills: dict[str, Any],
        tools: dict[str, Any] | None = None,
        shell_env: dict[str, str] | None = None,
    ) -> None:
        """Load skill and tool definitions. Call before start()."""
        self._skills = skills
        self._tools = tools or {}
        self._shell_env = shell_env

    async def start(self) -> None:
        """Start scheduler tasks for all loaded skills."""
        for name, skill in self._skills.items():
            self._tasks[name] = asyncio.ensure_future(self._skill_loop(skill))
        logger.info("SkillRunner started (%d skills)", len(self._skills))

    async def stop(self) -> None:
        """Cancel all scheduler tasks."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    def pause(self, skill_name: str) -> None:
        self._paused.add(skill_name)
        logger.info("Skill paused: %s", skill_name)

    def resume(self, skill_name: str) -> None:
        self._paused.discard(skill_name)
        logger.info("Skill resumed: %s", skill_name)

    async def run_now(self, skill_name: str) -> None:
        """Immediately execute a skill regardless of its schedule."""
        skill = self._skills.get(skill_name)
        if not skill:
            logger.warning("run_now: unknown skill %s", skill_name)
            return
        asyncio.ensure_future(self._execute_skill(skill))

    # ------------------------------------------------------------------
    # Step resolution
    # ------------------------------------------------------------------

    def _resolve_step_command(self, step: Any) -> tuple[str | None, str | None, dict]:
        """Returns (command, working_dir, env) for a step.

        Resolution order:
        1. If step references a known Tool by name, use tool.command + tool.working_dir + tool.env
        2. If step has an inline command, use it with step.working_dir
        3. If step.tool_name but no matching tool, treat tool_name as inline command
        """
        if step.tool_name and step.tool_name in self._tools:
            tool = self._tools[step.tool_name]
            return tool.command, tool.working_dir, tool.env
        elif step.command:
            return step.command, getattr(step, "working_dir", None), {}
        elif step.tool_name:
            # Tool name used as inline command fallback
            return step.tool_name, getattr(step, "working_dir", None), {}
        return None, None, {}

    # ------------------------------------------------------------------
    # Scheduler loops
    # ------------------------------------------------------------------

    async def _skill_loop(self, skill: Any) -> None:
        """Main loop for a skill: fire on schedule, handle always-on."""
        try:
            if skill.loop:
                await self._loop_trigger(skill)
            elif skill.reactive:
                await self._reactive_trigger(skill)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Skill loop crashed: %s", skill.name)

    async def _loop_trigger(self, skill: Any) -> None:
        """Fire skill on cron schedule or fixed interval."""
        loop = skill.loop
        while True:
            if skill.name in self._paused:
                await asyncio.sleep(10)
                continue

            if loop.schedule:
                wait_secs = _seconds_until_next_cron(loop.schedule)
            elif loop.interval_seconds is not None:
                wait_secs = loop.interval_seconds
            else:
                wait_secs = 3600

            await asyncio.sleep(wait_secs)

            if skill.name not in self._paused:
                await self._execute_skill(skill)

    async def _reactive_trigger(self, skill: Any) -> None:
        """Polling loop or always-on runner."""
        reactive = skill.reactive

        if reactive.always_on:
            while True:
                if skill.name not in self._paused:
                    await self._execute_skill(skill)
                    # On always-on, restart after completion (respecting on_failure)
                await asyncio.sleep(5)
        else:
            while True:
                if skill.name in self._paused:
                    await asyncio.sleep(10)
                    continue

                interval = _adaptive_interval(reactive)
                await asyncio.sleep(interval)

                if skill.name not in self._paused:
                    await self._execute_skill(skill)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute_skill(self, skill: Any) -> None:
        """Run all steps of a skill in sequence."""
        run_id = _run_id()
        log_path = _prepare_log_path(skill.name, run_id)

        logger.info("Starting skill run: %s run=%s", skill.name, run_id)
        self._emit({
            "event": "agent_log",
            "skill_name": skill.name,
            "run_id": run_id,
            "line": f"[gits] Starting skill {skill.name} run={run_id}",
        })

        # Insert run record
        if self._db_path:
            from ..storage.db import RunsDB
            async with RunsDB(self._db_path) as db:
                await db.insert_run(run_id=run_id, skill_name=skill.name, log_path=str(log_path))

        # Ensure tmux session for this skill
        session_name = f"ghost-runner-{skill.name}"
        await asyncio.to_thread(_ensure_runner_session, session_name)

        # Start log capture
        await asyncio.to_thread(_start_pipe_pane, session_name, str(log_path))

        exit_code = 0
        final_status = "success"

        try:
            for step in skill.steps:
                cmd, working_dir, tool_env = self._resolve_step_command(step)
                if not cmd:
                    continue

                # Merge envs: shell_env base, then tool-specific env overrides
                merged_env: dict[str, str] = {}
                if self._shell_env:
                    merged_env.update(self._shell_env)
                merged_env.update(tool_env)

                exit_code = await self._run_step(
                    skill=skill,
                    run_id=run_id,
                    session_name=session_name,
                    cmd=cmd,
                    working_dir=working_dir,
                    env=merged_env,
                    log_path=log_path,
                )

                if exit_code != 0:
                    final_status = await self._handle_failure(
                        skill=skill,
                        run_id=run_id,
                        session_name=session_name,
                        cmd=cmd,
                        working_dir=working_dir,
                        env=merged_env,
                        log_path=log_path,
                    )
                    if final_status in ("failed", "guarded"):
                        break
        except Exception:
            logger.exception("Error executing skill %s", skill.name)
            exit_code = 1
            final_status = "failed"
        finally:
            # Stop log capture
            await asyncio.to_thread(_stop_pipe_pane, session_name)

            # Update current.log
            _update_current_log(skill.name, log_path)

            # Update DB
            if self._db_path:
                from ..storage.db import RunsDB
                async with RunsDB(self._db_path) as db:
                    await db.finish_run(run_id, exit_code=exit_code, status=final_status)

            # Rotate logs
            _rotate_logs(skill.name)

            logger.info("Skill run complete: %s run=%s status=%s", skill.name, run_id, final_status)
            self._emit({
                "event": "agent_log",
                "skill_name": skill.name,
                "run_id": run_id,
                "line": f"[gits] Run complete: status={final_status} exit_code={exit_code}",
            })

    async def _run_step(
        self,
        skill: Any,
        run_id: str,
        session_name: str,
        cmd: str,
        working_dir: str | None,
        env: dict[str, str],
        log_path: Path,
    ) -> int:
        """Send a command to the tmux session and wait for completion."""
        self._emit({
            "event": "agent_log",
            "skill_name": skill.name,
            "run_id": run_id,
            "line": f"[gits] Step: {cmd}",
        })

        exit_code = await asyncio.to_thread(
            _exec_command_in_session, session_name, cmd, working_dir, env
        )
        return exit_code

    async def _handle_failure(
        self,
        skill: Any,
        run_id: str,
        session_name: str,
        cmd: str,
        working_dir: str | None,
        env: dict[str, str],
        log_path: Path,
    ) -> str:
        """Apply on_failure policy. Returns final status."""
        policy = skill.on_failure or "stop"

        # Parse retry policy
        retry_m = re.match(r'retry:\s*max\s*(\d+)', policy)
        if retry_m:
            max_retries = int(retry_m.group(1))
            for attempt in range(max_retries):
                logger.info("Retrying step (attempt %d/%d): %s", attempt + 1, max_retries, cmd)
                self._emit({
                    "event": "agent_log",
                    "skill_name": skill.name,
                    "run_id": run_id,
                    "line": f"[gits] Retry {attempt + 1}/{max_retries}: {cmd}",
                })
                exit_code = await self._run_step(
                    skill, run_id, session_name, cmd, working_dir, env, log_path
                )
                if exit_code == 0:
                    return "success"
            # Retries exhausted — trigger guard
            return await self._try_guard(skill, run_id, cmd, log_path)

        if policy == "continue":
            return "failed"
        if policy == "restart":
            return "failed"  # Caller will restart
        if policy == "notify":
            self._emit({"event": "skill_failure", "skill_name": skill.name, "run_id": run_id})
            return "failed"
        # stop or unknown
        return "failed"

    async def _try_guard(self, skill: Any, run_id: str, cmd: str, log_path: Path) -> str:
        """Trigger guard if enabled. Returns final status."""
        if not skill.guard.enabled or not self._guard_fn:
            return "failed"

        # Read last 50 lines of log
        log_tail = _read_log_tail(log_path, 50)
        try:
            action = await self._guard_fn(skill.name, run_id, cmd, log_tail)
        except Exception:
            logger.exception("Guard fn failed")
            action = "abort"

        if self._db_path:
            from ..storage.db import RunsDB
            async with RunsDB(self._db_path) as db:
                await db.update_guard_log(run_id, {"action": action, "cmd": cmd})

        if action == "retry":
            session_name = f"ghost-runner-{skill.name}"
            merged_env: dict[str, str] = dict(self._shell_env or {})
            exit_code = await self._run_step(
                skill, run_id, session_name, cmd, None, merged_env, log_path
            )
            return "success" if exit_code == 0 else "failed"
        if action == "skip":
            return "success"
        # abort or fixed
        return "guarded"


# ------------------------------------------------------------------
# tmux helpers
# ------------------------------------------------------------------

def _ensure_runner_session(session_name: str) -> None:
    """Create tmux session if it doesn't exist, with known PS1."""
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    if result.returncode != 0:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name],
            check=True,
            capture_output=True,
        )
        # Set a known PS1 for prompt detection
        subprocess.run(
            ["tmux", "send-keys", "-t", session_name,
             f"export PS1='{GITS_PROMPT}' && clear", "Enter"],
            capture_output=True,
        )
        time.sleep(0.5)


def _start_pipe_pane(session_name: str, log_path: str) -> None:
    """Start piping tmux pane output to log file."""
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["tmux", "pipe-pane", "-t", session_name, "-o", f"cat >> {log_path}"],
        capture_output=True,
    )


def _stop_pipe_pane(session_name: str) -> None:
    """Stop piping tmux pane output."""
    subprocess.run(
        ["tmux", "pipe-pane", "-t", session_name],
        capture_output=True,
    )


def _exec_command_in_session(
    session_name: str,
    cmd: str,
    working_dir: str | None,
    env: dict[str, str],
) -> int:
    """Send command to tmux session, wait for GITS_EXIT_ marker, return exit code."""
    # Optionally cd to working_dir before running the command
    if working_dir:
        full_cmd = f"cd {working_dir} && {cmd}; echo GITS_EXIT_$?"
    else:
        full_cmd = f"{cmd}; echo GITS_EXIT_$?"

    subprocess.run(
        ["tmux", "send-keys", "-t", session_name, full_cmd, "Enter"],
        capture_output=True,
    )

    # Poll pane for completion (look for GITS_EXIT_ pattern)
    deadline = time.time() + 3600  # 1 hour timeout
    while time.time() < deadline:
        time.sleep(1)
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p"],
            capture_output=True,
            text=True,
        )
        output = result.stdout
        m = re.search(r'GITS_EXIT_(\d+)', output)
        if m:
            return int(m.group(1))

    return 1  # timeout


# ------------------------------------------------------------------
# Log helpers
# ------------------------------------------------------------------

def _prepare_log_path(skill_name: str, run_id: str) -> Path:
    log_dir = AGENTS_DIR / skill_name
    log_dir.mkdir(parents=True, exist_ok=True)
    # run_id already uses dashes (colons replaced in _run_id())
    return log_dir / f"{run_id}.log"


def _update_current_log(skill_name: str, log_path: Path) -> None:
    current = AGENTS_DIR / skill_name / "current.log"
    try:
        shutil.copy2(log_path, current)
    except OSError:
        logger.warning("Failed to update current.log for %s", skill_name)


def _read_log_tail(log_path: Path, n: int) -> str:
    try:
        lines = log_path.read_text().splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def _rotate_logs(skill_name: str) -> None:
    """Keep last MAX_LOG_FILES logs; delete files older than MAX_LOG_AGE_DAYS."""
    log_dir = AGENTS_DIR / skill_name
    if not log_dir.exists():
        return

    cutoff = time.time() - MAX_LOG_AGE_DAYS * 86400
    logs = sorted(
        [f for f in log_dir.glob("*.log") if f.name != "current.log"],
        key=lambda f: f.stat().st_mtime,
    )

    # Identify files to delete by age
    to_delete = set(f for f in logs if f.stat().st_mtime < cutoff)

    # Identify excess files (beyond MAX_LOG_FILES) among remaining
    remaining = [f for f in logs if f not in to_delete]
    if len(remaining) > MAX_LOG_FILES:
        # oldest first — delete the excess from the front
        to_delete.update(remaining[: len(remaining) - MAX_LOG_FILES])

    for f in to_delete:
        try:
            f.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------
# Cron / interval helpers
# ------------------------------------------------------------------

def _seconds_until_next_cron(schedule: str) -> float:
    """Return seconds until the next cron fire time."""
    try:
        from croniter import croniter
        now = datetime.now()
        cron = croniter(schedule, now)
        next_dt = cron.get_next(datetime)
        return max(0.0, (next_dt - now).total_seconds())
    except Exception:
        logger.warning("Invalid cron schedule: %s, defaulting to 1h", schedule)
        return 3600.0


def _adaptive_interval(reactive: Any, _now: datetime | None = None) -> float:
    """Return polling interval based on time-of-day and weekday.

    ``_now`` is injectable for testing; defaults to ``datetime.now()``.
    """
    now = _now if _now is not None else datetime.now()

    try:
        ps_h, ps_m = map(int, reactive.peak_start.split(":"))
        pe_h, pe_m = map(int, reactive.peak_end.split(":"))
        now_mins = now.hour * 60 + now.minute
        peak_start_mins = ps_h * 60 + ps_m
        peak_end_mins = pe_h * 60 + pe_m
        is_weekday = now.weekday() < 5
        in_peak = is_weekday and peak_start_mins <= now_mins < peak_end_mins
        return float(reactive.peak_seconds if in_peak else reactive.off_seconds)
    except Exception:
        return float(reactive.off_seconds)
