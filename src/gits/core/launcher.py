"""CodingCLILauncher — launch and resume coding CLI sessions.

Manages session resume for coding CLIs (claude, codex, copilot, opencode),
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
    "copilot": {"by_id": "copilot --resume {id}", "latest": "copilot --continue"},
    "opencode": {"by_id": "opencode -s {id}", "latest": "opencode -c"},
}

# Where each CLI stores session files
CLI_SESSION_PATHS: dict[str, str] = {
    "claude": "~/.claude/projects",
    "codex": "~/.codex/sessions",
    "copilot": "~/.copilot/session-state",
    "opencode": "~/.local/share/opencode/storage/session",
}


@dataclass
class CLISession:
    """A discovered coding CLI session."""

    session_id: str
    summary: str       # slug (e.g. "gleaming-dreaming-token")
    last_message: str  # last user message text, for description
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
        discoverers = {
            "claude": self._discover_claude_sessions,
            "codex": self._discover_codex_sessions,
            "copilot": self._discover_copilot_sessions,
            "opencode": self._discover_opencode_sessions,
        }
        discoverer = discoverers.get(cli)
        if discoverer:
            return discoverer(work_dir)
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
                slug = ""
                last_user = ""
                msg_count = 0
                with open(jsonl_file) as f:
                    for line in f:
                        msg_count += 1
                        try:
                            data = json.loads(line)
                            # Pick up slug from any record that has it
                            if not slug and data.get("slug"):
                                slug = data["slug"]
                            # Track last real user message
                            if data.get("type") == "user" and "message" in data:
                                content = data["message"].get("content", "")
                                text = ""
                                if isinstance(content, list):
                                    for block in content:
                                        if not isinstance(block, dict):
                                            continue
                                        if block.get("type") != "text":
                                            continue
                                        t = block.get("text", "").strip()
                                        if t and not t.startswith("<"):
                                            text = t
                                            break
                                elif isinstance(content, str):
                                    t = content.strip()
                                    if t and not t.startswith("<"):
                                        text = t
                                if text:
                                    last_user = " ".join(text.split())[:80]
                        except json.JSONDecodeError:
                            pass

                sessions.append(
                    CLISession(
                        session_id=session_id,
                        summary=slug or f"session-{session_id[:8]}",
                        last_message=last_user,
                        message_count=msg_count,
                        file_path=str(jsonl_file),
                        mtime=stat.st_mtime,
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue

        sessions.sort(key=lambda s: s.mtime, reverse=True)
        return sessions  # caller handles pagination

    def _discover_codex_sessions(self, work_dir: str) -> list[CLISession]:
        """Scan ~/.codex/sessions/ for matching Codex CLI sessions.

        Codex stores sessions at ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.
        Each JSONL file contains events; we look for the first user message
        as a summary.
        """
        codex_dir = Path.home() / ".codex" / "sessions"
        if not codex_dir.exists():
            return []

        sessions: list[CLISession] = []
        # Scan all rollout JSONL files (organized by date)
        for jsonl_file in codex_dir.rglob("rollout-*.jsonl"):
            try:
                stat = jsonl_file.stat()
                session_id = jsonl_file.stem  # e.g. "rollout-abc123"

                # Check if this session is associated with work_dir.
                # Codex JSONL has typed events; cwd is in session_meta
                # and turn_context entries under payload.cwd.
                summary = ""
                real_session_id = ""
                msg_count = 0
                cwd_match = False
                resolved_work_dir = str(Path(work_dir).resolve())

                with open(jsonl_file) as f:
                    for line_num, line in enumerate(f):
                        if line_num > 200:
                            break
                        msg_count += 1
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        event_type = data.get("type", "")
                        payload = data.get("payload", {})
                        if not isinstance(payload, dict):
                            payload = {}

                        # session_meta / turn_context have payload.cwd
                        if event_type in ("session_meta", "turn_context"):
                            event_cwd = payload.get("cwd", "")
                            if event_cwd:
                                try:
                                    if str(Path(event_cwd).resolve()) == resolved_work_dir:
                                        cwd_match = True
                                except (ValueError, OSError):
                                    pass
                            # session_meta has the real session UUID
                            if event_type == "session_meta" and payload.get("id"):
                                real_session_id = payload["id"]

                        # User message for summary: role=user in response_item.
                        # Codex injects system content (AGENTS.md, env context)
                        # as user messages. We take the LAST short user text
                        # from the first user turn as the real prompt.
                        if event_type == "response_item":
                            if payload.get("role") == "user":
                                content = payload.get("content", [])
                                if isinstance(content, list):
                                    for block in content:
                                        if isinstance(block, dict) and block.get("type") == "input_text":
                                            text = block.get("text", "").strip()
                                            if (
                                                text
                                                and not text.startswith("<")
                                                and not text.startswith("# AGENTS")
                                            ):
                                                summary = " ".join(text.split())[:80]
                            elif payload.get("role") == "assistant" and summary:
                                # Stop after the first assistant response
                                pass

                if not cwd_match:
                    continue

                # Use real UUID session_id if found, else filename stem
                if real_session_id:
                    session_id = real_session_id

                sessions.append(
                    CLISession(
                        session_id=session_id,
                        summary=summary or f"Session {session_id[:12]}",
                        last_message="",
                        message_count=msg_count,
                        file_path=str(jsonl_file),
                        mtime=stat.st_mtime,
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue

        sessions.sort(key=lambda s: s.mtime, reverse=True)
        return sessions  # caller handles pagination

    def _discover_copilot_sessions(self, work_dir: str) -> list[CLISession]:
        """Scan ~/.copilot/session-state/ for matching Copilot CLI sessions.

        Copilot stores sessions at ~/.copilot/session-state/{session-id}/
        with events.jsonl for history and workspace.yaml for metadata.
        """
        copilot_dir = Path.home() / ".copilot" / "session-state"
        if not copilot_dir.exists():
            return []

        sessions: list[CLISession] = []
        for session_dir in copilot_dir.iterdir():
            if not session_dir.is_dir():
                continue

            events_file = session_dir / "events.jsonl"
            if not events_file.exists():
                continue

            try:
                stat = events_file.stat()
                session_id = session_dir.name

                summary = ""
                msg_count = 0
                cwd_match = False

                # Check workspace.yaml for cwd match
                workspace_file = session_dir / "workspace.yaml"
                if workspace_file.exists():
                    try:
                        ws_text = workspace_file.read_text()
                        # Simple YAML parsing — look for path/cwd/workspace key
                        for ws_line in ws_text.split("\n"):
                            ws_line = ws_line.strip()
                            for key in ("path:", "cwd:", "workspace:"):
                                if ws_line.startswith(key):
                                    ws_path = ws_line[len(key) :].strip().strip("'\"")
                                    if ws_path and str(
                                        Path(ws_path).resolve()
                                    ) == str(Path(work_dir).resolve()):
                                        cwd_match = True
                    except OSError:
                        pass

                # Scan events.jsonl for cwd and summary
                with open(events_file) as f:
                    for line_num, line in enumerate(f):
                        if line_num > 200:
                            break
                        msg_count += 1
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Check for cwd match in events
                        if not cwd_match:
                            event_cwd = (
                                data.get("cwd", "")
                                or data.get("workspace", "")
                                or data.get("working_directory", "")
                            )
                            if event_cwd and str(
                                Path(event_cwd).resolve()
                            ) == str(Path(work_dir).resolve()):
                                cwd_match = True

                        # Extract first user message as summary.
                        # Copilot format: type="user.message", data.content
                        if not summary:
                            event_type = data.get("type", "")
                            event_data = data.get("data", {})
                            if not isinstance(event_data, dict):
                                event_data = {}
                            role = data.get("role", "")
                            if event_type == "user.message":
                                text = event_data.get("content", "")
                                if isinstance(text, str) and text.strip():
                                    summary = " ".join(text.strip().split())[:80]
                            elif event_type == "user" or role == "user":
                                text = (
                                    data.get("text", "")
                                    or data.get("content", "")
                                    or data.get("message", "")
                                )
                                if isinstance(text, str) and text.strip():
                                    summary = " ".join(text.strip().split())[:80]

                if not cwd_match:
                    continue

                sessions.append(
                    CLISession(
                        session_id=session_id,
                        summary=summary or f"Session {session_id[:12]}",
                        last_message="",
                        message_count=msg_count,
                        file_path=str(events_file),
                        mtime=stat.st_mtime,
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue

        sessions.sort(key=lambda s: s.mtime, reverse=True)
        return sessions  # caller handles pagination

    def _discover_opencode_sessions(self, work_dir: str) -> list[CLISession]:
        """Scan ~/.local/share/opencode/storage/ for matching OpenCode sessions.

        anomalyco/opencode stores sessions as JSON files:
          - session/<projectID>/<sessionID>.json  (session metadata)
          - message/<sessionID>/<messageID>.json  (messages)
          - project/<projectID>.json              (project metadata with path)
        """
        storage_dir = Path.home() / ".local" / "share" / "opencode" / "storage"
        if not storage_dir.exists():
            return []

        # First, find which projectID matches our work_dir
        project_dir = storage_dir / "project"
        matching_project_ids: set[str] = set()

        resolved_work_dir = str(Path(work_dir).resolve())

        if project_dir.exists():
            for proj_file in project_dir.glob("*.json"):
                try:
                    data = json.loads(proj_file.read_text())
                    # OpenCode uses "worktree" for the project path
                    proj_path = (
                        data.get("worktree", "")
                        or data.get("path", "")
                        or data.get("cwd", "")
                    )
                    if proj_path:
                        try:
                            if str(Path(proj_path).resolve()) == resolved_work_dir:
                                matching_project_ids.add(proj_file.stem)
                        except (ValueError, OSError):
                            pass
                    # Also check sandboxes list
                    for sandbox in data.get("sandboxes", []):
                        if isinstance(sandbox, str):
                            try:
                                if str(Path(sandbox).resolve()) == resolved_work_dir:
                                    matching_project_ids.add(proj_file.stem)
                            except (ValueError, OSError):
                                pass
                except (json.JSONDecodeError, OSError):
                    continue

        if not matching_project_ids:
            return []

        # Now scan sessions for those project IDs
        sessions: list[CLISession] = []
        session_base = storage_dir / "session"

        for project_id in matching_project_ids:
            session_proj_dir = session_base / project_id
            if not session_proj_dir.exists():
                continue

            for sess_file in session_proj_dir.glob("*.json"):
                try:
                    stat = sess_file.stat()
                    data = json.loads(sess_file.read_text())
                    session_id = data.get("id", sess_file.stem)
                    title = data.get("title", "") or data.get("name", "")

                    # Count messages
                    msg_dir = storage_dir / "message" / session_id
                    msg_count = (
                        len(list(msg_dir.glob("*.json")))
                        if msg_dir.exists()
                        else 0
                    )

                    sessions.append(
                        CLISession(
                            session_id=session_id,
                            summary=title or f"Session {session_id[:12]}",
                            last_message="",
                            message_count=msg_count,
                            file_path=str(sess_file),
                            mtime=stat.st_mtime,
                        )
                    )
                except (json.JSONDecodeError, OSError):
                    continue

        sessions.sort(key=lambda s: s.mtime, reverse=True)
        return sessions  # caller handles pagination
