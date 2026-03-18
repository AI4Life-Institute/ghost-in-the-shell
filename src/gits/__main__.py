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
from typing import Any


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

    # gits info
    sub.add_parser("info", help="Show current bindings")

    # gits desktop  (launched by the Electron shell)
    desktop_p = sub.add_parser("desktop", help="Desktop app backend (IPC over stdio)")
    desktop_p.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args()

    if args.command == "start" or args.command is None:
        if args.command is None:
            # Default: inject start defaults
            args.log_level = None
            args.dev = False
        _cmd_start(args)
    elif args.command == "hook":
        _cmd_hook(args)
    elif args.command == "info":
        _cmd_status(args)
    elif args.command == "desktop":
        _cmd_desktop(args)


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

    # Skip non-interactive (one-shot) claude invocations such as `claude -p`.
    # These are background tasks launched by orchestrators; updating session_map
    # for them would steal the window's interactive session and break forwarding.
    # Detection: check if the grandparent claude process was started with -p /
    # --print flags by reading /proc/<ppid>/cmdline.
    try:
        ppid = os.getppid()
        cmdline_raw = Path(f"/proc/{ppid}/cmdline").read_bytes()
        cmdline_args = cmdline_raw.split(b"\x00")
        is_noninteractive = any(
            arg in (b"-p", b"--print") for arg in cmdline_args
        )
        if is_noninteractive:
            logger.debug(
                "Skipping session_map update: parent claude (pid=%d) is "
                "running in non-interactive mode (-p/--print)",
                ppid,
            )
            return
    except OSError:
        pass  # /proc not available (non-Linux), proceed normally

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

    # Also write a per-pane session file: ~/.gits/pane_sessions/<TMUX_PANE>.json
    # This is the authoritative source for pane-based session detection and
    # avoids all mtime-based race conditions when multiple windows share a
    # project directory.  The monitor reads /proc/<claude_pid>/environ to get
    # TMUX_PANE, then looks up this file directly.
    import tempfile as _tempfile
    pane_sessions_dir = Path.home() / ".gits" / "pane_sessions"
    try:
        pane_sessions_dir.mkdir(parents=True, exist_ok=True)
        pane_file = pane_sessions_dir / f"{pane_id}.json"
        payload_out = {
            "session_id": session_id,
            "cwd": cwd,
            "window_key": session_window_key,
        }
        fd2, tmp2 = _tempfile.mkstemp(dir=pane_sessions_dir, suffix=".tmp")
        try:
            with open(fd2, "w") as f:
                json.dump(payload_out, f)
            Path(tmp2).replace(pane_file)
            logger.debug("Wrote pane session file: %s -> %s", pane_file.name, session_id)
        except BaseException:
            Path(tmp2).unlink(missing_ok=True)
            raise
    except OSError as e:
        logger.warning("Failed to write pane session file: %s", e)


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


def _install_hook(config_dir: str | None = None) -> int:
    """Install the gits hook into Claude's settings.json. Returns 0 on success.

    *config_dir* overrides the default ``~/.claude`` directory so the hook
    can be installed for aliases that use a custom ``CLAUDE_CONFIG_DIR``.
    """
    from pathlib import Path

    if config_dir:
        settings_file = Path(config_dir).expanduser() / "settings.json"
    else:
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


