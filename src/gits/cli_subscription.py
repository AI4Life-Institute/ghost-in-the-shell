"""CLI handlers for ``gits subscription`` family.

.. deprecated:: 0.3
    Replaced by ``gits account`` (see ``gits.cli_account``). This module is
    preserved for V1 transition compatibility — invocations log a one-line
    deprecation hint and continue to function. Will be removed after users
    migrate. See openspec change ``add-multi-account-hotswap``.

Each handler runs in a one-shot CLI process — distinct from the long-lived
``gits start`` daemon. They share state with the daemon via:

* ``~/.gits/subscriptions/manifest.json`` (read/written by both)
* ``~/.gits/.switch.lock`` (fcntl.flock — coordinates daemon HealthMonitor and
  CLI invocations so they never mutate credentials concurrently)
* ``~/.gits/state.json`` (read-only here — used to enumerate bindings whose
  panes need their ``claude`` process killed/respawned during a switch)

The daemon does not need to be told about a CLI-initiated switch: when its
HealthMonitor next polls, it sees the lock released and a fresh manifest.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from .config import Settings
from .core.launcher import CodingCLILauncher
from .core.session import SessionManager
from .core.subscription import (
    AddResult,
    SubscriptionVault,
    SubscriptionVaultError,
    SwitchAborted,
    SwitchPrimitive,
    SwitchResult,
)
from .core.tmux import TmuxController

logger = logging.getLogger(__name__)


def _build_primitive(settings: Settings) -> tuple[SwitchPrimitive, SubscriptionVault]:
    """Construct a SwitchPrimitive sharing state with the daemon."""
    from .core.subscription import active_env_file_path

    vault = SubscriptionVault(settings.subscriptions_dir)
    session_mgr = SessionManager(settings.state_dir)
    launcher = CodingCLILauncher(
        session_map_path=settings.session_map_file,
        config_path=settings.config_file,
        active_env_file=active_env_file_path(settings.state_dir),
    )
    tmux = TmuxController(settings.tmux_session_name)
    primitive = SwitchPrimitive(
        tmux=tmux,
        session_mgr=session_mgr,
        launcher=launcher,
        vault=vault,
        lock_path=settings.credential_lock_file,
    )
    return primitive, vault


def _format_age(ts_iso: str | None) -> str:
    if not ts_iso:
        return "never"
    try:
        ts = time.mktime(time.strptime(ts_iso.rstrip("Z"), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return ts_iso
    delta = max(0.0, time.time() - ts)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def _format_rate_limit(epoch: float | None) -> str:
    if epoch is None or epoch <= time.time():
        return "available"
    delta = epoch - time.time()
    if delta < 3600:
        return f"limited {int(delta / 60)}m left"
    return f"limited {int(delta / 3600)}h left"


# ─────────────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────────────


def cmd_add(args: argparse.Namespace) -> None:
    """``gits subscription add <name>`` — register a new subscription.

    Default: launches ``claude auth login`` so the user completes OAuth in
    their browser, then snapshots the resulting credentials into the vault.
    The subscription becomes active ONLY if there's no existing active
    subscription; otherwise the previously-active subscription remains
    active and bindings are respawned with the original credentials.

    Migration shortcut: ``--capture-current`` skips OAuth and snapshots the
    existing ``~/.claude/.credentials.json`` as-is. Only valid before any
    subscription has been registered (it's a one-time migration step from
    pre-feature ghost installs).
    """
    from .core.subscription import fetch_claude_identity, parse_live_credentials

    settings = Settings()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.subscriptions_dir.mkdir(parents=True, exist_ok=True)
    primitive, vault = _build_primitive(settings)

    name: str = args.name
    capture_current: bool = bool(getattr(args, "capture_current", False))
    manifest = vault.load()

    print(f"Registering subscription {name!r}...")
    if capture_current:
        # Best-effort identity peek so the user can verify before commit.
        identity = fetch_claude_identity()
        if identity:
            email = identity.get("email", "(unknown)")
            org = identity.get("orgName", "")
            sub_type = identity.get("subscriptionType", "")
            print(
                f"--capture-current: snapshotting existing login → {email}"
                + (f" · {org}" if org else "")
                + (f" · {sub_type}" if sub_type else "")
            )
        else:
            existing_oauth = parse_live_credentials(vault.claude_credentials_path)
            if existing_oauth.get("accessToken"):
                print(
                    "--capture-current: snapshotting existing ~/.claude/.credentials.json "
                    "(identity could not be read via `claude auth status`)."
                )
            # else: validation will reject below
        print(f"No `claude auth login` will run.")
    else:
        if manifest.active is not None:
            print(
                f"Active subscription is {manifest.active!r}; that will stay active.\n"
                f"This command will only register {name!r} as a new vault entry.\n"
            )
        print(
            "Will pause any running claude processes managed by ghost, then\n"
            "launch `claude auth login` — complete the OAuth flow in your browser.\n"
            "After login the credentials are snapshotted to the vault."
        )
    print()

    try:
        result: AddResult = asyncio.run(
            primitive.add_subscription(name, capture_current=capture_current)
        )
    except SubscriptionVaultError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except SwitchAborted as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(2)

    if not result.succeeded:
        print(f"× add did not complete: {result.error or 'unknown'}", file=sys.stderr)
        sys.exit(3)

    # Reload manifest to read the captured email/orgId for confirmation
    refreshed = vault.load().get(name)
    detail = ""
    if refreshed:
        bits = []
        if refreshed.email:
            bits.append(refreshed.email)
        if refreshed.org_name:
            bits.append(refreshed.org_name)
        if refreshed.subscription_type:
            bits.append(refreshed.subscription_type)
        if bits:
            detail = "  (" + " · ".join(bits) + ")"

    if result.became_active:
        print(f"✓ subscription {name!r} added and set as active{detail}")
    else:
        print(f"✓ subscription {name!r} added{detail} (active subscription unchanged)")

    if result.bindings_failed:
        print(
            f"⚠ {len(result.bindings_failed)} binding(s) failed to respawn: "
            + ", ".join(result.bindings_failed),
            file=sys.stderr,
        )


def _short_org(org_id: str | None) -> str:
    if not org_id:
        return "-"
    # First 8 chars of UUID — enough to distinguish in practice.
    return org_id.split("-", 1)[0]


def cmd_list(args: argparse.Namespace) -> None:
    settings = Settings()
    vault = SubscriptionVault(settings.subscriptions_dir)
    if not vault.exists():
        print("(no subscriptions registered — run `gits subscription add <name>`)")
        return
    manifest = vault.load()
    if not manifest.subscriptions:
        print("(no subscriptions registered)")
        return

    width_name = max(8, max(len(s.name) for s in manifest.subscriptions) + 1)
    print(
        f"{'  ':2}{'NAME':{width_name}} {'ORG':10} {'EMAIL':28} "
        f"{'TYPE':6} {'LAST USED':12} {'STATUS':20}"
    )
    for s in manifest.subscriptions:
        marker = "* " if s.name == manifest.active else "  "
        org = _short_org(s.org_id)
        email = s.email or "-"
        sub_type = s.subscription_type or "-"
        last = _format_age(s.last_used)
        status = _format_rate_limit(s.rate_limited_until)
        print(
            f"{marker}{s.name:{width_name}} {org:10} {email:28} "
            f"{sub_type:6} {last:12} {status:20}"
        )


def cmd_remove(args: argparse.Namespace) -> None:
    settings = Settings()
    primitive, _ = _build_primitive(settings)
    try:
        asyncio.run(primitive.remove_subscription(args.name, force=args.force))
    except SubscriptionVaultError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ subscription {args.name!r} removed")


def cmd_switch(args: argparse.Namespace) -> None:
    """Honor rate_limited_until — refuse to switch into a rate-limited subscription."""
    settings = Settings()
    primitive, vault = _build_primitive(settings)
    manifest = vault.load()
    target = manifest.get(args.name)
    if target is None:
        print(f"error: subscription {args.name!r} not found", file=sys.stderr)
        sys.exit(1)
    if target.rate_limited_until is not None and target.rate_limited_until > time.time():
        until = time.strftime(
            "%Y-%m-%d %H:%M", time.localtime(target.rate_limited_until)
        )
        print(
            f"error: {args.name!r} is rate-limited until {until}; "
            f"use `gits subscription use {args.name}` to override",
            file=sys.stderr,
        )
        sys.exit(1)

    _do_switch(primitive, args.name, reason="manual")


def cmd_use(args: argparse.Namespace) -> None:
    """Force switch — clears rate_limited_until first."""
    settings = Settings()
    primitive, vault = _build_primitive(settings)
    manifest = vault.load()
    if manifest.get(args.name) is None:
        print(f"error: subscription {args.name!r} not found", file=sys.stderr)
        sys.exit(1)
    asyncio.run(vault.update_rate_limit(args.name, None))
    _do_switch(primitive, args.name, reason="use")


def _do_switch(primitive: SwitchPrimitive, name: str, *, reason: str) -> None:
    from .core.subscription import fetch_claude_identity

    try:
        result: SwitchResult = asyncio.run(primitive.switch_to(name, reason=reason))
    except SubscriptionVaultError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    except SwitchAborted as e:
        print(f"error: switch aborted: {e}", file=sys.stderr)
        sys.exit(2)
    if result.previous == result.target:
        print(f"✓ {name} is already active (no-op)")
        return

    # Refresh manifest entry from live claude auth status — token refresh / org
    # change on Anthropic side may have drifted the stored email/org_id since
    # the original add. This keeps `gits subscription list` in sync with reality.
    ident = fetch_claude_identity()
    drift_note = ""
    if ident:
        m = primitive.vault.load()
        entry = m.get(name)
        if entry is not None:
            new_email = ident.get("email")
            new_org_id = ident.get("orgId")
            new_org_name = ident.get("orgName")
            new_sub_type = ident.get("subscriptionType")
            changed = []
            if new_email and new_email != entry.email:
                changed.append(f"email: {entry.email} → {new_email}")
                entry.email = new_email
            if new_org_id and new_org_id != entry.org_id:
                changed.append(
                    f"org: {_short_org(entry.org_id)} → {_short_org(new_org_id)}"
                )
                entry.org_id = new_org_id
            if new_org_name and new_org_name != entry.org_name:
                entry.org_name = new_org_name
            if new_sub_type and new_sub_type != entry.subscription_type:
                entry.subscription_type = new_sub_type
            if changed:
                asyncio.run(primitive.vault.save(m))
                drift_note = " (manifest refreshed: " + ", ".join(changed) + ")"

    org_label = _short_org(ident.get("orgId")) if ident else "?"
    email_label = (ident.get("email") if ident else None) or "?"
    print(f"✓ switched: {result.previous} → {result.target}")
    print(f"  identity: org={org_label} · email={email_label}{drift_note}")
    if result.bindings_respawned:
        print(f"  respawned {len(result.bindings_respawned)} binding(s)")
    if result.bindings_failed:
        print(
            f"  ⚠ {len(result.bindings_failed)} binding(s) failed to respawn: "
            + ", ".join(result.bindings_failed),
            file=sys.stderr,
        )


def cmd_status(args: argparse.Namespace) -> None:
    from .core.subscription import fetch_claude_identity, parse_live_credentials

    settings = Settings()
    vault = SubscriptionVault(settings.subscriptions_dir)
    if not vault.exists():
        print("subscriptions: not enabled (run `gits subscription add <name>`)")
        return
    manifest = vault.load()

    # Count bindings using the active subscription
    binding_count = 0
    try:
        sm = SessionManager(settings.state_dir)
        binding_count = len(sm.list_bindings())
    except Exception:
        pass

    active = manifest.active or "(none)"
    active_entry = manifest.get(manifest.active) if manifest.active else None
    print(f"active:     {active}")
    if active_entry:
        print(
            f"  stored:     org={_short_org(active_entry.org_id)} · email={active_entry.email or '-'}"
        )
    # Live claude auth identity (best-effort; reflects ~/.claude/.credentials.json)
    live_oauth = parse_live_credentials(vault.claude_credentials_path)
    if live_oauth.get("accessToken"):
        ident = fetch_claude_identity()
        if ident:
            live_org = _short_org(ident.get("orgId"))
            live_email = ident.get("email", "-")
            mismatch = (
                "  ⚠ vault drift"
                if active_entry
                and ident.get("orgId")
                and active_entry.org_id
                and ident.get("orgId") != active_entry.org_id
                else ""
            )
            print(
                f"  live:       org={live_org} · email={live_email}{mismatch}"
            )
    print(f"bindings:   {binding_count} active")

    candidate = vault.candidate(manifest, exclude=manifest.active)
    if candidate:
        print(f"next:       {candidate} (rotation candidate)")
    else:
        print("next:       (no candidate available)")

    if manifest.last_switch:
        ls = manifest.last_switch
        print(
            f"last switch: {ls.from_name or '(none)'} → {ls.to_name} "
            f"at {ls.at} ({ls.reason})"
        )


# ─────────────────────────────────────────────────────────────────────
# Argparse wiring
# ─────────────────────────────────────────────────────────────────────


def install_parser(parent_subparsers: argparse._SubParsersAction) -> None:
    """Install ``gits subscription <subcmd>`` parser tree.

    Also registered under the alias ``sub`` for terser invocations.
    """
    for cmd_name in ("subscription", "sub"):
        sub_p = parent_subparsers.add_parser(
            cmd_name,
            help="Manage Claude subscriptions (multi-account credential vault)",
        )
        ssub = sub_p.add_subparsers(dest="sub_command", required=True)

        p_add = ssub.add_parser(
            "add",
            help="Register a new subscription. Runs `claude auth login` by default; "
            "use --capture-current to snapshot existing credentials instead.",
        )
        p_add.add_argument("name", help="Subscription name (e.g., work, home)")
        p_add.add_argument(
            "--capture-current",
            dest="capture_current",
            action="store_true",
            help=(
                "Skip OAuth and snapshot the existing ~/.claude/.credentials.json "
                "as the new subscription. Use cases: (1) first-add migration of an "
                "existing ghost install; (2) headless/SSH workflow — login on a "
                "machine with browser, scp ~/.claude/.credentials.json over to "
                "this host, then run with --capture-current. Refused if the live "
                "credentials' Anthropic org_id matches the currently-active "
                "subscription (would create a duplicate)."
            ),
        )
        p_add.set_defaults(_handler=cmd_add)

        p_list = ssub.add_parser("list", help="List registered subscriptions")
        p_list.set_defaults(_handler=cmd_list)

        p_remove = ssub.add_parser("remove", help="Delete a subscription")
        p_remove.add_argument("name")
        p_remove.add_argument(
            "--force", action="store_true", help="Allow removing the active subscription"
        )
        p_remove.set_defaults(_handler=cmd_remove)

        p_switch = ssub.add_parser(
            "switch", help="Switch active subscription (refuses if rate-limited)"
        )
        p_switch.add_argument("name")
        p_switch.set_defaults(_handler=cmd_switch)

        p_use = ssub.add_parser(
            "use",
            help="Force switch — clears rate-limit on target before switching",
        )
        p_use.add_argument("name")
        p_use.set_defaults(_handler=cmd_use)

        p_status = ssub.add_parser("status", help="Show vault summary")
        p_status.set_defaults(_handler=cmd_status)


def dispatch(args: argparse.Namespace) -> None:
    """Run the handler attached by ``set_defaults(_handler=...)``."""
    # Deprecation notice — see openspec change ``add-multi-account-hotswap``.
    # ``gits subscription`` is preserved for V1 transition; new users should
    # use ``gits account`` (CLAUDE_CONFIG_DIR-based isolation).
    print(
        "[deprecated] `gits subscription` is replaced by `gits account` "
        "(CLAUDE_CONFIG_DIR-based isolation). This command still works for "
        "transition compatibility but will be removed in a future release.",
        file=sys.stderr,
    )
    handler = getattr(args, "_handler", None)
    if handler is None:
        print("usage: gits subscription <add|list|remove|switch|use|status>", file=sys.stderr)
        sys.exit(1)
    handler(args)
