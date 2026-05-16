"""Shared onboarding config: which guild + category to create new
worktree channels under.

Lives at ``~/.gits/butler-onboarding.json`` (user-writable, sits next to
``~/.gits/config.env`` where the bot token lives).

On first read, if the user file is missing, we seed it from
``onboarding.default.json`` shipped inside the package — so a new
contributor gets the shared team defaults without having to ask anyone
for guild/category IDs. (Replaces the prior arrangement where butler's
``onboarding.json`` was git-tracked next to ``butler.py`` in the vault.)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from importlib.resources import files
from pathlib import Path

USER_PATH = Path("~/.gits/butler-onboarding.json").expanduser()
DEFAULT_RESOURCE = "onboarding.default.json"


def _packaged_default_path() -> Path | None:
    """Filesystem path to the packaged default, or None if missing.

    importlib.resources returns a Traversable; for our wheel layout it's
    always a real path, so str() / Path() round-trips cleanly.
    """
    try:
        res = files("gits.butler").joinpath(DEFAULT_RESOURCE)
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    p = Path(str(res))
    return p if p.exists() else None


def _seed_default_if_missing() -> None:
    if USER_PATH.exists():
        return
    default = _packaged_default_path()
    if default is None:
        return
    USER_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(default, USER_PATH)


def load() -> dict | None:
    """Read user onboarding config; seed from packaged default on first read."""
    _seed_default_if_missing()
    if not USER_PATH.exists():
        return None
    try:
        with open(USER_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def require() -> dict:
    cfg = load()
    if not cfg:
        sys.exit(
            f"ghost butler: no onboarding config at {USER_PATH}.\n"
            f"  Run: ghost butler config-onboarding --category <category_id>\n"
            f"  (find a category id with: "
            f"ghost discord channel list --guild <guild_id>)"
        )
    return cfg


def save(cfg: dict) -> None:
    USER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_PATH, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")


def user_path_str() -> str:
    """Display string for the user-writable config path."""
    return str(USER_PATH)