def _cmd_desktop(args: argparse.Namespace) -> None:
    """Desktop backend: read JSON commands from stdin, write JSON events to stdout.

    Protocol (newline-delimited JSON):
      stdin:  {"cmd": "sessions"}
      stdin:  {"cmd": "send",    "payload": {"channel_id": "...", "text": "..."}}
      stdin:  {"cmd": "capture", "payload": {"channel_id": "..."}}
      stdin:  {"cmd": "button",  "payload": {"channel_id": "...", "callback_data": "..."}}
      stdin:  {"cmd": "ping"}
      stdout: {"event": "ready",    "version": "..."}
      stdout: {"event": "sessions", "sessions": [...]}
      stdout: {"event": "output",   "channel_id": "...", "text": "..."}
      stdout: {"event": "capture",  "channel_id": "...", "text": "..."}
      stdout: {"event": "pong"}
      stdout: {"event": "error",    "msg": "..."}
    """
    log_level = (args.log_level or "WARNING").upper()  # quiet by default — stdout is IPC
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,  # keep stdout clean for IPC
    )
    logger = logging.getLogger("gits.desktop")
    logger.info("Desktop backend starting")

    from .adapters.desktop import DesktopAdapter
    from .adapters.base import IncomingMessage
    from .core.engine import Engine
    from .config import Settings

    settings = Settings()
    settings.state_dir.mkdir(parents=True, exist_ok=True)

    engine = Engine(settings)

    async def _run() -> None:
        loop = asyncio.get_event_loop()
        skill_runner: Any = None

        def _emit(obj: dict) -> None:
            """Write a JSON event to stdout (Electron picks this up)."""
            try:
                print(json.dumps(obj), flush=True)
            except Exception:
                pass

        # Wire up the DesktopAdapter
        adapter = DesktopAdapter(emit=_emit)
        engine.set_adapter(adapter)
        adapter.on_message(engine.handle_message)
        adapter.on_button_click(engine.handle_button_click)

        # Start the engine (health monitor, pane poller, etc.)
        # Redirect stdout → stderr during startup to prevent installer print()
        # calls from contaminating the JSON IPC channel.
        _old_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            await engine.start()
        finally:
            sys.stdout = _old_stdout

        # Start SkillRunner
        from .core.skill_loader import SkillLoader
        from .core.skill_runner import SkillRunner
        from .storage.db import DEFAULT_DB_PATH

        _loader = SkillLoader()
        _tools = _loader.load_tools()
        _skills = _loader.load_skills(_tools)
        _shell_env = _loader.get_shell_env()
        _loader.load_ops_session()

        skill_runner = SkillRunner(
            emit_fn=_emit,
            db_path=DEFAULT_DB_PATH,
        )
        skill_runner.load(_skills, tools=_tools, shell_env=_shell_env)
        await skill_runner.start()

        # Emit ready + initial session list
        _emit({"event": "ready", "version": _get_version()})
        _emit_sessions(engine, _emit)

        # Start background pane status watcher
        asyncio.ensure_future(_pane_watcher(engine, _emit, logger))

        # Read commands from stdin line by line
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        try:
            while True:
                try:
                    line = await reader.readline()
                except Exception:
                    break
                if not line:
                    break
                line = line.decode().strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Desktop: invalid JSON on stdin: %s", line[:80])
                    continue

                cmd = msg.get("cmd", "")
                payload = msg.get("payload", {})
                logger.debug("Desktop cmd: %s %s", cmd, payload)

                try:
                    if cmd == "ping":
                        _emit({"event": "pong"})

                    elif cmd == "sessions":
                        _emit_sessions(engine, _emit)

                    elif cmd == "send":
                        channel_id = payload.get("channel_id", "")
                        text = payload.get("text", "")
                        if channel_id and text:
                            await adapter.dispatch_message(IncomingMessage(
                                platform="desktop",
                                channel_id=channel_id,
                                user_id="desktop_user",
                                text=text,
                            ))
                        else:
                            _emit({"event": "error", "msg": "send requires channel_id and text"})

                    elif cmd == "capture":
                        channel_id = payload.get("channel_id", "")
                        binding = engine.session_mgr.get_binding(channel_id)
                        if binding:
                            text = await engine.tmux.capture_pane_text(binding.window_id)
                            _emit({"event": "capture", "channel_id": channel_id, "text": text})
                        else:
                            _emit({"event": "error", "msg": f"No binding for channel {channel_id}"})

                    elif cmd == "button":
                        channel_id = payload.get("channel_id", "")
                        callback_data = payload.get("callback_data", "")
                        if channel_id and callback_data:
                            await adapter.dispatch_button(channel_id, "desktop_user", callback_data)
                        else:
                            _emit({"event": "error", "msg": "button requires channel_id and callback_data"})

                    elif cmd == "skills":
                        # Scan ~/.gits/skills/*.md → emit skills_list
                        asyncio.ensure_future(_handle_skills(skill_runner, _emit))

                    elif cmd == "agents":
                        # Query runs from DB → emit agents_list
                        asyncio.ensure_future(_handle_agents(_emit))

                    elif cmd == "skill_run":
                        skill_name = payload.get("skill_name", "")
                        if skill_name and skill_runner:
                            asyncio.ensure_future(skill_runner.run_now(skill_name))
                        else:
                            _emit({"event": "error", "msg": "skill_run requires skill_name"})

                    elif cmd == "skill_pause":
                        skill_name = payload.get("skill_name", "")
                        if skill_name and skill_runner:
                            skill_runner.pause(skill_name)
                            _emit({"event": "skill_paused", "skill_name": skill_name})
                        else:
                            _emit({"event": "error", "msg": "skill_pause requires skill_name"})

                    elif cmd == "skill_resume":
                        skill_name = payload.get("skill_name", "")
                        if skill_name and skill_runner:
                            skill_runner.resume(skill_name)
                            _emit({"event": "skill_resumed", "skill_name": skill_name})
                        else:
                            _emit({"event": "error", "msg": "skill_resume requires skill_name"})

                    elif cmd == "new_session":
                        import subprocess as _sp
                        import time as _time
                        sess_name = payload.get("name", "ghost")
                        work_dir = os.path.expanduser(payload.get("work_dir", "~"))
                        cli = payload.get("cli", "claude")
                        ts = str(int(_time.time() * 1000))
                        channel_id = f"desktop-{ts}"
                        result = _sp.run(
                            ["tmux", "new-window", "-t", f"{engine.tmux.session_name}:",
                             "-n", sess_name, "-c", work_dir, "-P", "-F", "#{window_id}"],
                            capture_output=True, text=True,
                        )
                        window_id = result.stdout.strip()
                        if not window_id:
                            _emit({"event": "error", "msg": "Failed to create tmux window"})
                        else:
                            _sp.run(["tmux", "send-keys", "-t",
                                     f"{engine.tmux.session_name}:{window_id}", cli, "Enter"])
                            await engine.session_mgr.bind(
                                platform="desktop",
                                channel_id=channel_id,
                                window_id=window_id,
                                window_name=sess_name,
                                work_dir=work_dir,
                                coding_cli=cli,
                            )
                            _emit_sessions(engine, _emit)

                    elif cmd == "agent_log":
                        skill_name = payload.get("skill_name", "")
                        run_id = payload.get("run_id", "")
                        if skill_name:
                            asyncio.ensure_future(_handle_agent_log(skill_name, run_id, _emit))
                        else:
                            _emit({"event": "error", "msg": "agent_log requires skill_name"})

                    else:
                        _emit({"event": "error", "msg": f"Unknown command: {cmd}"})

                except Exception as exc:
                    logger.exception("Desktop: error handling cmd %s", cmd)
                    _emit({"event": "error", "cmd": cmd, "msg": str(exc)})
        finally:
            if skill_runner:
                await skill_runner.stop()
            await engine.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Desktop: shutting down")
    except Exception:
        logger.exception("Desktop backend crashed")


