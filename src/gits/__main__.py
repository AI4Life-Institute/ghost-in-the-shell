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
    hook_p.add_argument(
        "--all-accounts",
        action="store_true",
        help=(
            "When combined with --install, also install the Claude hook into "
            "every per-account settings.json registered in ~/.gits/accounts/manifest.json "
            "(per openspec change add-multi-account-hotswap)."
        ),
    )

    # gits info
    sub.add_parser("info", help="Show current bindings")

    # gits wechat
    weixin_p = sub.add_parser("wechat", help="WeChat setup wizard")
    weixin_p.add_argument(
        "--relogin",
        action="store_true",
        help="Re-run openclaw-weixin install even if account already exists",
    )
    weixin_p.add_argument(
        "--path",
        default=None,
        help="Default project path to auto-bind (saved to ~/.gits/config.env)",
    )
    weixin_p.add_argument(
        "--no-start",
        action="store_true",
        help="Skip starting the bot after setup",
    )

    # gits discord — transport group + setup wizard (formerly bare `ghost discord`,
    # now `ghost discord setup`; see task a2ec59). Registration lives in
    # gits.butler.discord_cli so transport verbs share http helpers with butler.
    from .butler import discord_cli as _discord_cli

    _discord_cli.install_parser(sub)

    # gits butler — PM-semantic layer atop discord transport (task a2ec59).
    from .butler import butler_cli as _butler_cli

    _butler_cli.install_parser(sub)

    # gits restart
    sub.add_parser("restart", help="Restart the Ghost background service")

    # ghost reset-wechat
    sub.add_parser("reset-wechat", help="Clear WeChat binding and restart (re-triggers onboarding)")

    # gits setup
    setup_p = sub.add_parser("setup", help="Interactive setup wizard (select platforms)")
    setup_p.add_argument("--no-start", action="store_true", help="Skip restart after setup")

    # gits desktop  (launched by the Electron shell)
    desktop_p = sub.add_parser("desktop", help="Desktop app backend (IPC over stdio)")
    desktop_p.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    # gits subscription / gits sub  (deprecated; kept for V1 transition)
    from . import cli_subscription as _cli_sub

    _cli_sub.install_parser(sub)

    # gits account / gits acct  (Phase 0.11 — replaces gits subscription)
    from . import cli_account as _cli_account

    _cli_account.install_parser(sub)

    # gits install-skills / gits uninstall-skills  (G-4 irzsbr)
    from . import install_skills as _install_skills

    _install_skills.install_parser(sub)

    args = parser.parse_args()

    if args.command == "start":
        _cmd_start(args)
    elif args.command == "restart":
        _cmd_restart()
    elif args.command == "reset-wechat":
        _cmd_reset_weixin()
    elif args.command is None:
        # Default: run setup wizard
        args.no_start = False
        _cmd_setup(args)
    elif args.command == "hook":
        _cmd_hook(args)
    elif args.command == "info":
        _cmd_status(args)
    elif args.command == "wechat":
        _cmd_weixin(args)
    elif args.command == "discord":
        _discord_cli.dispatch(args)
    elif args.command == "butler":
        _butler_cli.dispatch(args)
    elif args.command == "setup":
        _cmd_setup(args)
    elif args.command == "desktop":
        _cmd_desktop(args)
    elif args.command in ("subscription", "sub"):
        _cli_sub.dispatch(args)
    elif args.command in ("account", "acct"):
        _cli_account.dispatch(args)
    elif args.command in ("install-skills", "uninstall-skills"):
        _install_skills.dispatch(args)


def _cmd_start(args: argparse.Namespace) -> None:
    """Start the GITS bot."""
    if args.dev:
        _cmd_start_dev(args)
        return

    _cmd_start_normal(args)


def _cmd_restart() -> None:
    """Restart the Ghost launchd service."""
    import subprocess as _sp

    label = "ai.ghost.gits"
    uid = _sp.check_output(["id", "-u"]).decode().strip()
    result = _sp.run(["launchctl", "list", label], capture_output=True)
    if result.returncode != 0:
        print("Ghost is not running. Run `ghost setup` first.")
        raise SystemExit(1)

    _sp.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"], check=True)
    print("✓ Ghost restarted")


