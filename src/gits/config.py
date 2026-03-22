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