async def _pane_watcher(engine: Any, emit_fn: Any, logger: Any) -> None:
    """Background task: poll all bound panes every 2s, push status + content."""
    from .core.terminal_parser import parse_status_line

    prev_statuses: dict[str, str] = {}

    while True:
        await asyncio.sleep(2)
        bindings = engine.session_mgr.list_bindings()
        for b in bindings:
            try:
                text = await engine.tmux.capture_pane_text(b.window_id)
                status = parse_status_line(text) or "idle"

                # Always emit capture (terminal content)
                emit_fn({
                    "event": "pane_update",
                    "channel_id": b.channel_id,
                    "status": status,
                    "text": text,
                })

                prev_statuses[b.channel_id] = status
            except Exception as exc:
                logger.debug("pane_watcher: error for %s: %s", b.channel_id, exc)


def _emit_sessions(engine: Any, emit_fn: Any) -> None:
    """Emit the current session list to the Electron/Tauri frontend."""
    import subprocess as _sp
    bindings = engine.session_mgr.list_bindings()

    # Determine which tmux windows are still alive
    try:
        result = _sp.run(
            ["tmux", "list-windows", "-t", engine.tmux.session_name, "-F", "#{window_id}"],
            capture_output=True, text=True,
        )
        alive_ids = {line.strip() for line in result.stdout.splitlines() if line.strip()} \
            if result.returncode == 0 else {b.window_id for b in bindings}
    except Exception:
        alive_ids = {b.window_id for b in bindings}

    emit_fn({"event": "sessions",
             "tmux_session": engine.tmux.session_name,
             "sessions": [
        {
            "channel_id": b.channel_id,
            "window_id": b.window_id,
            "window_name": b.window_name,
            "work_dir": b.work_dir,
            "coding_cli": b.coding_cli,
            "platform": b.platform,
            "parent_channel_id": b.parent_channel_id,
            "created_at": b.created_at,
            "alive": b.window_id in alive_ids,
        }
        for b in bindings
    ]})


def _get_version() -> str:
    try:
        from . import __version__

        return __version__
    except ImportError:
        return "unknown"


async def _handle_skills(skill_runner: Any, emit_fn: Any) -> None:
    from .core.skill_loader import SkillLoader
    loader = SkillLoader()
    tools = loader.load_tools()
    skills = loader.load_skills(tools)
    emit_fn({
        "event": "skills_list",
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "trigger": "loop" if s.loop else "reactive",
                "on_failure": s.on_failure,
                "guard_enabled": s.guard.enabled,
                "steps": len(s.steps),
                "paused": skill_runner._paused.__contains__(s.name) if skill_runner else False,
            }
            for s in skills.values()
        ]
    })


async def _handle_agents(emit_fn: Any) -> None:
    from .storage.db import RunsDB, DEFAULT_DB_PATH
    try:
        async with RunsDB(DEFAULT_DB_PATH) as db:
            runs = await db.query_runs(limit=100)
        emit_fn({"event": "agents_list", "runs": runs})
    except Exception as exc:
        emit_fn({"event": "agents_list", "runs": [], "error": str(exc)})


async def _handle_agent_log(skill_name: str, run_id: str, emit_fn: Any) -> None:
    from pathlib import Path
    agents_dir = Path.home() / ".gits" / "agents"
    if run_id:
        safe_id = run_id.replace(":", "-")
        log_file = agents_dir / skill_name / f"{safe_id}.log"
    else:
        log_file = agents_dir / skill_name / "current.log"

    if not log_file.exists():
        emit_fn({"event": "agent_log", "skill_name": skill_name, "run_id": run_id, "line": None, "eof": True})
        return

    try:
        for line in log_file.read_text().splitlines():
            emit_fn({"event": "agent_log", "skill_name": skill_name, "run_id": run_id, "line": line})
        emit_fn({"event": "agent_log", "skill_name": skill_name, "run_id": run_id, "line": None, "eof": True})
    except OSError as exc:
        emit_fn({"event": "error", "msg": f"agent_log: {exc}"})


if __name__ == "__main__":
    main()
