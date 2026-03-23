"""openclaw-compatible account storage.

File layout (mirrors the real openclaw gateway):
  ~/.openclaw/<channel>/accounts.json          — list of raw account IDs
  ~/.openclaw/<channel>/accounts/<id>.json     — per-account data
  ~/.openclaw/<channel>/accounts/<id>.sync.json — get_updates cursor
"""

from __future__ import annotations

import json
from pathlib import Path

_OPENCLAW_DIR = Path("~/.openclaw").expanduser()


# ── ID normalisation ──────────────────────────────────────────────────────────

def normalize_account_id(raw: str) -> str:
    """Mirror openclaw's normalizeAccountId: lowercase, non-[a-z0-9_] → '-'."""
    s = raw.strip().lower()
    if not s:
        return "default"
    result = ""
    for ch in s:
        if ch.isalnum() or ch == "_":
            result += ch
        else:
            result += "-"
    while "--" in result:
        result = result.replace("--", "-")
    result = result.strip("-")
    return result[:64] or "default"


# ── Path helpers ──────────────────────────────────────────────────────────────

def _channel_dir(channel: str) -> Path:
    return _OPENCLAW_DIR / channel


def _accounts_dir(channel: str) -> Path:
    return _channel_dir(channel) / "accounts"


def _accounts_index(channel: str) -> Path:
    return _channel_dir(channel) / "accounts.json"


def _account_file(channel: str, account_id: str) -> Path:
    return _accounts_dir(channel) / f"{normalize_account_id(account_id)}.json"


def _sync_file(channel: str, account_id: str) -> Path:
    return _accounts_dir(channel) / f"{normalize_account_id(account_id)}.sync.json"


# ── Public API ────────────────────────────────────────────────────────────────

def _load_account(channel: str, raw_id: str) -> dict | None:
    """Load a single account by raw ID, or None if missing/invalid."""
    f = _account_file(channel, raw_id)
    if not f.exists():
        # also try raw filename (legacy / hand-written accounts)
        f_raw = _accounts_dir(channel) / f"{raw_id}.json"
        if f_raw.exists():
            f = f_raw
        else:
            return None
    try:
        data = json.loads(f.read_text())
        return {
            "account_id": raw_id,
            "normalized_id": normalize_account_id(raw_id),
            "token": data.get("token", ""),
            "base_url": data.get("baseUrl", "").rstrip("/"),
            "user_id": data.get("userId", ""),
            "saved_at": data.get("savedAt", ""),
        }
    except Exception:
        return None


def discover(channel: str) -> dict | None:
    """Return the first available account for the channel, or None."""
    index = _accounts_index(channel)
    if not index.exists():
        return None
    try:
        ids: list[str] = json.loads(index.read_text())
    except Exception:
        return None
    for raw_id in ids:
        acct = _load_account(channel, raw_id)
        if acct is not None:
            return acct
    return None


def discover_all(channel: str) -> list[dict]:
    """Return all available accounts for the channel."""
    index = _accounts_index(channel)
    if not index.exists():
        return []
    try:
        ids: list[str] = json.loads(index.read_text())
    except Exception:
        return []
    accounts = []
    for raw_id in ids:
        acct = _load_account(channel, raw_id)
        if acct is not None:
            accounts.append(acct)
    return accounts


def save(channel: str, account: dict) -> None:
    """Write an account to the openclaw file layout.

    ``account`` must contain ``account_id``, ``token``, ``base_url``,
    and optionally ``user_id``.  Updates the accounts.json index.
    """
    raw_id: str = account["account_id"]
    norm_id = normalize_account_id(raw_id)
    acct_dir = _accounts_dir(channel)
    acct_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "token": account.get("token", ""),
        "baseUrl": account.get("base_url", ""),
        "userId": account.get("user_id", ""),
        "savedAt": account.get("saved_at", ""),
    }
    (acct_dir / f"{norm_id}.json").write_text(json.dumps(data, indent=2))

    index_file = _accounts_index(channel)
    try:
        ids: list[str] = json.loads(index_file.read_text()) if index_file.exists() else []
    except Exception:
        ids = []
    if raw_id not in ids:
        ids.insert(0, raw_id)
    index_file.write_text(json.dumps(ids))


def load_sync_buf(channel: str, account_id: str) -> str:
    """Return the saved get_updates cursor, or empty string."""
    f = _sync_file(channel, account_id)
    if not f.exists():
        # also try raw (legacy)
        f_raw = _accounts_dir(channel) / f"{account_id}.sync.json"
        if f_raw.exists():
            f = f_raw
        else:
            return ""
    try:
        return json.loads(f.read_text()).get("get_updates_buf", "")
    except Exception:
        return ""


def remove(channel: str, account_id: str) -> None:
    """Remove an account from storage: deletes data/sync files and updates the index."""
    norm_id = normalize_account_id(account_id)
    acct_dir = _accounts_dir(channel)

    for fname in [f"{norm_id}.json", f"{account_id}.json",
                  f"{norm_id}.sync.json", f"{account_id}.sync.json"]:
        f = acct_dir / fname
        if f.exists():
            f.unlink()

    index_file = _accounts_index(channel)
    try:
        ids: list[str] = json.loads(index_file.read_text()) if index_file.exists() else []
    except Exception:
        ids = []
    ids = [i for i in ids if i != account_id]
    index_file.write_text(json.dumps(ids))


def save_sync_buf(channel: str, account_id: str, buf: str) -> None:
    """Persist the get_updates cursor."""
    try:
        f = _sync_file(channel, account_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"get_updates_buf": buf}))
    except Exception:
        pass
