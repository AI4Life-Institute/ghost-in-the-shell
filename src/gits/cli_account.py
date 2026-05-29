"""CLI handlers for ``gits account`` family (Phase 0.11).

Per openspec change ``add-multi-account-hotswap``. Five commands:

* ``add <name> [--capture-current]`` — register a new account.
* ``list`` — show every account with the dispatcher's load-balanced
  ranking (the exact rule ``--account=auto`` uses to pick).
* ``switch <name> --binding <id>`` — change a binding's claude account.
* ``remove <name>`` — delete an account dir + manifest entry.
* ``import <session_id> --to <name> [--from <name>] [--force]`` — copy a
  session JSONL between accounts.

Short alias ``gits acct`` is registered alongside ``gits account``.

Each handler runs in a one-shot CLI process; the long-lived ``gits start``
daemon picks up changes via state.json and manifest.json on its next
maybe-reload. Some handlers (``switch``) talk to the daemon via the
daemon's own RPC; for V1 they invoke ``Engine.switch_account`` in a
synchronous helper that constructs a transient Engine. Coordinating with
a running daemon is left for V2.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import Settings
from .core.account import (
    AccountLayout,
    AccountLayoutError,
    is_ghost_managed,
)
from .core.account_load import AccountRank, format_pick_token, rank_accounts
from .core.account_vault import (
    AccountEntry,
    AccountVault,
    AccountVaultError,
    SessionAlreadyExistsError,
    SessionMultipleSourcesError,
    SessionNotFoundError,
    extract_account_metadata,
    import_session,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Command: gits account add
# ─────────────────────────────────────────────────────────────────────


def cmd_add(args: argparse.Namespace) -> None:
    """``gits account add <name> [--capture-current]``.

    Three execution paths, in priority order:

    1. **Pre-loaded credentials** — if ``~/.claude-{name}/`` already exists
       with the ``.gits-managed`` marker AND ``.credentials.json`` AND the
       account is not yet in the vault, skip directory creation and
       ``claude auth login`` subprocess; just register. This handles the
       SSH-only workflow where ``claude`` CLI's URL+paste-code OAuth flow
       only triggers when invoked interactively (not as a subprocess) —
       see issue: ``gits account add`` runs claude under ``subprocess.run``
       and claude detects a non-interactive TTY context, falling back to
       the auto-callback mode that needs a localhost listener which SSH
       can't reach without port-forwarding. Workaround: user runs
       ``CLAUDE_CONFIG_DIR=~/.claude-{name} claude auth login`` directly,
       then ``gits account add {name}`` to register.

    2. **--capture-current** (first add only): rsync ``~/.claude/`` into
       the new account dir (full snapshot), then auto-migrate every
       legacy ``claude_account=None`` binding to this account.

    3. **Default path**: create empty account dir, spawn
       ``claude auth login`` subprocess with ``CLAUDE_CONFIG_DIR`` set.
    """
    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)

    name: str = args.name
    capture_current: bool = bool(args.capture_current)

    # Validate name early so the error is consistent across all paths.
    try:
        from .core.account import validate_account_name
        validate_account_name(name)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    target = layout.account_dir(name)
    creds_path = layout.credentials_file(name)

    # If the user pre-created the dir via direct ``claude auth login`` (the
    # SSH-paste-mode workflow) but didn't touch the marker, auto-write it
    # before the pre-loaded detection kicks in. This is safe because we
    # only do it when (a) creds.json is present (claude wrote it), (b) no
    # other manifest entry claims this name, and (c) the dir contains
    # nothing that would suggest it's NOT a fresh claude config dir.
    if (
        target.exists()
        and not is_ghost_managed(target)
        and creds_path.exists()
        and vault.get(name) is None
    ):
        from .core.account import MARKER_FILENAME
        try:
            (target / MARKER_FILENAME).touch(mode=0o644)
            print(
                f"[info] writing missing .gits-managed marker at {target} "
                "(detected pre-loaded credentials from direct `claude auth login`)"
            )
        except OSError as e:
            print(f"[error] could not write marker: {e}", file=sys.stderr)
            sys.exit(1)

    # ── Path 1: pre-loaded credentials ────────────────────────────────
    pre_loaded = (
        target.exists()
        and is_ghost_managed(target)
        and creds_path.exists()
        and vault.get(name) is None
    )
    if pre_loaded:
        if capture_current:
            print(
                "[error] --capture-current is incompatible with pre-loaded credentials "
                f"at {target}. Either remove the directory or omit --capture-current.",
                file=sys.stderr,
            )
            sys.exit(1)
        meta = extract_account_metadata(creds_path)
        if not meta.get("subscriptionType"):
            print(
                f"[error] {creds_path} does not contain a parseable accessToken. "
                f"Re-run `CLAUDE_CONFIG_DIR={target} claude auth login` to refresh.",
                file=sys.stderr,
            )
            sys.exit(1)
        entry = AccountEntry(
            name=name,
            email=meta.get("email"),
            org_id=meta.get("orgId"),
            subscription_type=meta.get("subscriptionType"),
            config_dir=str(target),
            last_used=_now_iso(),
        )
        try:
            vault.add(entry)
        except AccountVaultError as e:
            print(f"[error] {e}", file=sys.stderr)
            sys.exit(1)
        hooks_ok = _propagate_hooks(layout, name)
        print(f"[ok] account '{name}' registered from pre-loaded credentials at {target}")
        if entry.subscription_type:
            print(f"     subscriptionType: {entry.subscription_type}")
        if entry.email:
            print(f"     email: {entry.email}")
        if entry.org_id:
            print(f"     orgId: {entry.org_id}")
        if not hooks_ok:
            _warn_hook_propagation_failed(name)
            sys.exit(1)
        return

    # ── Path 2 & 3: standard flow ─────────────────────────────────────
    # --capture-current is only valid for the very first add.
    if capture_current and vault.is_initialized() and len(vault.list()) > 0:
        print(
            "[error] --capture-current is only valid for the first account add. "
            "Use plain `gits account add <name>` (which runs claude auth login).",
            file=sys.stderr,
        )
        sys.exit(1)

    if capture_current:
        creds = layout.legacy_claude_dir() / ".credentials.json"
        if not creds.exists():
            print(
                f"[error] --capture-current requires {creds} to exist with a valid "
                "claudeAiOauth.accessToken. Omit --capture-current to run claude "
                "auth login instead.",
                file=sys.stderr,
            )
            sys.exit(1)
        meta = extract_account_metadata(creds)
        if not meta or "subscriptionType" not in meta:
            print(
                f"[error] {creds} does not contain a parseable accessToken. "
                "Omit --capture-current to run claude auth login instead.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Create the directory (validates name; rsync if --capture-current).
    try:
        target = layout.create_account_dir(name, capture_current=capture_current)
    except (ValueError, AccountLayoutError) as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    # If we did NOT capture, run `claude auth login` with CLAUDE_CONFIG_DIR set.
    if not capture_current:
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = str(target)
        print(f"Running `claude auth login` with CLAUDE_CONFIG_DIR={target} ...")
        print(
            "[hint] If claude only prints a URL and hangs (paste prompt missing under "
            "subprocess), Ctrl-C, then run directly:\n"
            f"       CLAUDE_CONFIG_DIR={target} claude auth login\n"
            f"       (the marker {target}/.gits-managed is already in place)\n"
            f"       then re-run `gits account add {name}` — ghost will detect the\n"
            "       pre-loaded credentials and skip the subprocess login.",
            file=sys.stderr,
        )
        try:
            rc = subprocess.run(
                ["claude", "auth", "login"],
                env=env,
                check=False,
            ).returncode
        except FileNotFoundError:
            print("[error] `claude` not found on PATH", file=sys.stderr)
            _cleanup_account_dir(target)
            sys.exit(1)
        if rc != 0:
            print(
                f"[error] claude auth login exited with code {rc}; account creation aborted",
                file=sys.stderr,
            )
            _cleanup_account_dir(target)
            sys.exit(1)

    # Read metadata from the (possibly newly-written) credentials file.
    creds_path = layout.credentials_file(name)
    metadata = extract_account_metadata(creds_path)
    entry = AccountEntry(
        name=name,
        email=metadata.get("email"),
        org_id=metadata.get("orgId"),
        subscription_type=metadata.get("subscriptionType"),
        config_dir=str(target),
        last_used=_now_iso(),
    )
    try:
        vault.add(entry)
    except AccountVaultError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    # Hooks propagation: replicate the ghost-owned SessionStart hook into the
    # new account's settings.json. A failure here silently breaks the dispatch
    # mirror for the account, so it must be surfaced loudly (see below).
    hooks_ok = _propagate_hooks(layout, name)

    # Auto-migrate existing bindings (only on first --capture-current).
    if capture_current:
        migrated = asyncio.run(_migrate_bindings(settings, name))
        if migrated:
            print(f"[info] migrated {migrated} existing binding(s) to claude_account={name!r}")

    print(f"[ok] account '{name}' added at {target}")
    if entry.email:
        print(f"     email: {entry.email}")
    if entry.org_id:
        print(f"     orgId: {entry.org_id}")
    if entry.subscription_type:
        print(f"     subscriptionType: {entry.subscription_type}")

    if not hooks_ok:
        _warn_hook_propagation_failed(name)
        sys.exit(1)


def _cleanup_account_dir(target: Path) -> None:
    """Remove a partially-created account directory after a failed add."""
    import shutil
    if target.exists():
        try:
            shutil.rmtree(target)
        except OSError:
            logger.warning("could not clean up partial dir at %s", target)


def _propagate_hooks(layout: AccountLayout, name: str) -> bool:
    """Install the ghost ``SessionStart`` hook into a new account's settings.json.

    Reuses the existing idempotent ``_install_hook(config_dir=...)`` from
    ``__main__``. Returns ``True`` when the hook is installed afterwards,
    ``False`` on any failure. The caller (:func:`cmd_add`) is responsible
    for surfacing a failure loudly — see :func:`_warn_hook_propagation_failed`.
    """
    try:
        from .__main__ import _install_hook
        rc = _install_hook(config_dir=str(layout.account_dir(name)), quiet=True)
        if rc != 0:
            logger.warning("hook propagation to account %s returned rc=%s", name, rc)
            return False
        return True
    except Exception as e:
        logger.warning("hook propagation to account %s failed: %s", name, e)
        return False


def _warn_hook_propagation_failed(name: str) -> None:
    """Print a loud, operator-visible warning that hook propagation failed.

    Called by :func:`cmd_add` when :func:`_propagate_hooks` could not install
    the ``SessionStart`` hook. The account *is* registered — but until the
    hook is installed, dispatched sessions on it will not mirror to Discord,
    which is exactly the silent breakage this task exists to prevent.
    """
    bar = "━" * 64
    print(
        f"\n[warn] {bar}\n"
        f"[warn] account {name!r} was registered, but its Claude SessionStart\n"
        f"[warn] hook could NOT be installed in settings.json.\n"
        f"[warn]\n"
        f"[warn] Dispatched sessions on {name!r} will NOT mirror to Discord\n"
        f"[warn] until this is repaired. The account IS registered — do not\n"
        f"[warn] re-run `gits account add`. Repair with:\n"
        f"[warn]\n"
        f"[warn]     gits account fix-hooks {name}\n"
        f"[warn] {bar}",
        file=sys.stderr,
    )


async def _migrate_bindings(settings: Settings, target_account: str) -> int:
    """Drive ``SessionManager.migrate_legacy_bindings`` from the CLI."""
    from .core.session import SessionManager
    mgr = SessionManager(state_dir=settings.state_dir)
    return await mgr.migrate_legacy_bindings(target_account)


# ─────────────────────────────────────────────────────────────────────
# Command: gits account fix-hooks
# ─────────────────────────────────────────────────────────────────────


def cmd_fix_hooks(args: argparse.Namespace) -> None:
    """``gits account fix-hooks [<name>...]``.

    Ensure the Claude ``SessionStart`` hook is installed in each account's
    ``settings.json`` — the hook is what wires the dispatch mirror chain
    (Claude session start → ``gits hook`` → daemon sets ``cli_session_id``
    → ``jsonl_monitor`` forwards output to Discord). A missing hook breaks
    that chain silently.

    With no names, processes every account in the manifest; otherwise only
    the named accounts. Exit code 0 if every targeted account ends with the
    hook installed (whether already-ok or repaired); non-zero if at least
    one install failed.
    """
    from .__main__ import _install_hook, _is_hook_installed

    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)

    if not vault.is_initialized() or not vault.list():
        print("No accounts configured. Run `gits account add <name>` first.")
        return

    requested: list[str] = list(getattr(args, "names", None) or [])
    if requested:
        unknown = [n for n in requested if vault.get(n) is None]
        if unknown:
            print(
                f"[error] no such account(s): {', '.join(unknown)}",
                file=sys.stderr,
            )
            sys.exit(1)
        # Preserve caller order, de-duplicated.
        seen: set[str] = set()
        targets: list[AccountEntry] = []
        for n in requested:
            if n not in seen:
                seen.add(n)
                entry = vault.get(n)
                if entry is not None:
                    targets.append(entry)
    else:
        targets = vault.list()

    repaired = already = failed = 0
    for entry in targets:
        settings_file = Path(entry.config_dir).expanduser() / "settings.json"

        # Decide the current state *before* calling _install_hook so we can
        # report already-ok vs repaired (the installer collapses both to 0).
        current: object = {}
        unreadable = False
        if settings_file.exists():
            try:
                current = json.loads(settings_file.read_text())
            except (OSError, json.JSONDecodeError):
                unreadable = True

        if (
            not unreadable
            and isinstance(current, dict)
            and _is_hook_installed(current)
        ):
            print(f"{entry.name}: hook already installed in {settings_file}")
            already += 1
            continue

        rc = _install_hook(config_dir=entry.config_dir, quiet=True)
        if rc == 0:
            print(f"{entry.name}: installed hook in {settings_file}")
            repaired += 1
        else:
            print(
                f"{entry.name}: [error] could not install hook in {settings_file}",
                file=sys.stderr,
            )
            failed += 1

    if failed:
        print(
            f"[error] {repaired} repaired, {already} already-ok, {failed} failed",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"[ok] {repaired} repaired, {already} already-ok")


# ─────────────────────────────────────────────────────────────────────
# Command: gits account list
# ─────────────────────────────────────────────────────────────────────


def cmd_list(args: argparse.Namespace) -> None:
    """``gits account list`` — show the dispatcher's load-balanced ranking.

    Columns mirror the rule ``ghost butler dispatch --account=auto`` uses
    to pick: score = (5h load + 7d load) / weight, lowest wins, subject
    to the credential gate. Runs offline — no network calls.
    """
    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)
    if not vault.is_initialized() or not vault.list():
        print("No accounts configured. Run `gits account add <name> --capture-current` to start.")
        return

    manifest = vault.load()
    binding_counts = _binding_count_per_account(settings)
    ranked = rank_accounts(
        vault, live_binding_counts=binding_counts, layout=layout,
    )
    by_name = {row.name: row for row in ranked}

    print(_format_header())
    for entry in manifest.accounts:
        row = by_name.get(entry.name)
        if row is None:
            continue
        prefix = "*" if entry.name == manifest.default else " "
        print(_format_row(prefix, row))

    if manifest.default:
        print(f"\n[current: {manifest.default}]")
    print(f"[bindings: {sum(binding_counts.values())} total across {len(binding_counts)} accounts]")
    if manifest.default and any(row.name == manifest.default for row in ranked):
        print(
            f"[note] `*` ({manifest.default}) is the default account: it runs "
            "natively, so its load includes the operator's own interactive "
            "`claude` usage in ~/.claude — that's real pressure on its 5h/7d caps."
        )
    if any(row.rank is None for row in ranked):
        print(
            "[note] `—` in the pick? column = account excluded from auto-dispatch "
            "(no resolvable credential — `.credentials.json` missing and no "
            "macOS keychain entry). Run `claude --resume` under that account to refresh."
        )
    unset = [a.name for a in manifest.accounts if not a.weight]
    if unset and len(manifest.accounts) > 1:
        print(
            f"[warn] no `weight` set for: {', '.join(unset)}.\n"
            f"       Multi-account dispatch load-balancing assumes 1.0 — "
            f"set actual plan tier with `gits account set-weight <name> <N>` "
            f"(e.g. 20 for Max 20x, 6 for Team 6x)."
        )


def _binding_count_per_account(settings: Settings) -> dict[str, int]:
    """Cheap read of state.json — count bindings by claude_account."""
    state_file = settings.state_dir / "state.json"
    if not state_file.exists():
        return {}
    try:
        data = json.loads(state_file.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    counts: dict[str, int] = {}
    for binding in data.get("bindings", {}).values():
        acct = binding.get("claude_account")
        if isinstance(acct, str):
            counts[acct] = counts.get(acct, 0) + 1
    return counts


def _human_count(n: float) -> str:
    """Render a token-load count as ``947K`` / ``322M`` / ``1.6B`` etc.

    Raw integer when ``n < 1000``; otherwise scaled with one decimal,
    trailing ``.0`` stripped (so ``322M`` not ``322.0M``).
    """
    if n < 0:
        n = 0.0
    if n < 1000:
        return f"{int(round(n))}"
    if n < 1e6:
        scaled, suffix = n / 1e3, "K"
    elif n < 1e9:
        scaled, suffix = n / 1e6, "M"
    else:
        scaled, suffix = n / 1e9, "B"
    out = f"{scaled:.1f}"
    if out.endswith(".0"):
        out = out[:-2]
    return f"{out}{suffix}"


def _format_header() -> str:
    return (
        f"{'  name':<14} {'tier':<5} {'weight':<7} "
        f"{'5h load':<9} {'7d load':<9} {'score':<9} "
        f"{'pick?':<7} bindings"
    )


def _format_row(prefix: str, row: AccountRank) -> str:
    tier = row.tier or "—"
    weight = f"{row.weight:g}"
    return (
        f"{prefix} {row.name:<12} "
        f"{tier:<5} {weight:<7} "
        f"{_human_count(row.load_5h):<9} "
        f"{_human_count(row.load_7d):<9} "
        f"{_human_count(row.score):<9} "
        f"{format_pick_token(row.rank):<7} "
        f"{row.bindings}"
    )


# ─────────────────────────────────────────────────────────────────────
# Command: gits account switch
# ─────────────────────────────────────────────────────────────────────


def cmd_switch(args: argparse.Namespace) -> None:
    """``gits account switch <name> --binding <id>``."""
    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)
    target: str = args.name
    binding_id: str = args.binding

    if vault.get(target) is None:
        print(f"[error] no such account '{target}'", file=sys.stderr)
        sys.exit(1)

    # Run the switch through a transient Engine so we get the full kill +
    # respawn flow + manifest update. CLI path uses auto_import=False —
    # users run `gits account import` separately if they want history.
    rc = asyncio.run(_run_cli_switch(settings, binding_id, target))
    sys.exit(rc)


async def _run_cli_switch(settings: Settings, binding_id: str, target: str) -> int:
    from .core.engine import Engine

    engine = Engine(settings)
    # Don't start the full daemon; just run the switch primitive directly.
    # Engine's switch_account only requires session_mgr / launcher / tmux /
    # account_vault — all initialized in __init__. tmux operations may fail
    # if no daemon is running and the tmux session doesn't exist; the user
    # is expected to run this from a state where their daemon and tmux are alive.
    try:
        result = await engine.switch_account(binding_id, target, auto_import=False)
    except Exception as e:
        print(f"[error] switch failed: {e}", file=sys.stderr)
        return 1
    if not result.success:
        print(f"[error] switch failed: {result.error}", file=sys.stderr)
        return 1
    print(f"[ok] binding {binding_id} switched: {result.previous} → {result.target}")
    if result.respawn_failed:
        print(
            f"[warn] respawn failed; binding marked respawn_failed. "
            f"Run `gits account switch {target} --binding {binding_id}` again to retry.",
            file=sys.stderr,
        )
    return 0


# ─────────────────────────────────────────────────────────────────────
# Command: gits account remove
# ─────────────────────────────────────────────────────────────────────


def cmd_remove(args: argparse.Namespace) -> None:
    """``gits account remove <name>``."""
    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)
    name: str = args.name

    if vault.get(name) is None:
        print(f"[error] no such account '{name}'", file=sys.stderr)
        sys.exit(1)

    # Refuse if any binding still references the account.
    in_use = _bindings_using_account(settings, name)
    if in_use:
        print(
            f"[error] account '{name}' is in use by {len(in_use)} binding(s):",
            file=sys.stderr,
        )
        for cid in in_use:
            print(f"  - {cid}", file=sys.stderr)
        print(
            "Switch each binding to another account first "
            "(`gits account switch <other> --binding <id>`).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Remove the directory and manifest entry.
    import shutil
    target = layout.account_dir(name)
    if target.exists():
        shutil.rmtree(target)
    vault.remove(name)
    print(f"[ok] account '{name}' removed")


def _bindings_using_account(settings: Settings, name: str) -> list[str]:
    state_file = settings.state_dir / "state.json"
    if not state_file.exists():
        return []
    try:
        data = json.loads(state_file.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return [
        cid
        for cid, b in data.get("bindings", {}).items()
        if b.get("claude_account") == name
    ]


# ─────────────────────────────────────────────────────────────────────
# Command: gits account import
# ─────────────────────────────────────────────────────────────────────


def cmd_import(args: argparse.Namespace) -> None:
    """``gits account import <session_id> --to <name> [--from <name>] [--force]``."""
    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)

    try:
        result = import_session(
            args.session_id,
            to=args.to,
            from_=args.from_,
            force=args.force,
            vault=vault,
            layout=layout,
        )
    except (
        AccountVaultError,
        SessionNotFoundError,
        SessionAlreadyExistsError,
    ) as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
    except SessionMultipleSourcesError as e:
        print(f"[error] {e}", file=sys.stderr)
        for c in e.candidates:
            print(f"  - {c}", file=sys.stderr)
        sys.exit(1)

    src = "<legacy ~/.claude>" if result.source_account is None else result.source_account
    overwrite = " (overwrote previous target)" if result.overwrote_target else ""
    print(
        f"[ok] imported session {args.session_id}{overwrite}\n"
        f"     from {src}: {result.source_path}\n"
        f"       to {result.target_account}: {result.target_path}\n"
        f"     {result.file_size} bytes, {result.line_count} lines"
    )


# ─────────────────────────────────────────────────────────────────────
# Commands: set-default, set-tags
# ─────────────────────────────────────────────────────────────────────


def cmd_set_default(args: argparse.Namespace) -> None:
    """``gits account set-default <name>``."""
    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)
    try:
        vault.set_default(args.name)
    except AccountVaultError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[ok] manifest.default = {args.name!r}")
    print("     Restart ghost (`pm2 restart ghost-in-the-shell`) for the change to take effect.")


def cmd_set_tags(args: argparse.Namespace) -> None:
    """``gits account set-tags <name> [<tag>...]``.

    Replaces the account's tag list. Pass zero tags to clear.
    Tags surface in the ``/account-switch`` autocomplete.
    """
    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)
    try:
        vault.set_tags(args.name, args.tags or [])
    except AccountVaultError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
    if args.tags:
        print(f"[ok] {args.name} tags = {args.tags}")
    else:
        print(f"[ok] {args.name} tags cleared")


def cmd_set_weight(args: argparse.Namespace) -> None:
    """``gits account set-weight <name> <weight>``.

    Per task [[gbraq8]]: sets the account's capacity weight used by
    the dispatch load-balancer (``utilization = load / weight``).
    """
    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)
    try:
        vault.set_weight(args.name, args.weight)
    except AccountVaultError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[ok] {args.name} weight = {args.weight:g}")


# ─────────────────────────────────────────────────────────────────────
# Commands: refresh, refresh-install, refresh-uninstall, migrate-default-native
# (per openspec change ``add-default-account-native-and-refresh``)
# ─────────────────────────────────────────────────────────────────────

#: launchd label and plist filename used for the daily refresh job.
LAUNCHD_LABEL = "com.gits.token-refresh"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def cmd_refresh(args: argparse.Namespace) -> None:
    """``gits account refresh [--account <name>]``.

    Invokes ``claude --print ping`` per account to exercise claude's own
    OAuth refresh path, keeping non-default accounts' refresh tokens warm.
    Exit code 0 if all attempted refreshes succeed (or no work to do);
    1 if any failed.
    """
    from .core.token_refresh import refresh_account, refresh_all_non_default

    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)

    target = getattr(args, "account", None)
    if target:
        try:
            manifest = vault.load()
        except Exception as e:
            print(f"[error] cannot load account manifest: {e}", file=sys.stderr)
            sys.exit(1)
        is_default = (manifest.default == target)
        result = refresh_account(target, layout, is_default=is_default)
        marker = "✓" if result.success else "✗"
        suffix = f" ({result.duration_s:.1f}s)" if result.duration_s else ""
        print(f"{marker} {result.account}{suffix}")
        if not result.success:
            tail = result.stderr_tail or result.skipped_reason or "(no detail)"
            print(f"  {tail}", file=sys.stderr)
            sys.exit(1)
        return

    results = refresh_all_non_default(vault, layout)
    if not results:
        print("[ok] no non-default accounts to refresh")
        return

    any_failed = False
    for r in results:
        marker = "✓" if r.success else "✗"
        print(f"{marker} {r.account} ({r.duration_s:.1f}s)")
        if not r.success:
            any_failed = True
            tail = r.stderr_tail or r.skipped_reason or "(no detail)"
            print(f"  {tail}", file=sys.stderr)
    print(f"[summary] {sum(1 for r in results if r.success)}/{len(results)} ok")
    if any_failed:
        sys.exit(1)


_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{gits_bin}</string>
    <string>account</string>
    <string>refresh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>4</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>{log_path}</string>
  <key>StandardErrorPath</key>
  <string>{log_path}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path_env}</string>
    <key>HOME</key>
    <string>{home}</string>
  </dict>
</dict>
</plist>
"""


