"""CLI entry point — ``gits start`` / ``gits hook``.

Usage:
    gits start          Start the bot (Discord + tmux bridge)
    gits hook           Called by Claude Code SessionStart hook
    gits status         Show current bindings
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="gits",
        description="Ghost in the Shell — Social platform <-> tmux bridge",
    )
    sub = parser.add_subparsers(dest="command")

    # gits start
    start_p = sub.add_parser("start", help="Start the bot")
    start_p.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level",
    )

    # gits hook
    hook_p = sub.add_parser("hook", help="Claude Code session hook")
    hook_p.add_argument("--session-id", default=None, help="Session ID")
    hook_p.add_argument("--window-name", default=None, help="tmux window name")

    # gits status
    sub.add_parser("status", help="Show current bindings")

    args = parser.parse_args()

    if args.command == "start":
        _cmd_start(args)
    elif args.command == "hook":
        _cmd_hook(args)
    elif args.command == "status":
        _cmd_status(args)
    else:
        parser.print_help()
        sys.exit(1)


def _cmd_start(args: argparse.Namespace) -> None:
    """Start the GITS bot."""
    from .config import Settings

    settings = Settings()

    # Ensure state directory exists
    settings.state_dir.mkdir(parents=True, exist_ok=True)

    # Configure logging
    log_level = args.log_level or settings.log_level
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(settings.log_file, mode="a"),
        ],
    )

    logger = logging.getLogger("gits")
    logger.info("Starting Ghost in the Shell v%s", _get_version())

    # Validate token
    if not settings.gits_discord_token:
        logger.error("GITS_DISCORD_TOKEN not set. Check your .env file.")
        sys.exit(1)

    # Ensure state directory
    settings.state_dir.mkdir(parents=True, exist_ok=True)

    # Import and wire modules
    from .adapters.discord.bot import DiscordAdapter
    from .core.engine import Engine

    engine = Engine(settings)
    adapter = DiscordAdapter(
        token=settings.gits_discord_token,
        allowed_users=settings.allowed_users,
        allowed_guilds=settings.allowed_guilds,
    )

    # Wire adapter <-> engine
    engine.set_adapter(adapter)
    adapter.set_engine(engine)

    # Register message forwarding and button click handling
    adapter.on_message(engine.handle_message)
    adapter.on_button_click(engine.handle_button_click)

    async def _run() -> None:
        await engine.start()
        try:
            await adapter.start()
        finally:
            await engine.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")


def _cmd_hook(args: argparse.Namespace) -> None:
    """Handle the Claude Code SessionStart hook.

    This is called by Claude Code when a new session starts inside tmux.
    It reads session information and persists the mapping.

    The hook reads from stdin (Claude Code pipes session info) and
    from environment variables (TMUX_PANE for window identification).
    """
    # Read session info from environment / stdin
    session_id = args.session_id or os.environ.get("CLAUDE_SESSION_ID", "")
    tmux_pane = os.environ.get("TMUX_PANE", "")

    if not session_id:
        # Try to read from stdin (Claude Code pipes JSON)
        try:
            data = json.loads(sys.stdin.read())
            session_id = data.get("session_id", "")
        except (json.JSONDecodeError, OSError):
            pass

    if not session_id or not tmux_pane:
        # Not enough info — silently exit
        return

    from .config import Settings

    settings = Settings()
    state_dir = settings.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write to session_map.json
    session_map_file = state_dir / "session_map.json"
    session_map: dict[str, str] = {}
    if session_map_file.exists():
        try:
            with open(session_map_file) as f:
                session_map = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    session_map[tmux_pane] = session_id

    with open(session_map_file, "w") as f:
        json.dump(session_map, f, indent=2)

    # Also write a notification file that the bot can watch
    notify_file = state_dir / "hook_notify.json"
    with open(notify_file, "w") as f:
        json.dump(
            {"tmux_pane": tmux_pane, "session_id": session_id},
            f,
        )


def _cmd_status(args: argparse.Namespace) -> None:
    """Show current bindings."""
    from .config import Settings

    settings = Settings()
    state_file = settings.state_dir / "state.json"

    if not state_file.exists():
        print("No bindings found.")
        return

    try:
        with open(state_file) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        print("Could not read state file.")
        return

    bindings = data.get("bindings", {})
    if not bindings:
        print("No active bindings.")
        return

    print(f"Active bindings ({len(bindings)}):")
    print("-" * 60)
    for channel_id, b in bindings.items():
        print(f"  Channel: {channel_id}")
        print(f"    Window: {b.get('window_name', '?')} ({b.get('window_id', '?')})")
        print(f"    Dir:    {b.get('work_dir', '?')}")
        print(f"    CLI:    {b.get('coding_cli', '?')}")
        if b.get("cli_session_id"):
            print(f"    Session: {b['cli_session_id'][:16]}...")
        print()


def _get_version() -> str:
    try:
        from . import __version__

        return __version__
    except ImportError:
        return "unknown"


if __name__ == "__main__":
    main()
