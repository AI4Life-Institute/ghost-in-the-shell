"""CLI entry point — ``gits start`` / ``gits hook``.

Usage:
    gits start          Start the bot (Discord + tmux bridge)
    gits hook           Called by Claude Code SessionStart hook
    gits hook --install Install the hook into ~/.claude/settings.json
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
    start_p.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: auto-restart on source file changes",
    )

    # gits hook
    hook_p = sub.add_parser("hook", help="Coding CLI session hook")
    hook_p.add_argument(
        "--install",
        action="store_true",
        help="Install the hook into ~/.claude/settings.json",
    )
    hook_p.add_argument(
        "--install-copilot",
        action="store_true",
        help="Install the hook into ~/.copilot/hooks/hooks.json",
    )
    hook_p.add_argument(
        "--install-codex",
        action="store_true",
        help="Install the hook into ~/.codex/hooks.json and enable codex_hooks feature",
    )
    hook_p.add_argument(
        "--install-opencode",
        action="store_true",
        help="Install the session plugin into OpenCode config",
    )

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
    if args.dev:
        _cmd_start_dev(args)
        return

    _cmd_start_normal(args)


def _cmd_start_dev(args: argparse.Namespace) -> None:
    """Start in dev mode with auto-restart on file changes."""
    import subprocess as _sp
    from pathlib import Path

    try:
        import watchfiles
    except ImportError:
        print(
            "watchfiles not installed. Run: uv add watchfiles",
            file=sys.stderr,
        )
        sys.exit(1)

    src_dir = Path(__file__).parent
    log_level = args.log_level or "INFO"
    cmd = [sys.executable, "-m", "gits", "start", "--log-level", log_level]

    print(f"[dev] Watching {src_dir} for changes...")
    print(f"[dev] Press Ctrl-C to stop")

    proc = None
    try:
        while True:
            # Start the bot subprocess
            print(f"[dev] Starting: {' '.join(cmd)}")
            proc = _sp.Popen(cmd)

            # Wait for any .py file change
            for changes in watchfiles.watch(
                src_dir,
                watch_filter=lambda change, path: path.endswith(".py"),
            ):
                changed_files = [str(p) for _, p in changes]
                print(f"[dev] File changed: {', '.join(changed_files)}")
                break

            # Kill and restart
            print("[dev] Restarting...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except _sp.TimeoutExpired:
                proc.kill()
                proc.wait()
    except KeyboardInterrupt:
        print("\n[dev] Shutting down...")
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait()


def _cmd_start_normal(args: argparse.Namespace) -> None:
    """Start the GITS bot (normal mode)."""
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
        bind_root=settings.bind_root,
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
    """Handle the Claude Code SessionStart hook, or install the hook.

    This runs INSIDE tmux panes where bot env vars are NOT available,
    so it must NOT import config.py or anything requiring GITS_DISCORD_TOKEN.

    Normal mode: reads JSON from stdin (Claude Code pipes session info)
    and TMUX_PANE from environment to write ~/.gits/session_map.json.

    Install mode (--install): adds the hook to ~/.claude/settings.json.
    """
    import fcntl
    import re
    import shutil
    import subprocess

    # Configure logging for the hook subprocess
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )
    logger = logging.getLogger("gits.hook")

    if args.install:
        logger.info("Hook install requested (Claude)")
        sys.exit(_install_hook())

    if args.install_copilot:
        logger.info("Hook install requested (Copilot)")
        sys.exit(_install_copilot_hook())

    if args.install_codex:
        logger.info("Hook install requested (Codex)")
        sys.exit(_install_codex_hook())

    if args.install_opencode:
        logger.info("Hook install requested (OpenCode)")
        sys.exit(_install_opencode_plugin())

    # --- Normal hook processing: read JSON from stdin ---
    logger.debug("Processing hook event from stdin")
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse stdin JSON: %s", e)
        return

    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd", "")
    event = payload.get("hook_event_name", "")

    if not session_id or not event:
        logger.debug("Empty session_id or event, ignoring")
        return

    # Validate session_id is UUID format
    uuid_re = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    if not uuid_re.match(session_id):
        logger.warning("Invalid session_id format: %s", session_id)
        return

    # Validate cwd is absolute (if provided)
    if cwd and not os.path.isabs(cwd):
        logger.warning("cwd is not absolute: %s", cwd)
        return

    # Accept both Claude's "SessionStart" and Codex's "session_start"
    if event not in ("SessionStart", "session_start"):
        logger.debug("Ignoring non-SessionStart event: %s", event)
        return

    # Get tmux session:window_id for this pane
    pane_id = os.environ.get("TMUX_PANE", "")
    if not pane_id:
        logger.warning("TMUX_PANE not set, cannot determine window")
        return

    result = subprocess.run(
        [
            "tmux",
            "display-message",
            "-t",
            pane_id,
            "-p",
            "#{session_name}:#{window_id}",
        ],
        capture_output=True,
        text=True,
    )
    raw_output = result.stdout.strip()
    # Expected format: "session_name:@id"
    parts = raw_output.split(":", 1)
    if len(parts) < 2:
        logger.warning(
            "Failed to parse session:window_id from tmux (pane=%s, output=%s)",
            pane_id,
            raw_output,
        )
        return
    tmux_session_name, window_id = parts
    session_window_key = f"{tmux_session_name}:{window_id}"

    logger.debug(
        "tmux key=%s, session_id=%s, cwd=%s",
        session_window_key,
        session_id,
        cwd,
    )

    # Read-modify-write with file locking to prevent concurrent hook races
    from pathlib import Path

    gits_dir = Path.home() / ".gits"
    map_file = gits_dir / "session_map.json"
    map_file.parent.mkdir(parents=True, exist_ok=True)

    lock_path = map_file.with_suffix(".lock")
    try:
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            logger.debug("Acquired lock on %s", lock_path)
            try:
                session_map: dict[str, dict[str, str]] = {}
                if map_file.exists():
                    try:
                        session_map = json.loads(map_file.read_text())
                    except (json.JSONDecodeError, OSError):
                        logger.warning(
                            "Failed to read existing session_map, starting fresh"
                        )

                session_map[session_window_key] = {
                    "session_id": session_id,
                    "cwd": cwd,
                }

                # Atomic write: write to temp then rename
                import tempfile

                fd, tmp = tempfile.mkstemp(
                    dir=map_file.parent, suffix=".tmp"
                )
                try:
                    with open(fd, "w") as f:
                        json.dump(session_map, f, indent=2, ensure_ascii=False)
                    Path(tmp).replace(map_file)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise

                logger.info(
                    "Updated session_map: %s -> session_id=%s, cwd=%s",
                    session_window_key,
                    session_id,
                    cwd,
                )
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)
    except OSError as e:
        logger.error("Failed to write session_map: %s", e)


# -- Hook install ------------------------------------------------------------

_CLAUDE_SETTINGS_FILE = os.path.expanduser("~/.claude/settings.json")
_HOOK_COMMAND_SUFFIX = "gits hook"


def _find_gits_path() -> str:
    """Find the full path to the gits executable."""
    import shutil

    gits_path = shutil.which("gits")
    if gits_path:
        return gits_path

    # Fall back to same directory as the Python interpreter (venv installs)
    python_dir = os.path.dirname(sys.executable)
    gits_in_venv = os.path.join(python_dir, "gits")
    if os.path.exists(gits_in_venv):
        return gits_in_venv

    return "gits"


def _is_hook_installed(settings: dict) -> bool:
    """Check if gits hook is already installed in the settings."""
    hooks = settings.get("hooks", {})
    session_start = hooks.get("SessionStart", [])

    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        inner_hooks = entry.get("hooks", [])
        for h in inner_hooks:
            if not isinstance(h, dict):
                continue
            cmd = h.get("command", "")
            if cmd == _HOOK_COMMAND_SUFFIX or cmd.endswith("/" + _HOOK_COMMAND_SUFFIX):
                return True
    return False


def _install_hook() -> int:
    """Install the gits hook into Claude's settings.json. Returns 0 on success."""
    from pathlib import Path

    settings_file = Path(_CLAUDE_SETTINGS_FILE)
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error reading {settings_file}: {e}", file=sys.stderr)
            return 1

    if _is_hook_installed(settings):
        print(f"Hook already installed in {settings_file}")
        return 0

    gits_path = _find_gits_path()
    hook_command = f"{gits_path} hook"
    hook_config = {"type": "command", "command": hook_command, "timeout": 5}

    if "hooks" not in settings:
        settings["hooks"] = {}
    if "SessionStart" not in settings["hooks"]:
        settings["hooks"]["SessionStart"] = []

    settings["hooks"]["SessionStart"].append({"hooks": [hook_config]})

    try:
        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        print(f"Error writing {settings_file}: {e}", file=sys.stderr)
        return 1

    print(f"Hook installed successfully in {settings_file}")
    return 0