def _cmd_reset_weixin() -> None:
    """Clear all WeChat bindings from state so next message re-triggers onboarding."""
    import json
    from pathlib import Path

    state_file = Path("~/.gits/state.json").expanduser()
    if not state_file.exists():
        print("No state file found, nothing to reset.")
        _cmd_restart()
        return

    state = json.loads(state_file.read_text())
    bindings = state.get("bindings", {})
    before = len(bindings)

    # Save session IDs so auto-bind can try to resume them
    sessions_file = Path("~/.gits/weixin_sessions.json").expanduser()
    saved = json.loads(sessions_file.read_text()) if sessions_file.exists() else {}
    for cid, b in bindings.items():
        if "@im.wechat" in cid and b.get("cli_session_id"):
            saved[cid] = b["cli_session_id"]
    sessions_file.write_text(json.dumps(saved, indent=2))

    state["bindings"] = {
        cid: b for cid, b in bindings.items()
        if "@im.wechat" not in cid
    }
    removed = before - len(state["bindings"])
    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    print(f"✓ Cleared {removed} WeChat binding(s)")
    _cmd_restart()


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

    # Suppress noisy third-party DEBUG logs
    for _noisy in ("libtmux", "discord", "asyncio", "aiohttp", "urllib3", "httpx", "httpcore"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    logger = logging.getLogger("gits")
    logger.info("Starting Ghost in the Shell v%s", _get_version())

    from .adapters.weixin.bot import WeixinAdapter
    from .core.engine import Engine

    has_discord = bool(settings.gits_discord_token)
    has_weixin = WeixinAdapter.is_available()

    if not has_discord and not has_weixin:
        logger.error(
            "No platform configured. Set GITS_DISCORD_TOKEN or install openclaw-weixin."
        )
        sys.exit(1)

    # Ensure state directory
    settings.state_dir.mkdir(parents=True, exist_ok=True)

    engine = Engine(settings)
    adapters = []

    if has_discord:
        from .adapters.discord.bot import DiscordAdapter

        discord_adapter = DiscordAdapter(
            token=settings.gits_discord_token,
            allowed_users=settings.allowed_users,
            allowed_guilds=settings.allowed_guilds,
            bind_root=settings.bind_root,
        )
        adapters.append(discord_adapter)
        logger.info("Discord adapter enabled")

    if has_weixin:
        from .adapters.weixin.bot import discover_all_accounts
        default_path = str(settings.gits_default_path) if settings.gits_default_path else None
        from .adapters.weixin.whitelist import get_workspace
        wx_accounts = discover_all_accounts()
        for wx_acct in wx_accounts:
            try:
                workspace = get_workspace(wx_acct["account_id"])
                wx_adapter = WeixinAdapter(
                    default_path=workspace or default_path,
                    account=wx_acct,
                    workspace_root=workspace,
                )
                adapters.append(wx_adapter)
                logger.info("WeChat adapter enabled: account=%s workspace=%s",
                            wx_acct["account_id"], workspace or "(none)")
            except Exception as exc:
                logger.warning("WeChat adapter failed to init (account=%s): %s", wx_acct.get("account_id"), exc)

    # Always use MultiAdapter so new adapters can be added dynamically at runtime
    from .adapters.base import MultiAdapter
    from .telemetry import track
    primary = MultiAdapter(adapters)

    platforms = [*(["discord"] if has_discord else []), *(["weixin"] if has_weixin else [])]
    track("bot_started", platforms=",".join(platforms))

    engine.set_adapter(primary)

    async def _add_new_weixin_account(account: dict) -> None:
        """Called by /share when a new WeChat account is registered."""
        from .adapters.weixin.whitelist import get_workspace
        workspace = get_workspace(account["account_id"])
        try:
            new_wx = WeixinAdapter(
                default_path=workspace,
                account=account,
                workspace_root=workspace,
            )
            new_wx.set_engine(engine)
            new_wx.on_message(engine.handle_message)
            new_wx.on_button_click(engine.handle_button_click)
            new_wx.set_new_account_callback(_add_new_weixin_account)
            new_wx.set_revoke_account_callback(_revoke_weixin_account)
            primary.add_adapter(new_wx)
            asyncio.create_task(new_wx.start())
            logger.info("New WeChat adapter started: account=%s", account["account_id"])
        except Exception as exc:
            logger.error("Failed to start new WeChat adapter (account=%s): %s", account.get("account_id"), exc)

    async def _revoke_weixin_account(account_id: str) -> None:
        """Called by /revoke to stop and deregister a shared WeChat account."""
        await primary.remove_adapter(account_id)
        logger.info("WeChat adapter revoked: account=%s", account_id)

    for a in adapters:
        a.set_engine(engine)
        a.on_message(engine.handle_message)
        a.on_button_click(engine.handle_button_click)
        if hasattr(a, "set_new_account_callback"):
            a.set_new_account_callback(_add_new_weixin_account)
        if hasattr(a, "set_revoke_account_callback"):
            a.set_revoke_account_callback(_revoke_weixin_account)

    async def _run() -> None:
        await engine.start()
        try:
            if len(adapters) == 1:
                await adapters[0].start()
            else:
                await asyncio.gather(*(a.start() for a in adapters))
        finally:
            await engine.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")


def _ensure_launchd_service() -> None:
    """Install and/or (re)start the ai.ghost.gits launchd service."""
    import os
    import shutil
    import subprocess as _sp
    from pathlib import Path

    home = Path.home()
    uid = os.getuid()
    label = "ai.ghost.gits"
    ghost_bin = shutil.which("ghost") or f"{home}/.local/bin/ghost"

    # ── run script ───────────────────────────────────────────────────
    run_sh = home / ".gits" / "run-gits.sh"
    run_sh.parent.mkdir(parents=True, exist_ok=True)
    run_sh.write_text(f"""\
#!/bin/bash
set -a
[ -f "$HOME/.gits/config.env" ] && source "$HOME/.gits/config.env"
set +a
exec "{ghost_bin}" start
""")
    run_sh.chmod(0o755)

    # ── plist ────────────────────────────────────────────────────────
    log_dir = home / ".gits" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    plist_path = home / "Library" / "LaunchAgents" / f"{label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key><string>{label}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>5</integer>
    <key>WorkingDirectory</key><string>{home}</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/bash</string>
      <string>{run_sh}</string>
    </array>
    <key>StandardOutPath</key><string>{log_dir}/gits.log</string>
    <key>StandardErrorPath</key><string>{log_dir}/gits.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
      <key>HOME</key><string>{home}</string>
      <key>PATH</key><string>{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
  </dict>
</plist>
""")

    # ── load or kickstart ────────────────────────────────────────────
    check = _sp.run(["launchctl", "list", label], capture_output=True)
    if check.returncode != 0:
        # Not loaded yet — bootstrap
        r = _sp.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print(f"✓  Ghost installed and started (auto-launches on login)")
        else:
            print(f"   launchd install failed: {r.stderr.strip()}")
    else:
        # Already loaded — kickstart
        r = _sp.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print("✓  Ghost restarted in background")
        else:
            print(f"   launchd restart failed: {r.stderr.strip()}")


def _detect_discord_guilds(token: str) -> list[tuple[int, str]]:
    """Connect to Discord and return list of (guild_id, guild_name) the bot is in."""
    import asyncio

    import discord

    guilds: list[tuple[int, str]] = []

    async def _fetch():
        intents = discord.Intents.default()
        intents.guilds = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            for g in client.guilds:
                guilds.append((g.id, g.name))
            await client.close()

        await client.start(token)

    try:
        asyncio.run(_fetch())
    except Exception:
        pass
    return guilds


def _discord_guild_setup(token: str, config_env: "Path") -> None:
    """Detect guilds and let user pick which to whitelist."""
    import base64

    # Build invite URL from token
    token_prefix = token.split(".")[0]
    # Pad base64
    padded = token_prefix + "=" * (-len(token_prefix) % 4)
    try:
        client_id = base64.b64decode(padded).decode()
    except Exception:
        client_id = None

    if client_id:
        invite_url = (
            f"https://discord.com/oauth2/authorize?client_id={client_id}"
            f"&scope=bot+applications.commands&permissions=274877991936"
        )
        print(f"\n  Invite your bot to a server (skip if already done):")
        print(f"  {invite_url}")

    print("\n  ⚠️  Before continuing, make sure you have:")
    print("     1. Invited the bot to your server(s) using the link above")
    print("     2. Enabled 'Message Content Intent' in the Discord Developer Portal")
    print("        (Bot → Privileged Gateway Intents → Message Content Intent)")
    input("\n  Press Enter when ready to detect servers...")

    print("  Connecting to Discord...")
    guilds = _detect_discord_guilds(token)

    if not guilds:
        print("  ⚠️  Bot is not in any servers. Invite it first, then run setup again.")
        return

    print(f"  Found {len(guilds)} server(s):")
    for i, (gid, name) in enumerate(guilds, 1):
        print(f"    {i}. {name} ({gid})")

    if len(guilds) == 1:
        confirm = input(f"\n  Whitelist '{guilds[0][1]}'? [Y/n] ").strip().lower()
        selected = guilds if confirm != "n" else []
    else:
        picks = input(
            f"\n  Which servers to whitelist? (comma-separated numbers, or 'all') [{len(guilds)} found]: "
        ).strip().lower()
        if picks == "all" or picks == "":
            selected = guilds
        else:
            indices = [int(x.strip()) - 1 for x in picks.split(",") if x.strip().isdigit()]
            selected = [guilds[i] for i in indices if 0 <= i < len(guilds)]

    if selected:
        guild_val = "[" + ",".join(str(gid) for gid, _ in selected) + "]"
        lines = config_env.read_text().splitlines() if config_env.exists() else []
        new_lines = [l for l in lines if not l.startswith("ALLOWED_GUILDS=")]
        new_lines.append(f"ALLOWED_GUILDS={guild_val}")
        config_env.write_text("\n".join(new_lines) + "\n")
        names = ", ".join(name for _, name in selected)
        print(f"  ✓ Whitelisted: {names}")
    else:
        print("  ⚠️  No servers whitelisted — all commands will be denied until configured.")


def _cmd_setup(args: argparse.Namespace) -> None:
    """Interactive setup wizard — select platforms, configure each, then restart."""
    import os
    import subprocess as _sp
    from pathlib import Path

    import questionary
    from questionary import Style

    from .openclaw import accounts as _accounts

    _WEIXIN_CHANNEL = "openclaw-weixin"

    _style = Style([
        ("qmark", "fg:#00bfff bold"),
        ("question", "bold"),
        ("answer", "fg:#00ff7f bold"),
        ("pointer", "fg:#00bfff bold"),
        ("selected", "fg:#00ff00 bold"),      # ● selected item — bright green
        ("highlighted", "fg:#ffffff bold"),   # cursor on unselected item — bold white
        ("text", "fg:#666666"),               # ○ unselected item — grey
        ("instruction", "fg:#555555 italic"),
    ])

    print()
    print("  Ghost — Setup Wizard")
    print()
    print("  Ghost collects anonymous usage data (commands used, platform, version).")
    print("  No message content or file paths are collected.")
    print("  Opt out: set GITS_TELEMETRY=0 in ~/.gits/config.env")
    print()

    config_env = Path("~/.gits/config.env").expanduser()
    existing_discord = ""
    if config_env.exists():
        for line in config_env.read_text().splitlines():
            if line.startswith("GITS_DISCORD_TOKEN="):
                existing_discord = line.split("=", 1)[1].strip()
    existing_weixin = _accounts.discover(_WEIXIN_CHANNEL)

    # ── Select platforms to configure ────────────────────────────────
    # Replace questionary's default ●/○ with more intuitive [✓]/[ ]
    import questionary.prompts.common as _qpc
    _qpc.INDICATOR_SELECTED   = "[✓]"
    _qpc.INDICATOR_UNSELECTED = "[ ]"

    discord_label = f"Discord  {'(configured)' if existing_discord else '(not configured)'}"
    weixin_label  = f"WeChat  {'(configured)' if existing_weixin else '(not configured)'}"

    choices = questionary.checkbox(
        "Select platforms to configure (↑↓ move, space to toggle, enter to confirm):",
        choices=[
            questionary.Choice(discord_label, value="discord", checked=not existing_discord),
            questionary.Choice(weixin_label,  value="wechat",  checked=not existing_weixin),
        ],
        style=_style,
    ).ask()

    if not choices:
        print("\nNo platform selected. Exiting.")
        return

    configured = []

    # ── Discord ──────────────────────────────────────────────────────
    if "discord" in choices:
        print("\n─── Discord ──────────────────────────────────")
        if existing_discord:
            print(f"  Current token: {existing_discord[:20]}...")
        token = questionary.password(
            "Discord Bot Token:",
            style=_style,
        ).ask() or ""
        if token:
            config_env.parent.mkdir(parents=True, exist_ok=True)
            lines = config_env.read_text().splitlines() if config_env.exists() else []
            new_lines = [l for l in lines if not l.startswith("GITS_DISCORD_TOKEN=")]
            new_lines.append(f"GITS_DISCORD_TOKEN={token}")
            config_env.write_text("\n".join(new_lines) + "\n")
            print("  ✓ Token saved")
            _discord_guild_setup(token, config_env)
            configured.append("Discord")
        elif existing_discord:
            configured.append("Discord")
    elif existing_discord:
        configured.append("Discord")

    # ── WeChat ───────────────────────────────────────────────────────
    if "wechat" in choices:
        print("\n─── WeChat ───────────────────────────────────")
        if existing_weixin:
            print(f"  Current account: {existing_weixin['account_id']}")
        try:
            acct = _weixin_direct_login()
            _accounts.save(_WEIXIN_CHANNEL, acct)
            print(f"  ✓ Account saved: {acct['account_id']}")
            configured.append("WeChat")
        except Exception as exc:
            print(f"  ✗ WeChat login failed: {exc}")
    elif existing_weixin:
        configured.append("WeChat")

    if not configured:
        print("\nNothing configured.")
        return

    print(f"\n✅ Configured: {', '.join(configured)}")

    # Default-on: sync bundled Claude Code skills into ~/.claude/skills/
    # (G-4 irzsbr). Honors GHOST_NO_INSTALL_SKILLS=1 opt-out.
    try:
        from . import install_skills as _install_skills_mod
        if not _install_skills_mod._opt_out_active(False):
            print("\n─── Claude Code skills ───────────────────────")
            _install_skills_mod.install_skills()
    except Exception as exc:  # never let skills install break setup
        print(f"  ⚠ install-skills failed (non-fatal): {exc}")

    if args.no_start:
        print("Run `ghost start` to start the bot")
        return

    _ensure_launchd_service()
    print("Logs: ~/.gits/logs/gits.err.log")


def _cmd_discord(args: argparse.Namespace) -> None:
    """Discord setup wizard: save bot token to ~/.gits/config.env and restart."""
    import os
    import subprocess as _sp
    from pathlib import Path

    print("=" * 60)
    print("Ghost — Discord Setup Wizard")
    print("=" * 60)

    config_env = Path("~/.gits/config.env").expanduser()
    existing_token = ""
    if config_env.exists():
        for line in config_env.read_text().splitlines():
            if line.startswith("GITS_DISCORD_TOKEN="):
                existing_token = line.split("=", 1)[1].strip()

    # Show current status
    if existing_token:
        print(f"\nCurrent token: {existing_token[:20]}...")
    else:
        print("\nNo Discord token configured")

    # Get token
    token = args.token
    if not token:
        prompt = "Discord Bot Token (leave blank to keep current): " if existing_token else "Discord Bot Token: "
        token = input(prompt).strip()

    if not token and not existing_token:
        print("✗  No token entered. Exiting.")
        sys.exit(1)

    if token:
        # Write to config.env
        config_env.parent.mkdir(parents=True, exist_ok=True)
        lines = config_env.read_text().splitlines() if config_env.exists() else []
        new_lines = [l for l in lines if not l.startswith("GITS_DISCORD_TOKEN=")]
        new_lines.append(f"GITS_DISCORD_TOKEN={token}")
        config_env.write_text("\n".join(new_lines) + "\n")
        print(f"✓  Written to {config_env}")

    _discord_guild_setup(token or existing_token, config_env)

    if args.no_start:
        print("\n✅ Setup complete. Run `ghost start` to start the bot")
        return

    # Restart via launchd
    r = _sp.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/ai.ghost.gits"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        print("✓  Ghost restarted in background (ai.ghost.gits)")
    else:
        print(f"      launchd restart failed: {r.stderr.strip()}")
        print("      Run manually: ghost start")

    print("\n✅ Done! Logs: ~/.gits/logs/gits.err.log")


def _weixin_direct_login() -> dict:
    """Login to WeChat via ilinkai QR code — no openclaw required.

    Returns the account dict on success, raises RuntimeError on failure.
    """
    import json
    import time
    import urllib.request

    _BASE = "https://ilinkai.weixin.qq.com"

    # 1. Get QR code
    print("\n      Fetching WeChat QR code...")
    with urllib.request.urlopen(f"{_BASE}/ilink/bot/get_bot_qrcode?bot_type=3") as r:
        resp = json.loads(r.read())
    qrcode_id: str = resp.get("qrcode") or ""
    qrcode_url: str = resp.get("qrcode_img_content") or resp.get("qrcode_url") or ""
    if not qrcode_id:
        raise RuntimeError(f"get_bot_qrcode returned no qrcode: {resp}")

    # 2. Display QR code in terminal
    print(f"\n      Scan the QR code with WeChat to log in:\n")
    try:
        import qrcode as _qr  # type: ignore
        qr = _qr.QRCode(border=1)
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    except ImportError:
        # fallback: print URL prominently
        print(f"      {qrcode_url}")
        print()
        print("      ↑ Open the link above in a browser and scan with WeChat")
        print("      (or: uv add qrcode  to display the QR code directly in terminal)")

    # 3. Long-poll for scan result
    print("\n      Waiting for scan...")
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(2)
        try:
            with urllib.request.urlopen(
                f"{_BASE}/ilink/bot/get_qrcode_status?qrcode={qrcode_id}"
            ) as r:
                status_resp = json.loads(r.read())
        except Exception as e:
            raise RuntimeError(f"Failed to poll login status: {e}")

        status = status_resp.get("status", "")
        if status in ("confirmed", "success", "2"):
            break
        if status in ("expired", "cancelled", "-1"):
            raise RuntimeError(f"QR code expired: {status}")
        print(".", end="", flush=True)
    else:
        raise RuntimeError("Timed out waiting for scan (120s)")

    print("\n✓  Scan successful")

    # 4. Extract credentials
    token: str = status_resp.get("bot_token") or status_resp.get("token") or ""
    base_url: str = (status_resp.get("baseurl") or status_resp.get("base_url") or _BASE).rstrip("/")
    user_id: str = status_resp.get("ilink_user_id") or status_resp.get("user_id") or ""
    account_id: str = status_resp.get("ilink_bot_id") or status_resp.get("bot_id") or user_id

    if not token:
        raise RuntimeError(f"Login succeeded but no token received: {status_resp}")

    return {
        "account_id": account_id,
        "token": token,
        "base_url": base_url,
        "user_id": user_id,
        "saved_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }


def _enable_openclaw_weixin_plugin() -> None:
    """Ensure openclaw-weixin plugin is enabled in ~/.openclaw/openclaw.json."""
    import json
    from pathlib import Path

    config_path = Path("~/.openclaw/openclaw.json").expanduser()
    if not config_path.exists():
        return
    try:
        config = json.loads(config_path.read_text())
        plugins = config.setdefault("plugins", {})
        entries = plugins.setdefault("entries", {})
        if not entries.get("openclaw-weixin", {}).get("enabled", False):
            entries["openclaw-weixin"] = {"enabled": True}
            config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False))
            print("✓  openclaw-weixin plugin enabled")
    except Exception as e:
        print(f"      (Could not update openclaw config: {e})")


def _cmd_weixin(args: argparse.Namespace) -> None:
    """WeChat setup wizard: QR login via ilinkai, configure, and start."""
    from pathlib import Path

    from .openclaw import accounts as _accounts

    _CHANNEL = "openclaw-weixin"

    print("=" * 60)
    print("Ghost — WeChat Setup Wizard")
    print("=" * 60)

    # Step 1: Check existing account
    existing = _accounts.discover(_CHANNEL)
    if existing and not args.relogin:
        print(f"\n[2/4] Account found, skipping login")
        print(f"      account_id : {existing['account_id']}")
        print(f"      user_id    : {existing['user_id']}")
        print(f"      base_url   : {existing['base_url']}")
        account = existing
    else:
        if args.relogin:
            print("\n[2/4] --relogin specified, re-logging in...")
        else:
            print("\n[2/4] No account found, starting login...")
        try:
            acct = _weixin_direct_login()
        except Exception as exc:
            print(f"\n✗  Login failed: {exc}")
            sys.exit(1)
        _accounts.save(_CHANNEL, acct)
        account = acct
        print(f"✓  Account ready: {account['account_id']}")

    # Step 3: Default path
    print("\n[3/4] Configure default project path")
    default_path = args.path
    if not default_path:
        default_path = input("      Default bind path (leave blank to skip): ").strip() or None

    if default_path:
        config_env = Path("~/.gits/config.env").expanduser()
        config_env.parent.mkdir(parents=True, exist_ok=True)
        # Update or append GITS_DEFAULT_PATH
        lines = config_env.read_text().splitlines() if config_env.exists() else []
        new_lines = [l for l in lines if not l.startswith("GITS_DEFAULT_PATH=")]
        new_lines.append(f"GITS_DEFAULT_PATH={default_path}")
        config_env.write_text("\n".join(new_lines) + "\n")
        print(f"✓  Written to {config_env}: GITS_DEFAULT_PATH={default_path}")

    # Step 4: Start bot via launchd (background, silent)
    print("\n[4/4] Starting Ghost...")
    if args.no_start:
        print("      --no-start specified, skipping startup")
        print("\n✅ Setup complete. Run `ghost start` to start the bot")
        return

    import subprocess as _sp
    r = _sp.run(
        ["launchctl", "kickstart", "-k", f"gui/{__import__('os').getuid()}/ai.ghost.gits"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        print("✓  Ghost started in background (ai.ghost.gits)")
    else:
        print(f"      launchd start failed: {r.stderr.strip()}")
        print("      Run manually: ghost start")

    print("\n✅ Done! Logs: ~/.gits/logs/gits.err.log")


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
    from pathlib import Path

    # Configure logging for the hook subprocess
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )
    logger = logging.getLogger("gits.hook")

    if args.install:
        logger.info("Hook install requested (Claude)")
        rc = _install_hook()
        if getattr(args, "all_accounts", False):
            # Per-account propagation (Phase 0.10 / D14): iterate manifest
            # accounts and install into each ~/.claude-{name}/settings.json.
            try:
                from .config import Settings as _Settings
                from .core.account_vault import AccountVault as _AccountVault
                settings = _Settings()
                vault = _AccountVault(settings.state_dir)
                if vault.is_initialized():
                    for entry in vault.list():
                        sub_rc = _install_hook(config_dir=entry.config_dir)
                        if sub_rc != 0:
                            rc = sub_rc
            except Exception as e:
                logger.warning("hook --all-accounts propagation failed: %s", e)
        sys.exit(rc)

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
    #
    # Detection: walk up the process tree to find the first ancestor whose
    # comm is "claude" and check its cmdline for -p / --print.  Walking is
    # necessary because Claude may invoke the hook via a shell
    # ("/bin/sh -c gits hook"), making the direct parent a shell process
    # rather than the claude binary itself.
    try:
        pid = os.getpid()
        visited: set[int] = set()
        claude_ancestor_pid: int | None = None
        codex_ancestor_pid: int | None = None
        while pid > 1 and pid not in visited:
            visited.add(pid)
            try:
                status_lines = Path(f"/proc/{pid}/status").read_text().splitlines()
                ppid = next(
                    int(line.split()[1])
                    for line in status_lines
                    if line.startswith("PPid:")
                )
            except (OSError, StopIteration):
                break
            try:
                comm = Path(f"/proc/{ppid}/comm").read_text().strip()
                if comm == "claude":
                    claude_ancestor_pid = ppid
                    break
                if comm == "codex":
                    codex_ancestor_pid = ppid
                    break
            except OSError:
                pass
            pid = ppid

        if claude_ancestor_pid is not None:
            cmdline_raw = Path(f"/proc/{claude_ancestor_pid}/cmdline").read_bytes()
            cmdline_args = cmdline_raw.split(b"\x00")
            is_noninteractive = any(
                arg in (b"-p", b"--print") for arg in cmdline_args
            )
            if is_noninteractive:
                logger.debug(
                    "Skipping session_map update: claude ancestor (pid=%d) is "
                    "running in non-interactive mode (-p/--print)",
                    claude_ancestor_pid,
                )
                return

        if codex_ancestor_pid is not None:
            cmdline_raw = Path(f"/proc/{codex_ancestor_pid}/cmdline").read_bytes()
            cmdline_args = cmdline_raw.split(b"\x00")
            is_noninteractive = any(
                arg in (b"-q", b"--quiet") for arg in cmdline_args
            )
            if is_noninteractive:
                logger.debug(
                    "Skipping session_map update: codex ancestor (pid=%d) is "
                    "running in non-interactive mode (-q/--quiet)",
                    codex_ancestor_pid,
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
