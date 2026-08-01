"""CLI handler for ``gits resource`` / ``gits efficiency`` (task [[jeyuxq]]).

Prints the current red/green watchdog snapshot — the same resource +
token data the in-process watchdog samples, rendered human-readable for
ad-hoc checks. Read-only + zero-network, like the watchdog itself.
"""

from __future__ import annotations

import argparse

from .config import Settings
from .core.account import AccountLayout
from .core.account_vault import AccountVault


def install_parser(sub: argparse._SubParsersAction) -> None:
    """Register ``gits resource`` and alias ``gits efficiency``."""
    for verb in ("resource", "efficiency"):
        p = sub.add_parser(
            verb,
            help="Show the current resource + token watchdog snapshot",
        )
        p.add_argument(
            "--no-token",
            action="store_true",
            help="Skip the token-balance face (resource only)",
        )


def dispatch(args: argparse.Namespace) -> None:
    from .core import resource_watch as rw
    from .core.watchdog_config import load_watchdog_config

    settings = Settings()
    config = load_watchdog_config()
    layout = AccountLayout()

    res = rw.sample_resources(config)

    if getattr(args, "no_token", False):
        tok = rw.TokenSample()
    else:
        vault = AccountVault(state_dir=settings.state_dir, layout=layout)
        tok = rw.sample_tokens(vault, config, layout=layout)

    print(rw.format_snapshot(res, tok, config.thresholds))
