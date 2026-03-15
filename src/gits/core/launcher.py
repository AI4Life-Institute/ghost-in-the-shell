"""CodingCLILauncher — launch and resume coding CLI sessions.

Manages session resume for coding CLIs (claude, codex, opencode),
persists a window-to-session mapping, and discovers existing sessions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# CLI session resume command templates
RESUME_TEMPLATES: dict[str, dict[str, str]] = {
    "claude": {"by_id": "claude --resume {id}", "latest": "claude --continue"},
    "codex": {"by_id": "codex resume {id}", "latest": "codex resume --last"},
    "opencode": {"by_id": "opencode --session {id}", "latest": "opencode --continue"},
}


@dataclass
class CLISession:
    """A discovered coding CLI session."""

    session_id: str
    summary: str
    message_count: int
    file_path: str
    mtime: float


class CodingCLILauncher:
    """Launch coding CLIs with session resume support."""

    def __init__(self, session_map_path: Path):
        self.session_map_path = session_map_path
        self._session_map: dict[str, str] = {}  # window_name -> session_id
        self._load_map()

    def _load_map(self) -> None:
        if self.session_map_path.exists():
            try:
                with open(self.session_map_path) as f:
                    self._session_map = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._session_map = {}

    def get_session_id(self, window_name: str) -> str | None:
        return self._session_map.get(window_name)

    def set_session_id(self, window_name: str, session_id: str) -> None:
        self._session_map[window_name] = session_id
        # persist
        self.session_map_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.session_map_path, "w") as f:
            json.dump(self._session_map, f, indent=2)

    def build_launch_command(
        self,
        cli: str = "claude",
        session_id: str | None = None,
        work_dir: str | None = None,
    ) -> str:
        """Build the CLI launch/resume command string.

        When *session_id* is provided the CLI is launched in resume mode.
        When it is ``None`` or empty, a **fresh** session is started
        (just the bare CLI command — no ``--continue``).
        """
        templates = RESUME_TEMPLATES.get(cli)
        if templates and session_id:
            return templates["by_id"].format(id=session_id)
        # No session_id → start fresh (don't use --continue)
        return cli

    def discover_sessions(self, work_dir: str, cli: str = "claude") -> list[CLISession]:
        """Discover existing CLI sessions for a given directory."""
        if cli == "claude":
            return self._discover_claude_sessions(work_dir)
        if cli == "codex":
            return self._discover_codex_sessions(work_dir)
        return []

    def _discover_claude_sessions(self, work_dir: str) -> list[CLISession]:
        """Scan ~/.claude/projects/<dir-hash>/ for JSONL session files."""
        claude_projects = Path.home() / ".claude" / "projects"
        if not claude_projects.exists():
            return []

        # Claude Code uses directory path with / replaced by - as hash.
        # The exact escaping may vary, so try exact match first then scan.
        dir_hash = work_dir.replace("/", "-")

        project_dir = claude_projects / dir_hash
        if not project_dir.exists():
            # Try without leading dash
            alt = claude_projects / dir_hash.lstrip("-")
            if alt.exists():
                project_dir = alt
            else:
                # Scan for a project dir whose name roughly matches
                try:
                    for d in claude_projects.iterdir():
                        if not d.is_dir():
                            continue
                        # Normalise both to lowercase dashes for comparison
                        norm_d = d.name.lower().replace("_", "-")
                        norm_hash = dir_hash.lower().replace("_", "-").lstrip("-")
                        if norm_d == norm_hash or norm_d.lstrip("-") == norm_hash:
                            project_dir = d
                            break
                    else:
                        return []
                except OSError:
                    return []

        sessions: list[CLISession] = []
        for jsonl_file in project_dir.glob("*.jsonl"):
            try:
                stat = jsonl_file.stat()
                session_id = jsonl_file.stem
                # Read first line for summary
                summary = ""
                msg_count = 0
                with open(jsonl_file) as f:
                    for line in f:
                        msg_count += 1
                        if msg_count == 1:
                            try:
                                data = json.loads(line)
                                if "message" in data:
                                    content = data["message"].get("content", "")
                                    if isinstance(content, list):
                                        for block in content:
                                            if (
                                                isinstance(block, dict)
                                                and block.get("type") == "text"
                                            ):
                                                summary = block.get("text", "")[:60]
                                                break
                                    elif isinstance(content, str):
                                        summary = content[:60]
                            except json.JSONDecodeError:
                                pass

                sessions.append(
                    CLISession(
                        session_id=session_id,
                        summary=summary or f"Session {session_id[:8]}",
                        message_count=msg_count,
                        file_path=str(jsonl_file),
                        mtime=stat.st_mtime,
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue

        sessions.sort(key=lambda s: s.mtime, reverse=True)
        return sessions[:10]  # top 10 most recent

    def _discover_codex_sessions(self, work_dir: str) -> list[CLISession]:
        """Scan ~/.codex/sessions/ for matching sessions."""
        codex_dir = Path.home() / ".codex" / "sessions"
        if not codex_dir.exists():
            return []
        # TODO: implement codex session discovery when format is known
        return []