def _install_copilot_hook() -> int:
    """Install the gits hook into Copilot's hooks.json. Returns 0 on success.

    Copilot CLI uses ~/.copilot/hooks/hooks.json (or .github/hooks/hooks.json)
    with events like sessionStart.
    """
    from pathlib import Path

    hooks_dir = Path.home() / ".copilot" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = hooks_dir / "hooks.json"

    hooks: dict = {}
    if hooks_file.exists():
        try:
            hooks = json.loads(hooks_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error reading {hooks_file}: {e}", file=sys.stderr)
            return 1

    # Check if already installed
    session_start = hooks.get("sessionStart", [])
    gits_path = _find_gits_path()
    hook_command = f"{gits_path} hook"

    for entry in session_start:
        if isinstance(entry, dict) and entry.get("command", "").endswith("gits hook"):
            print(f"Hook already installed in {hooks_file}")
            return 0

    session_start.append({
        "type": "command",
        "command": hook_command,
        "timeout": 5000,
    })
    hooks["sessionStart"] = session_start

    try:
        hooks_file.write_text(
            json.dumps(hooks, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        print(f"Error writing {hooks_file}: {e}", file=sys.stderr)
        return 1

    print(f"Hook installed successfully in {hooks_file}")
    return 0


def _install_codex_hook() -> int:
    """Install the gits hook into Codex's hooks.json and enable codex_hooks feature.

    Codex CLI uses ~/.codex/hooks.json with session_start event hooks,
    and requires the codex_hooks feature flag in ~/.codex/config.toml.
    """
    from pathlib import Path

    # 1. Enable codex_hooks feature in config.toml
    config_file = Path.home() / ".codex" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)

    config_text = ""
    if config_file.exists():
        try:
            config_text = config_file.read_text()
        except OSError as e:
            print(f"Error reading {config_file}: {e}", file=sys.stderr)
            return 1

    if "codex_hooks" not in config_text:
        # Add [features] section with codex_hooks = true
        if "[features]" in config_text:
            config_text = config_text.replace(
                "[features]", "[features]\ncodex_hooks = true"
            )
        else:
            config_text += "\n[features]\ncodex_hooks = true\n"
        try:
            config_file.write_text(config_text)
            print(f"Enabled codex_hooks feature in {config_file}")
        except OSError as e:
            print(f"Error writing {config_file}: {e}", file=sys.stderr)
            return 1
    else:
        print(f"codex_hooks feature already in {config_file}")

    # 2. Install hook in hooks.json
    hooks_file = Path.home() / ".codex" / "hooks.json"

    hooks: dict = {}
    if hooks_file.exists():
        try:
            hooks = json.loads(hooks_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error reading {hooks_file}: {e}", file=sys.stderr)
            return 1

    # Check if already installed
    gits_path = _find_gits_path()
    hook_command = f"{gits_path} hook"

    if "hooks" not in hooks:
        hooks["hooks"] = {}
    # Codex uses PascalCase "SessionStart" as the event key
    session_start = hooks["hooks"].get("SessionStart", [])

    for entry in session_start:
        if isinstance(entry, dict):
            for h in entry.get("hooks", []):
                if isinstance(h, dict) and h.get("command", "").endswith("gits hook"):
                    print(f"Hook already installed in {hooks_file}")
                    return 0

    session_start.append({
        "hooks": [
            {
                "type": "command",
                "command": hook_command,
                "timeout": 5,
            }
        ]
    })
    hooks["hooks"]["SessionStart"] = session_start

    try:
        hooks_file.write_text(
            json.dumps(hooks, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        print(f"Error writing {hooks_file}: {e}", file=sys.stderr)
        return 1

    print(f"Hook installed successfully in {hooks_file}")
    return 0


def _install_opencode_plugin() -> int:
    """Install the GITS session plugin into OpenCode's config.

    OpenCode v1.2.26+ loads plugins declared in opencode.json via the
    ``"plugin"`` key. Directory auto-loading (``~/.config/opencode/plugins/``)
    does NOT work as of v1.2.26 despite documentation claims.

    This function:
    1. Copies the plugin package to ``~/.config/opencode/plugins/gits-session-hook/``
    2. Adds ``"gits-session-hook@file:<path>"`` to ``opencode.json``'s ``plugin`` array
    """
    from pathlib import Path
    import shutil

    # 1. Copy plugin package to a stable location
    plugin_src = Path(__file__).parent / "plugins" / "opencode"
    if not plugin_src.exists():
        print(f"Plugin source not found at {plugin_src}", file=sys.stderr)
        return 1

    plugin_dest = Path.home() / ".config" / "opencode" / "plugins" / "gits-session-hook"
    plugin_dest.mkdir(parents=True, exist_ok=True)

    for filename in ("index.mjs", "package.json"):
        src = plugin_src / filename
        dst = plugin_dest / filename
        if not src.exists():
            print(f"Missing {src}", file=sys.stderr)
            return 1
        # Overwrite if content differs (update check)
        if dst.exists() and dst.read_bytes() == src.read_bytes():
            continue
        shutil.copy2(src, dst)

    # 2. Add plugin entry to opencode.json
    config_dir = Path.home() / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "opencode.json"

    config: dict = {}
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error reading {config_file}: {e}", file=sys.stderr)
            return 1

    if "plugin" not in config:
        config["plugin"] = []

    plugin_entry = f"gits-session-hook@file:{plugin_dest}"

    # Check if already installed (any gits-session-hook entry)
    for entry in config["plugin"]:
        if isinstance(entry, str) and "gits-session-hook" in entry:
            if entry == plugin_entry:
                print(f"OpenCode plugin already installed in {config_file}")
                return 0
            # Update path if it changed
            config["plugin"].remove(entry)
            break

    config["plugin"].append(plugin_entry)

    try:
        config_file.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        print(f"Error writing {config_file}: {e}", file=sys.stderr)
        return 1

    print(f"OpenCode plugin installed: {plugin_entry}")
    print(f"  Plugin files: {plugin_dest}")
    print(f"  Config: {config_file}")
    return 0


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