def cmd_refresh_install(args: argparse.Namespace) -> None:
    """``gits account refresh-install`` — schedule daily refresh.

    On macOS: writes ``~/Library/LaunchAgents/com.gits.token-refresh.plist``
    and bootstraps it with launchctl. On Linux: prints a cron snippet for
    the user to install manually.
    """
    gits_bin = shutil.which("gits")
    if not gits_bin:
        print(
            "[error] `gits` not found on PATH; cannot schedule. "
            "Install gits globally first.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not _is_macos():
        print(
            "[info] Linux scheduling is not auto-installed. Add this to your crontab:\n"
            f"\n  0 4 * * * {gits_bin} account refresh >> ~/.gits/token-refresh.log 2>&1\n"
        )
        return

    log_dir = Path.home() / ".gits"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "token-refresh.log"
    plist_path = _plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    # Preserve a sane PATH for the launchd-spawned process; pull from the
    # invoking shell so user-installed claude (e.g. ~/.local/bin) resolves.
    path_env = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")

    plist_path.write_text(_PLIST_TEMPLATE.format(
        label=LAUNCHD_LABEL,
        gits_bin=gits_bin,
        log_path=str(log_path),
        path_env=path_env,
        home=str(Path.home()),
    ))
    plist_path.chmod(0o644)

    # Bootstrap (re-bootstrap is idempotent if we bootout first)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist_path)],
                   capture_output=True)
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(plist_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(
            f"[error] launchctl bootstrap failed (rc={result.returncode}):\n"
            f"  stderr: {result.stderr.strip()}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"[ok] installed daily refresh at 04:00 → {plist_path}")
    print(f"     logs: {log_path}")


def cmd_refresh_uninstall(args: argparse.Namespace) -> None:
    """``gits account refresh-uninstall`` — remove the daily refresh job."""
    if not _is_macos():
        print("[info] no-op on Linux; remove the cron entry manually if present.")
        return

    plist_path = _plist_path()
    if not plist_path.exists():
        print("[ok] no refresh job installed; nothing to do.")
        return

    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist_path)],
                   capture_output=True)
    plist_path.unlink()
    print(f"[ok] uninstalled {plist_path}")


