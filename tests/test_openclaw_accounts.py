"""Unit tests for src/gits/openclaw/accounts.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gits.openclaw.accounts import (
    discover,
    load_sync_buf,
    normalize_account_id,
    save,
    save_sync_buf,
)


# ── normalize_account_id ─────────────────────────────────────────────────────

class TestNormalizeAccountId:
    def test_email_like_id(self):
        assert normalize_account_id("b0d5982b9b4d@im.bot") == "b0d5982b9b4d-im-bot"

    def test_already_normalized(self):
        assert normalize_account_id("myaccount") == "myaccount"

    def test_uppercase_lowercased(self):
        assert normalize_account_id("MyAccount") == "myaccount"

    def test_special_chars_become_dashes(self):
        assert normalize_account_id("foo@bar.baz") == "foo-bar-baz"

    def test_multiple_specials_collapse(self):
        assert normalize_account_id("a..b") == "a-b"

    def test_leading_trailing_specials_stripped(self):
        assert normalize_account_id("@foo@") == "foo"

    def test_empty_returns_default(self):
        assert normalize_account_id("") == "default"

    def test_whitespace_only_returns_default(self):
        assert normalize_account_id("   ") == "default"

    def test_underscore_kept(self):
        assert normalize_account_id("foo_bar") == "foo_bar"

    def test_max_64_chars(self):
        long_id = "a" * 100
        result = normalize_account_id(long_id)
        assert len(result) == 64

    def test_all_specials_returns_default(self):
        assert normalize_account_id("@@@") == "default"


# ── discover ─────────────────────────────────────────────────────────────────

class TestDiscover:
    def _write_account(
        self, openclaw_dir: Path, channel: str, raw_id: str, data: dict
    ) -> None:
        from gits.openclaw.accounts import normalize_account_id
        norm = normalize_account_id(raw_id)
        acct_dir = openclaw_dir / channel / "accounts"
        acct_dir.mkdir(parents=True, exist_ok=True)
        (acct_dir / f"{norm}.json").write_text(json.dumps(data))
        index = openclaw_dir / channel / "accounts.json"
        ids = json.loads(index.read_text()) if index.exists() else []
        ids.append(raw_id)
        index.write_text(json.dumps(ids))

    def test_returns_none_when_no_index(self, tmp_path):
        import gits.openclaw.accounts as mod
        orig = mod._OPENCLAW_DIR
        mod._OPENCLAW_DIR = tmp_path
        try:
            assert discover("openclaw-weixin") is None
        finally:
            mod._OPENCLAW_DIR = orig

    def test_returns_first_account(self, tmp_path):
        import gits.openclaw.accounts as mod
        orig = mod._OPENCLAW_DIR
        mod._OPENCLAW_DIR = tmp_path
        try:
            self._write_account(
                tmp_path,
                "openclaw-weixin",
                "b0d5982b9b4d@im.bot",
                {"token": "tok123", "baseUrl": "https://example.com", "userId": "u1"},
            )
            result = discover("openclaw-weixin")
            assert result is not None
            assert result["token"] == "tok123"
            assert result["base_url"] == "https://example.com"
            assert result["user_id"] == "u1"
            assert result["account_id"] == "b0d5982b9b4d@im.bot"
            assert result["normalized_id"] == "b0d5982b9b4d-im-bot"
        finally:
            mod._OPENCLAW_DIR = orig

    def test_skips_missing_account_file(self, tmp_path):
        import gits.openclaw.accounts as mod
        orig = mod._OPENCLAW_DIR
        mod._OPENCLAW_DIR = tmp_path
        try:
            channel_dir = tmp_path / "openclaw-weixin"
            channel_dir.mkdir(parents=True)
            (channel_dir / "accounts.json").write_text(json.dumps(["ghost@missing"]))
            assert discover("openclaw-weixin") is None
        finally:
            mod._OPENCLAW_DIR = orig

    def test_base_url_trailing_slash_stripped(self, tmp_path):
        import gits.openclaw.accounts as mod
        orig = mod._OPENCLAW_DIR
        mod._OPENCLAW_DIR = tmp_path
        try:
            self._write_account(
                tmp_path,
                "chan",
                "myid",
                {"token": "t", "baseUrl": "https://example.com/", "userId": ""},
            )
            result = discover("chan")
            assert result["base_url"] == "https://example.com"
        finally:
            mod._OPENCLAW_DIR = orig


# ── save / load_sync_buf / save_sync_buf ─────────────────────────────────────

class TestSyncBuf:
    def test_roundtrip(self, tmp_path):
        import gits.openclaw.accounts as mod
        orig = mod._OPENCLAW_DIR
        mod._OPENCLAW_DIR = tmp_path
        try:
            save_sync_buf("chan", "myid", "cursor-xyz")
            assert load_sync_buf("chan", "myid") == "cursor-xyz"
        finally:
            mod._OPENCLAW_DIR = orig

    def test_missing_returns_empty(self, tmp_path):
        import gits.openclaw.accounts as mod
        orig = mod._OPENCLAW_DIR
        mod._OPENCLAW_DIR = tmp_path
        try:
            assert load_sync_buf("chan", "noexist") == ""
        finally:
            mod._OPENCLAW_DIR = orig


class TestSave:
    def test_saves_and_discovers(self, tmp_path):
        import gits.openclaw.accounts as mod
        orig = mod._OPENCLAW_DIR
        mod._OPENCLAW_DIR = tmp_path
        try:
            save(
                "chan",
                {
                    "account_id": "user@example.com",
                    "token": "mytoken",
                    "base_url": "https://api.example.com",
                    "user_id": "uid1",
                },
            )
            result = discover("chan")
            assert result is not None
            assert result["token"] == "mytoken"
            assert result["account_id"] == "user@example.com"
        finally:
            mod._OPENCLAW_DIR = orig

    def test_updates_index(self, tmp_path):
        import gits.openclaw.accounts as mod
        orig = mod._OPENCLAW_DIR
        mod._OPENCLAW_DIR = tmp_path
        try:
            save("chan", {"account_id": "a1", "token": "t1", "base_url": "", "user_id": ""})
            save("chan", {"account_id": "a2", "token": "t2", "base_url": "", "user_id": ""})
            index = json.loads((tmp_path / "chan" / "accounts.json").read_text())
            assert "a1" in index
            assert "a2" in index
        finally:
            mod._OPENCLAW_DIR = orig
