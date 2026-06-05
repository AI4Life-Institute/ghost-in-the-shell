"""CodingCLILauncher — launch and resume coding CLI sessions.

Manages session resume for coding CLIs (claude, codex, copilot, opencode),
persists a window-to-session mapping, and discovers existing sessions.

Custom CLI aliases can be defined in ~/.gits/config.json:

    {
      "cli_aliases": {
        "clpy": {
          "type": "claude",
          "cmd": "clpy",
          "resume_by_id": "clpy --resume {id}"
        }
      }
    }

``type`` controls session discovery and permission-flag logic; ``cmd`` and
``resume_by_id`` override the actual commands that are run.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

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


def prefix_account_env(base: str, account_dir: Path) -> str:
    """Prepend ``CLAUDE_CONFIG_DIR=<dir>`` to a CLI command string.

    The single shared idiom for per-account claude isolation: the variable
    is inlined into the command string (run by tmux via the shell), NOT
    passed as a subprocess ``env=`` kwarg — when a tmux server is already
    running, ``env=`` only affects the short-lived client process and the
    pane inherits the *server's* environment instead (task [[mfgft7]]).
    """
    return f"CLAUDE_CONFIG_DIR={shlex.quote(str(account_dir))} {base}"


class ResolvedCLI(NamedTuple):
    """Resolved alias → concrete CLI info."""

    base_type: str        # "claude" | "codex" | "copilot" | "opencode"
    cmd: str              # launch command (e.g. "clpy")
    resume_by_id: str     # resume template, e.g. "clpy --resume {id}"
    session_path: str | None = None  # override session storage dir (e.g. "~/.claude-personal/projects")
    config_dir: str | None = None    # override Claude config dir (e.g. "~/.claude-personal")


@dataclass
class CLISession:
    """A discovered coding CLI session."""

    session_id: str
    summary: str       # slug (e.g. "gleaming-dreaming-token")
    last_message: str  # last user message text, for description
    message_count: int
    file_path: str
    mtime: float
    source_cli: str = ""  # which CLI this session belongs to (set by discover_all_sessions)
    first_message: str = ""  # first user message text, used as picker label


class CodingCLILauncher:
    """Launch coding CLIs with session resume support."""

    def __init__(
        self,
        session_map_path: Path,
        config_path: Path | None = None,
        active_env_file: Path | None = None,
        account_layout: object | None = None,
        account_vault: object | None = None,
    ):
        self.session_map_path = session_map_path
        self.config_path = config_path or (session_map_path.parent / "config.json")
        # ``active_env_file`` is preserved as a no-op for V1 transition
        # compatibility with the deprecated SubscriptionVault. Per openspec
        # change ``add-multi-account-hotswap``, account isolation now uses
        # ``CLAUDE_CONFIG_DIR`` injection (see ``build_launch_command`` and
        # ``gits.core.account.AccountLayout``); the env-source-file mechanism
        # is no longer applied to launch commands.
        self.active_env_file = active_env_file
        # Per Phase 0.5: account-aware path resolution. Lazy-imported so
        # tests can construct a launcher without account state.
        if account_layout is None:
            from .account import AccountLayout
            account_layout = AccountLayout()
        self._account_layout = account_layout
        # Per ``add-default-account-native-and-refresh``: when the binding's
        # account name equals ``manifest.default``, we skip CLAUDE_CONFIG_DIR
        # injection and use ~/.claude/ natively. The vault is optional —
        # when None (test/legacy construction), no default-translation
        # happens and behavior matches the original isolation rules.
        self._account_vault = account_vault
        self._session_map: dict[str, str] = {}  # window_name -> session_id
        self._aliases: dict[str, dict] = {}     # alias_name -> alias config
        self._load_map()
        self._load_aliases()

    def _effective_account(self, claude_account: str | None) -> str | None:
        from .account import effective_account
        return effective_account(claude_account, self._account_vault)

    def _load_map(self) -> None:
        if self.session_map_path.exists():
            try:
                with open(self.session_map_path) as f:
                    self._session_map = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._session_map = {}

    def _load_aliases(self) -> None:
        """Load cli_aliases from ~/.gits/config.json (silently ignore missing)."""
        if self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    data = json.load(f)
                self._aliases = data.get("cli_aliases", {})
            except (json.JSONDecodeError, OSError):
                self._aliases = {}

    def resolve_cli(self, cli: str, claude_account: str | None = None) -> ResolvedCLI:
        """Resolve a cli name / alias to its concrete launch info.

        For built-in names (claude, codex, …) returns defaults.
        For user-defined aliases from config.json, overrides cmd and
        resume_by_id while inheriting the base_type's session-discovery logic.

        When ``claude_account`` is set AND the resolved base type is
        ``claude``, ``session_path`` and ``config_dir`` are overridden by
        :class:`AccountLayout` to point at ``~/.claude-{name}/`` (per
        openspec change ``add-multi-account-hotswap``, Phase 0.5). Account
        precedence beats any alias-level ``session_path`` / ``config_dir``
        because account selection is a runtime decision while alias config
        is a static install.
        """
        alias_cfg = self._aliases.get(cli)
        if alias_cfg:
            base = alias_cfg.get("type", "claude")
            cmd = alias_cfg.get("cmd", cli)
            default_resume = RESUME_TEMPLATES.get(base, {}).get("by_id", f"{cli} --resume {{id}}")
            resume = alias_cfg.get("resume_by_id", default_resume.replace(base, cmd, 1))
            session_path = alias_cfg.get("session_path") or None
            config_dir = alias_cfg.get("config_dir") or None
            resolved = ResolvedCLI(
                base_type=base, cmd=cmd, resume_by_id=resume,
                session_path=session_path, config_dir=config_dir,
            )
        else:
            # Built-in CLI — derive from RESUME_TEMPLATES
            templates = RESUME_TEMPLATES.get(cli, {})
            resolved = ResolvedCLI(
                base_type=cli,
                cmd=cli,
                resume_by_id=templates.get("by_id", f"{cli} --resume {{id}}"),
            )

        # Account-aware override (Phase 0.5). Only applies when the resolved
        # base type is claude — codex/copilot/opencode have their own auth.
        # Defensive: only act on str account names (test fixtures sometimes
        # pass MagicMock for legacy fields). Default account translates to
        # None here so we use ~/.claude/ paths (no override applied).
        effective = self._effective_account(claude_account) if isinstance(claude_account, str) else claude_account
        if isinstance(effective, str) and resolved.base_type == "claude":
            account_dir = self._account_layout.account_dir(effective)
            resolved = ResolvedCLI(
                base_type=resolved.base_type,
                cmd=resolved.cmd,
                resume_by_id=resolved.resume_by_id,
                session_path=str(account_dir / "projects"),
                config_dir=str(account_dir),
            )
        return resolved

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
        claude_account: str | None = None,
    ) -> str:
        """Build the CLI launch/resume command string.

        When *session_id* is provided the CLI is launched in resume mode.
        When it is ``None`` or empty, a **fresh** session is started
        (just the bare CLI command — no ``--continue``).

        Account isolation (per openspec change ``add-multi-account-hotswap``):
        when ``claude_account`` is set AND the resolved base type is
        ``claude``, ``CLAUDE_CONFIG_DIR=$HOME/.claude-{name}`` is prepended
        to the command so the spawned claude process reads its credentials
        and writes its session JSONL to the per-account directory. For
        codex / copilot / opencode the account argument is ignored (they
        have their own auth mechanisms).
        """
        resolved = self.resolve_cli(cli, claude_account=claude_account)
        if session_id:
            base = resolved.resume_by_id.format(id=session_id)
        else:
            base = resolved.cmd

        # Default account → effective is None → no CLAUDE_CONFIG_DIR injected,
        # so claude uses ~/.claude/ natively and its own keychain refresh loop
        # stays warm (see ``add-default-account-native-and-refresh``).
        effective = self._effective_account(claude_account) if isinstance(claude_account, str) else claude_account
        if isinstance(effective, str) and resolved.base_type == "claude":
            account_dir = self._account_layout.account_dir(effective)
            return prefix_account_env(base, account_dir)
        return base

    def get_session_file(
        self,
        work_dir: str,
        cli: str,
        session_id: str,
        claude_account: str | None = None,
    ) -> str | None:
        """Return the file path for a known session ID, or None if not found.

        For Claude, the path is reconstructed directly (fast). When
        ``claude_account`` is supplied, the search is scoped to that
        account's ``~/.claude-{name}/projects/`` directory; otherwise the
        legacy ``~/.claude/projects/`` is used.

        For other CLIs, we scan discover_sessions and match by session_id;
        ``claude_account`` is ignored for non-claude bases.
        """
        resolved = self.resolve_cli(cli, claude_account=claude_account)
        if resolved.base_type == "claude":
            claude_projects = (
                Path(resolved.session_path).expanduser()
                if resolved.session_path
                else self._account_layout.legacy_claude_dir() / "projects"
            )
            dir_hash = work_dir.replace("/", "-")
            for candidate in (dir_hash, dir_hash.lstrip("-")):
                p = claude_projects / candidate / f"{session_id}.jsonl"
                if p.exists():
                    return str(p)
            # Fallback: scan project dirs
            try:
                for d in claude_projects.iterdir():
                    if not d.is_dir():
                        continue
                    p = d / f"{session_id}.jsonl"
                    if p.exists():
                        return str(p)
            except OSError:
                pass
            return None

        # Generic: scan and match by session_id
        for s in self.discover_sessions(work_dir, cli):
            if s.session_id == session_id:
                return s.file_path
        return None

    def discover_sessions(
        self,
        work_dir: str,
        cli: str = "claude",
        claude_account: str | None = None,
    ) -> list[CLISession]:
        """Discover existing CLI sessions for a given directory.

        ``claude_account`` (Phase 0.5): when set and the CLI base type is
        ``claude``, sessions are discovered under ``~/.claude-{name}/projects/``
        instead of ``~/.claude/projects/``.
        """
        resolved = self.resolve_cli(cli, claude_account=claude_account)
        discoverers = {
            "claude": self._discover_claude_sessions,
            "codex": self._discover_codex_sessions,
            "copilot": self._discover_copilot_sessions,
            "opencode": self._discover_opencode_sessions,
        }
        discoverer = discoverers.get(resolved.base_type)
        if discoverer:
            sessions = discoverer(work_dir, session_path=resolved.session_path)
            for s in sessions:
                s.source_cli = resolved.base_type
            return sessions
        return []

    def discover_all_sessions(
        self,
        work_dir: str,
        target_cli: str = "claude",
        claude_account: str | None = None,
    ) -> list[CLISession]:
        """Discover sessions from all CLIs, not just the target.

        Target-CLI sessions come first (sorted by mtime), then sessions from
        other CLIs sorted by mtime.  Each session has ``source_cli`` set so
        callers can detect cross-CLI import candidates.

        ``claude_account`` is forwarded to the target discovery so /info and
        the session-picker can look in the binding's per-account dir.
        Non-claude bases ignore the account argument.
        """
        resolved = self.resolve_cli(target_cli, claude_account=claude_account)
        target_type = resolved.base_type

        target_sessions = self.discover_sessions(
            work_dir, target_cli, claude_account=claude_account
        )

        other_sessions: list[CLISession] = []
        for cli_type in RESUME_TEMPLATES:
            if cli_type == target_type:
                continue
            try:
                sessions = self.discover_sessions(work_dir, cli_type)
                other_sessions.extend(sessions)
            except Exception:
                pass

        other_sessions.sort(key=lambda s: s.mtime, reverse=True)
        return target_sessions + other_sessions

    # ------------------------------------------------------------------
    # Cross-CLI conversation extraction
    # ------------------------------------------------------------------

    def extract_conversation_text(self, session: CLISession, max_chars: int = 8000) -> str:
        """Extract conversation turns from a session as human-readable markdown.

        Returns a string of ``**USER**: …`` / ``**ASSISTANT**: …`` blocks,
        truncated to *max_chars* (keeping the most recent messages).
        """
        cli = session.source_cli or "claude"
        extractors = {
            "claude": self._extract_claude_text,
            "codex": self._extract_codex_text,
            "copilot": self._extract_copilot_text,
            "opencode": self._extract_opencode_text,
        }
        extractor = extractors.get(cli)
        if not extractor:
            return ""
        try:
            messages = extractor(session.file_path)
        except Exception:
            return ""

        lines = [f"**{role.upper()}**: {text}\n" for role, text in messages]
        full = "\n".join(lines)

        if len(full) <= max_chars:
            return full

        # Keep most-recent messages when truncating
        kept: list[str] = []
        total = 0
        for line in reversed(lines):
            if total + len(line) > max_chars:
                break
            kept.append(line)
            total += len(line)
        kept.reverse()
        return "[…earlier messages omitted…]\n\n" + "\n".join(kept)

    def _extract_claude_text(self, file_path: str) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        with open(file_path) as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = data.get("type", "")
                if role not in ("user", "assistant"):
                    continue
                content = data.get("message", {}).get("content", "")
                text = ""
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            t = block.get("text", "").strip()
                            if t and not t.startswith("<"):
                                text += t + " "
                elif isinstance(content, str):
                    text = content.strip()
                text = text.strip()
                if text:
                    messages.append((role, text[:1000]))
        return messages

    def _extract_codex_text(self, file_path: str) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        with open(file_path) as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("type") != "response_item":
                    continue
                payload = data.get("payload", {})
                if not isinstance(payload, dict):
                    continue
                role = payload.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                content = payload.get("content", [])
                text = ""
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict):
                            text += (
                                block.get("text", "")
                                or block.get("input_text", "")
                                or block.get("output_text", "")
                            )
                text = text.strip()
                if text and not text.startswith("<") and not text.startswith("# AGENTS"):
                    messages.append((role, text[:1000]))
        return messages

    def _extract_copilot_text(self, file_path: str) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        with open(file_path) as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = data.get("role", "")
                event_type = data.get("type", "")
                if event_type == "user.message" or role == "user":
                    role = "user"
                    event_data = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
                    text = event_data.get("content", "") or data.get("text", "") or data.get("content", "")
                elif event_type == "assistant.message" or role == "assistant":
                    role = "assistant"
                    event_data = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
                    text = event_data.get("content", "") or data.get("text", "") or data.get("content", "")
                else:
                    continue
                if isinstance(text, str):
                    text = text.strip()
                    if text:
                        messages.append((role, text[:1000]))
        return messages

    def _extract_opencode_text(self, file_path: str) -> list[tuple[str, str]]:
        sess_file = Path(file_path)
        # message dir lives at storage_root/message/{session_id}/
        # session file lives at storage_root/session/{project_id}/{session_id}.json
        storage_dir = sess_file.parent.parent.parent
        try:
            sess_data = json.loads(sess_file.read_text())
            session_id = sess_data.get("id", sess_file.stem)
        except (json.JSONDecodeError, OSError):
            return []
        msg_dir = storage_dir / "message" / session_id
        if not msg_dir.exists():
            return []
        messages: list[tuple[str, str]] = []
        for msg_file in sorted(msg_dir.glob("*.json")):
            try:
                data = json.loads(msg_file.read_text())
                role = data.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                content = data.get("content", "") or data.get("text", "")
                if isinstance(content, list):
                    text = " ".join(
                        part.get("text", "") for part in content if isinstance(part, dict)
                    )
                else:
                    text = str(content)
                text = text.strip()
                if text:
                    messages.append((role, text[:1000]))
            except (json.JSONDecodeError, OSError):
                continue
        return messages

    def _discover_claude_sessions(self, work_dir: str, session_path: str | None = None) -> list[CLISession]:
        """Scan <session_path>/<dir-hash>/ for JSONL session files.

        *session_path* defaults to ``~/.claude/projects`` but can be overridden
        via the alias ``session_path`` config key to support separate Claude
        config dirs (e.g. ``~/.claude-work/projects``).
        """
        claude_projects = Path(session_path).expanduser() if session_path else Path.home() / ".claude" / "projects"
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
                first_user = ""
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
                            # Track first and last real user message
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
                                    if not first_user:
                                        first_user = " ".join(text.split())[:80]
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
                        first_message=first_user,
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue

        sessions.sort(key=lambda s: s.mtime, reverse=True)
        return sessions  # caller handles pagination

    def _discover_codex_sessions(self, work_dir: str, session_path: str | None = None) -> list[CLISession]:
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

    def _discover_copilot_sessions(self, work_dir: str, session_path: str | None = None) -> list[CLISession]:
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

    def _discover_opencode_sessions(self, work_dir: str, session_path: str | None = None) -> list[CLISession]:
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
