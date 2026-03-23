"""Per-account admin tracking for WeChat bot accounts.

State file: ~/.gits/weixin_admin_{normalized_account_id}.json

Bootstrap: the first user to message a bot becomes its admin automatically.
Only admins can run /addbot to register new bot accounts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _file(account_id: str) -> Path:
    safe = account_id.replace("/", "-").replace("\\", "-")[:64]
    return Path(f"~/.gits/weixin_admin_{safe}.json").expanduser()


def _load(account_id: str) -> dict:
    f = _file(account_id)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {"admins": {}}


def _save(account_id: str, data: dict) -> None:
    f = _file(account_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def is_empty(account_id: str) -> bool:
    """Return True if no admins have been registered yet (first-run bootstrap)."""
    return not _load(account_id)["admins"]


def is_locked(account_id: str) -> bool:
    """Return True if this bot was shared without admin rights.

    Locked bots do NOT auto-promote the first user to admin.
    The owner must explicitly use /share admin to allow it.
    """
    return _load(account_id).get("locked", False)


def lock(account_id: str) -> None:
    """Mark bot as locked — first user will NOT be auto-promoted to admin."""
    data = _load(account_id)
    data["locked"] = True
    _save(account_id, data)


def is_admin(account_id: str, user_id: str) -> bool:
    return user_id in _load(account_id)["admins"]


def add_admin(account_id: str, user_id: str) -> None:
    data = _load(account_id)
    data["admins"][user_id] = {
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(account_id, data)


def set_workspace(account_id: str, path: str) -> None:
    """Associate a locked workspace directory with this bot account."""
    data = _load(account_id)
    data["workspace"] = path
    _save(account_id, data)


def get_workspace(account_id: str) -> str | None:
    """Return the locked workspace path for this account, or None."""
    return _load(account_id).get("workspace")


def set_label(account_id: str, label: str) -> None:
    """Store a human-readable label for this shared account."""
    data = _load(account_id)
    data["label"] = label
    _save(account_id, data)


def get_label(account_id: str) -> str | None:
    """Return the human-readable label for this account, or None."""
    return _load(account_id).get("label")