def cmd_migrate_default_native(args: argparse.Namespace) -> None:
    """``gits account migrate-default-native [--apply]``.

    Compares ``~/.claude/.credentials.json`` vs ``~/.claude-<default>/.credentials.json``.
    Default mode prints which is newer and what would be done. With
    ``--apply``, copies the newer file over the older one (atomic
    temp+rename, mode 0600). The macOS keychain is NOT touched — claude
    will sync it on next invocation.
    """
    settings = Settings()
    layout = AccountLayout()
    vault = AccountVault(settings.state_dir, layout=layout)
    try:
        manifest = vault.load()
    except Exception as e:
        print(f"[error] cannot load manifest: {e}", file=sys.stderr)
        sys.exit(1)

    default = manifest.default
    if not default:
        print("[ok] no default account set; nothing to migrate.")
        return

    native = layout.legacy_claude_dir() / ".credentials.json"
    isolated = layout.account_dir(default) / ".credentials.json"

    if not native.exists() and not isolated.exists():
        print(f"[ok] neither {native} nor {isolated} exists; nothing to migrate.")
        return
    if not isolated.exists():
        print(f"[ok] {isolated} missing; native is already authoritative.")
        return
    if not native.exists():
        action = f"copy {isolated} → {native}"
    else:
        native_mt = native.stat().st_mtime
        isolated_mt = isolated.stat().st_mtime
        if native_mt > isolated_mt:
            action = f"copy {native} → {isolated} (native is newer)"
        elif isolated_mt > native_mt:
            action = f"copy {isolated} → {native} (isolated is newer)"
        else:
            print(f"[ok] {native} and {isolated} have identical mtimes; no action.")
            return

    if not getattr(args, "apply", False):
        print(f"[dry-run] would: {action}")
        print("           re-run with --apply to perform the copy.")
        return

    answer = input(f"Apply: {action} ? [y/N] ").strip().lower()
    if answer != "y":
        print("[aborted] not applying.")
        return

    src, dst = (isolated, native) if isolated.stat().st_mtime > native.stat().st_mtime else (native, isolated)
    if not native.exists():
        src, dst = isolated, native

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_bytes(src.read_bytes())
    tmp.chmod(0o600)
    tmp.replace(dst)
    print(f"[ok] copied {src} → {dst}")


