"""Configuration — Pydantic Settings with .env support."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = {
        "env_file": [Path("~/.gits/config.env").expanduser(), ".env"],
        "env_file_encoding": "utf-8",
    }

    # ── Platform ──────────────────────────────────────────────────────
    gits_discord_token: str = ""

    # ── Access control ────────────────────────────────────────────────
    allowed_users: list[int] = []
    allowed_guilds: list[int] = []

    # ── tmux ──────────────────────────────────────────────────────────
    tmux_session_name: str = "gits"
    coding_cli_command: str = "claude"

    # ── Screenshot ────────────────────────────────────────────────────
    screenshot_font_size: int = 28

    # ── Monitoring ────────────────────────────────────────────────────
    pane_poll_interval: float = 2.0
    jsonl_poll_interval: float = 2.0
    health_check_interval: float = 5.0

    # ── Builder OS (dormant until ~/.gits/builder_tickets.json exists) ──
    # Resolution boundary for builder-os repo-relative paths (0002 §5.1, M2).
    # Absolute paths stored in the registry are resolved once against this at
    # registration time; the monitor never depends on cwd.
    builder_os_root: Path | None = None
    builder_event_poll_interval: float = 2.0
    # Command that invokes the builder-os CLI (G4 response adapter, 0002 §5.6).
    # ``driver respond`` args are appended. Split on whitespace; default assumes
    # a ``builder-os`` console script on PATH. Override for a venv/wrapper
    # (e.g. ``uv run --project /path builder-os``). Tests mock the subprocess.
    builder_os_cmd: str = "builder-os"
    # Coalesce window for driver.progress lines: at most one rendered progress
    # line per ticket per this many seconds (F7 / 0002 §5.2).
    builder_progress_coalesce_seconds: float = 60.0

    # ── Security ──────────────────────────────────────────────────────
    allowed_paths: list[str] = []

    # ── Directories ───────────────────────────────────────────────────
    gits_dir: Path = Path("~/.gits")
    bind_root: Path | None = None
    log_level: str = "INFO"

    # ── Thread ────────────────────────────────────────────────────────
    thread_auto_archive_minutes: int = 10080  # 7 days

    # ── WeChat ────────────────────────────────────────────────────────
    gits_default_path: Path | None = None  # auto-bind path for WeChat

    @property
    def state_dir(self) -> Path:
        """Expanded state directory path."""
        return self.gits_dir.expanduser()

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def session_map_file(self) -> Path:
        return self.state_dir / "session_map.json"

    @property
    def config_file(self) -> Path:
        return self.state_dir / "config.json"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "gits.log"

    @property
    def subscriptions_dir(self) -> Path:
        return self.state_dir / "subscriptions"

    @property
    def subscriptions_manifest_file(self) -> Path:
        return self.subscriptions_dir / "manifest.json"

    @property
    def credential_lock_file(self) -> Path:
        return self.state_dir / ".switch.lock"

    @property
    def quota_patterns_file(self) -> Path:
        return self.state_dir / "quota_patterns.yaml"

    @property
    def builder_tickets_file(self) -> Path:
        """Ghost-owned builder ticket registry (0002 §5.1, G1)."""
        return self.state_dir / "builder_tickets.json"

    @property
    def builder_event_offsets_file(self) -> Path:
        """BuilderEventMonitor offset + projection-receipt store (0002 §5.4)."""
        return self.state_dir / "builder_event_offsets.json"

    @property
    def builder_humans_file(self) -> Path:
        """Ghost-local actor map for the response adapter (0002 §5.6, §11.4).

        ``{"<discord_user_id>": "<human_builder_id>"}``. Fail-closed: absent or
        unmapped ⇒ the adapter refuses a decision. Machine config, created at
        activation — never seeded in the repo. (The eventual home is an
        ``discord_user_id`` field on the org node; kept ghost-local for the MVP
        so the org schema is untouched.)
        """
        return self.state_dir / "builder_humans.json"

    @property
    def builder_renderer_state_file(self) -> Path:
        """BuilderRenderer dedup + card index (0002 §5.2, §4.3).

        Persists ``event_id → discord_message_id`` and a per-decision card
        record so replay renders nothing new and the "recorded → delivered"
        flip can find the card after a restart.
        """
        return self.state_dir / "builder_renderer.json"

    @property
    def builder_start_journal_file(self) -> Path:
        """Crash-safe ``/bos start`` token journal (B2).

        Records the minted capability token before ``ticket admit`` so a crash
        between admit and the registry write can be retried without minting a new
        token (which would strand every later human response as unauthorized).
        """
        return self.state_dir / "builder_start_journal.json"

    @property
    def builder_forced_forward_log(self) -> Path:
        """Ghost-side audit of ``/bos forward`` overrides (0002 §5.3).

        Ghost never writes into builder-os ``runtime-state/`` (§5.5), so the
        forced-forward audit record lives here rather than in the ticket's
        ``inputs.jsonl``. See the response adapter for the record shape.
        """
        return self.state_dir / "builder_forced_forwards.jsonl"
