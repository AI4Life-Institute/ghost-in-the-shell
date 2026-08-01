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

    # ── Core-OS ticket origination guard (Ghost task corehk) ──────────
    # Comma-separated *line* names holding a standing core-OS improvement
    # mandate; empty means nobody does, so every core-OS ticket needs
    # disclosed consent. Declared here only so the key is legal in
    # ~/.gits/config.env — this model is validated with extra='forbid', and
    # an undeclared key there makes every Settings() raise. The guard itself
    # (gits.hooks.core_os_ticket) reads config.env with a stdlib parser,
    # because PreToolUse hooks must not import pydantic.
    ghost_core_os_mandate: str = ""
    ghost_core_os_repos: str = ""

    # ── tmux ──────────────────────────────────────────────────────────
    tmux_session_name: str = "gits"
    coding_cli_command: str = "claude"

    # ── Screenshot ────────────────────────────────────────────────────
    screenshot_font_size: int = 28

    # ── Monitoring ────────────────────────────────────────────────────
    pane_poll_interval: float = 2.0
    jsonl_poll_interval: float = 2.0
    health_check_interval: float = 5.0

    # ── Deployment drift watch (Ghost task drftnt / ghost#37) ─────────
    # Only the two knobs an operator actually turns. The alert thresholds
    # live in gits.core.drift_watch.DriftPolicy as constants on purpose:
    # this model is validated with extra='forbid' (a pydantic *default*, so
    # it is not visible in model_config above), and every key declared here
    # is one that must exist forever — an undeclared key in
    # ~/.gits/config.env makes every Settings() in the bot, the hooks and
    # the CLI raise. See ghost#18.
    #
    # There is deliberately no alert-channel key: notices go to the butler
    # home channel that already exists. ghost#18 landed
    # GITS_WATCHDOG_ALERT_CHANNEL (declared below) and the two routes coexist
    # permanently — converging them was taken up as ghost#42 and **rejected**
    # (operator answer Q1, 2026-08-01). They are not two keys for one
    # audience: the watchdog's default channel is vault-weiliu-ghost-
    # efficiency's own home channel, and drift notices belong to whoever runs
    # this checkout. Merging would move one team's alerts somewhere they do
    # not read. Do not "tidy" these into one key; see docs/drift-
    # notification.md § Where notices go.
    ghost_drift_watch_enabled: bool = True
    ghost_drift_watch_interval_s: float = 3600.0

    # ── Resource + token watchdog (Ghost task jeyuxq / ghost#18) ──────
    # Declared here only so these keys are legal in ~/.gits/config.env —
    # this model is validated with extra='forbid' (a pydantic *default*, so
    # it is not visible in model_config above), and an undeclared key there
    # makes every Settings() raise. The watchdog itself
    # (gits.core.watchdog_config) reads config.env with a stdlib parser,
    # because PreToolUse hooks must not import pydantic.
    #
    # These are untyped `str = ""` placeholders on purpose. The real
    # defaults and the tolerant float/int coercion live in
    # watchdog_config.py, which is the single source of truth for them.
    # Typing them here would fork every default across two files that can
    # silently drift, and would turn an operator's typo into a raise in
    # *every* Settings() — bot, hooks and CLI — where the watchdog's own
    # parser merely falls back to its default. Local failure beats global.
    gits_watchdog_alert_channel: str = ""
    gits_watchdog_owner_mention: str = ""
    gits_disk_watch_path: str = ""
    gits_balance_digest_hour: str = ""
    # swap-used %
    gits_swap_warn_pct: str = ""
    gits_swap_critical_pct: str = ""
    gits_swap_clear_pct: str = ""
    # tmux-server fd count
    gits_tmux_fd_limit: str = ""
    gits_tmux_fd_warn: str = ""
    gits_tmux_fd_critical: str = ""
    gits_tmux_fd_clear: str = ""
    # load avg as multiple of core count
    gits_load_warn_ratio: str = ""
    gits_load_critical_ratio: str = ""
    gits_load_clear_ratio: str = ""
    # disk-free %
    gits_disk_warn_pct: str = ""
    gits_disk_critical_pct: str = ""
    gits_disk_clear_pct: str = ""
    # mem-avail MB
    gits_mem_warn_mb: str = ""
    gits_mem_critical_mb: str = ""
    gits_mem_clear_mb: str = ""
    # token cap-%
    gits_token_warn_pct: str = ""
    gits_token_critical_pct: str = ""
    gits_token_clear_pct: str = ""
    # balance skew
    gits_skew_binding_share: str = ""
    gits_skew_score_median_mult: str = ""
    # Per-account token caps: one key per window holding a NAME=VALUE list
    # ("alice=150000000,bob=2e9"). Deliberately NOT a
    # GITS_ACCOUNT_5H_CAP_<NAME> family: an arbitrary-suffix key cannot be
    # declared in a fixed model, so extra='forbid' rejected it and the
    # feature bricked Settings() for exactly the operator who configured it.
    gits_account_5h_caps: str = ""
    gits_account_7d_caps: str = ""

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