# ─────────────────────────────────────────────────────────────────────
# Parser registration
# ─────────────────────────────────────────────────────────────────────


def install_parser(parent_subparsers: argparse._SubParsersAction) -> None:
    """Install ``gits account <subcmd>`` parser tree (alias: ``gits acct``)."""
    for cmd_name in ("account", "acct"):
        acct_p = parent_subparsers.add_parser(
            cmd_name,
            help="Manage Claude accounts (CLAUDE_CONFIG_DIR-based isolation)",
        )
        ssub = acct_p.add_subparsers(dest="account_subcommand", required=True)

        p_add = ssub.add_parser(
            "add",
            help="Register a new account; runs `claude auth login` unless --capture-current.",
        )
        p_add.add_argument("name", help="Account name (e.g., personal, work)")
        p_add.add_argument(
            "--capture-current",
            dest="capture_current",
            action="store_true",
            help=(
                "Snapshot the existing ~/.claude/ contents (including credentials) "
                "as the new account. ONLY valid as the first `gits account add`. "
                "Triggers automatic migration of all existing bindings to this account."
            ),
        )
        p_add.set_defaults(_handler=cmd_add)

        p_fixhooks = ssub.add_parser(
            "fix-hooks",
            help=(
                "Ensure the Claude SessionStart hook is installed in every "
                "account's settings.json (self-heal for the dispatch mirror)"
            ),
        )
        p_fixhooks.add_argument(
            "names",
            nargs="*",
            help="Account name(s) to repair (default: all accounts)",
        )
        p_fixhooks.set_defaults(_handler=cmd_fix_hooks)

        p_list = ssub.add_parser(
            "list",
            help="List all accounts with load-balanced dispatch ranking (offline; no network)",
        )
        p_list.set_defaults(_handler=cmd_list)

        p_switch = ssub.add_parser(
            "switch",
            help="Switch a specific binding's claude account",
        )
        p_switch.add_argument("name", help="Target account name")
        p_switch.add_argument(
            "--binding",
            required=True,
            help="Binding (channel) id to switch",
        )
        p_switch.set_defaults(_handler=cmd_switch)

        p_remove = ssub.add_parser(
            "remove",
            help="Delete an account (refuses if any binding still uses it)",
        )
        p_remove.add_argument("name")
        p_remove.set_defaults(_handler=cmd_remove)

        p_import = ssub.add_parser(
            "import",
            help="Copy a session JSONL between accounts",
        )
        p_import.add_argument("session_id", help="Session UUID (without .jsonl)")
        p_import.add_argument(
            "--to",
            required=True,
            help="Target account name",
        )
        p_import.add_argument(
            "--from",
            dest="from_",
            default=None,
            help="Source account name (required when session id matches multiple locations)",
        )
        p_import.add_argument(
            "--force",
            action="store_true",
            help="Overwrite an existing target file (preserves a .gits-bak)",
        )
        p_import.set_defaults(_handler=cmd_import)

        p_refresh = ssub.add_parser(
            "refresh",
            help="Run `claude --print ping` per non-default account to refresh OAuth tokens",
        )
        p_refresh.add_argument(
            "--account",
            default=None,
            help="Refresh a single account (including default); skips the all-accounts loop",
        )
        p_refresh.set_defaults(_handler=cmd_refresh)

        p_install = ssub.add_parser(
            "refresh-install",
            help="Schedule daily refresh (launchd on macOS; cron snippet on Linux)",
        )
        p_install.set_defaults(_handler=cmd_refresh_install)

        p_uninstall = ssub.add_parser(
            "refresh-uninstall",
            help="Remove the scheduled daily refresh job",
        )
        p_uninstall.set_defaults(_handler=cmd_refresh_uninstall)

        p_migrate = ssub.add_parser(
            "migrate-default-native",
            help="Reconcile ~/.claude/.credentials.json with ~/.claude-<default>/ (dry-run by default)",
        )
        p_migrate.add_argument(
            "--apply",
            action="store_true",
            help="Actually perform the copy (default is dry-run)",
        )
        p_migrate.set_defaults(_handler=cmd_migrate_default_native)

        p_setdef = ssub.add_parser(
            "set-default",
            help="Set the manifest default account (routes through native ~/.claude/)",
        )
        p_setdef.add_argument("name", help="Account name to make default")
        p_setdef.set_defaults(_handler=cmd_set_default)

        p_settags = ssub.add_parser(
            "set-tags",
            help="Replace an account's tags (shown in /account-switch autocomplete)",
        )
        p_settags.add_argument("name", help="Account name")
        p_settags.add_argument(
            "tags", nargs="*", help="Tags (zero or more; clears if empty)",
        )
        p_settags.set_defaults(_handler=cmd_set_tags)

        p_setw = ssub.add_parser(
            "set-weight",
            help=(
                "Set account capacity weight for dispatch load-balancing "
                "(e.g. 20 for Max 20x, 6 for Team 6x)"
            ),
        )
        p_setw.add_argument("name", help="Account name")
        p_setw.add_argument(
            "weight", type=float, help="Capacity weight (positive number)"
        )
        p_setw.set_defaults(_handler=cmd_set_weight)


def dispatch(args: argparse.Namespace) -> None:
    """Run the handler attached by ``set_defaults(_handler=...)``."""
    handler = getattr(args, "_handler", None)
    if handler is None:
        print(
            "usage: gits account <add|fix-hooks|list|switch|remove|import|refresh|"
            "refresh-install|refresh-uninstall|migrate-default-native|"
            "set-default|set-tags|set-weight>",
            file=sys.stderr,
        )
        sys.exit(1)
    handler(args)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
