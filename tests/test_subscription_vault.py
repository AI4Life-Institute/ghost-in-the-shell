"""Tests for SubscriptionVault."""

import asyncio
import json
from pathlib import Path

import pytest

from gits.core.subscription import (
    LastSwitch,
    Manifest,
    Subscription,
    SubscriptionVault,
    SubscriptionVaultError,
    parse_credential_file,
)

# Sample credential payloads
CRED_A = {
    "claudeAiOauth": {
        "accessToken": "tok-A",
        "refreshToken": "rt-A",
        "expiresAt": 1000,
        "scopes": ["user:profile"],
        "subscriptionType": "max",
        "rateLimitTier": "standard",
    }
}
CRED_B = {
    "claudeAiOauth": {
        "accessToken": "tok-B",
        "refreshToken": "rt-B",
        "expiresAt": 2000,
        "scopes": ["user:profile"],
        "subscriptionType": "pro",
        "rateLimitTier": "standard",
    }
}


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    """File-only mode for vault tests."""
    monkeypatch.setattr("gits.core.subscription._read_keychain", lambda: None)
    monkeypatch.setattr("gits.core.subscription._write_keychain", lambda payload: True)
    monkeypatch.setattr("gits.core.subscription._delete_keychain", lambda: True)


@pytest.fixture
def vault_dir(tmp_path):
    return tmp_path / "subscriptions"


@pytest.fixture
def claude_cred_file(tmp_path):
    """Mock ~/.claude/.credentials.json."""
    p = tmp_path / "claude" / ".credentials.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(CRED_A))
    p.chmod(0o600)
    return p


@pytest.fixture
def vault(vault_dir, claude_cred_file):
    return SubscriptionVault(vault_dir, claude_credentials_path=claude_cred_file)


class TestVaultLifecycle:
    def test_exists_false_when_no_manifest(self, vault):
        assert vault.exists() is False

    def test_load_empty(self, vault):
        m = vault.load()
        assert m.active is None
        assert m.subscriptions == []

    def test_load_ignores_legacy_auto_switch_field(self, vault, vault_dir):
        import json

        vault_dir.mkdir(parents=True, exist_ok=True)
        (vault_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "active": None,
                    "subscriptions": [],
                    "last_switch": None,
                    "auto_switch_enabled": False,
                }
            )
        )
        m = vault.load()
        assert m.active is None
        assert not hasattr(m, "auto_switch_enabled")

    def test_save_and_reload(self, vault):
        m = Manifest(
            active="alice",
            subscriptions=[Subscription(name="alice", email="a@x.com")],
            last_switch=LastSwitch(at="2026-01-01T00:00:00Z", from_name=None, to_name="alice", reason="add"),
        )
        asyncio.run(vault.save(m))
        m2 = vault.load()
        assert m2.active == "alice"
        assert len(m2.subscriptions) == 1
        assert m2.subscriptions[0].email == "a@x.com"
        assert m2.last_switch.reason == "add"

    def test_corrupt_manifest_raises(self, vault):
        vault.vault_dir.mkdir(parents=True, exist_ok=True)
        vault.manifest_path.write_text("{not json")
        with pytest.raises(SubscriptionVaultError):
            vault.load()


class TestAdd:
    def test_first_add_becomes_active(self, vault):
        m = asyncio.run(vault.add("work", email="me@work.com", subscription_type="max"))
        assert m.active == "work"
        assert vault.credentials_path("work").exists()
        assert vault.credentials_path("work").stat().st_mode & 0o777 == 0o600
        # Snapshot should match source
        with open(vault.credentials_path("work")) as f:
            assert json.load(f) == CRED_A

    def test_second_add_does_not_change_active(self, vault, claude_cred_file):
        asyncio.run(vault.add("work", email="me@work.com"))
        # Update live cred file to simulate a fresh login
        claude_cred_file.write_text(json.dumps(CRED_B))
        m = asyncio.run(vault.add("home", email="me@home.com"))
        assert m.active == "work"  # unchanged
        assert len(m.subscriptions) == 2

    def test_add_duplicate_rejected(self, vault):
        asyncio.run(vault.add("work"))
        with pytest.raises(SubscriptionVaultError, match="already exists"):
            asyncio.run(vault.add("work"))

    def test_add_without_credentials_file(self, vault, claude_cred_file):
        claude_cred_file.unlink()
        with pytest.raises(SubscriptionVaultError, match="No live credentials"):
            asyncio.run(vault.add("work"))


class TestRemove:
    def test_remove_non_active(self, vault, claude_cred_file):
        asyncio.run(vault.add("work"))
        claude_cred_file.write_text(json.dumps(CRED_B))
        asyncio.run(vault.add("home"))
        m = asyncio.run(vault.remove("home"))
        assert m.active == "work"
        assert m.get("home") is None
        assert not vault.credentials_path("home").exists()

    def test_remove_active_rejected(self, vault):
        asyncio.run(vault.add("work"))
        with pytest.raises(SubscriptionVaultError, match="Cannot remove active"):
            asyncio.run(vault.remove("work"))

    def test_remove_active_with_force(self, vault):
        asyncio.run(vault.add("work"))
        m = asyncio.run(vault.remove("work", force=True))
        assert m.active is None
        assert m.get("work") is None
        assert not vault.credentials_path("work").exists()

    def test_remove_missing_rejected(self, vault):
        with pytest.raises(SubscriptionVaultError, match="not found"):
            asyncio.run(vault.remove("ghost"))


class TestUpdateActive:
    def test_update_active_records_switch(self, vault, claude_cred_file):
        asyncio.run(vault.add("work"))
        claude_cred_file.write_text(json.dumps(CRED_B))
        asyncio.run(vault.add("home"))
        m = asyncio.run(vault.update_active("home", from_name="work", reason="manual"))
        assert m.active == "home"
        assert m.last_switch.from_name == "work"
        assert m.last_switch.to_name == "home"
        assert m.last_switch.reason == "manual"

    def test_update_active_unknown_rejected(self, vault):
        asyncio.run(vault.add("work"))
        with pytest.raises(SubscriptionVaultError, match="not found"):
            asyncio.run(vault.update_active("ghost", from_name="work", reason="manual"))


class TestRateLimit:
    def test_set_and_clear(self, vault):
        asyncio.run(vault.add("work"))
        m = asyncio.run(vault.update_rate_limit("work", 9999999999.0))
        assert m.get("work").rate_limited_until == 9999999999.0
        m = asyncio.run(vault.update_rate_limit("work", None))
        assert m.get("work").rate_limited_until is None


class TestCandidate:
    def test_candidate_picks_least_recently_used(self, vault, claude_cred_file):
        asyncio.run(vault.add("a"))
        claude_cred_file.write_text(json.dumps(CRED_B))
        asyncio.run(vault.add("b"))
        # Bump 'a' usage to be more recent
        asyncio.run(vault.update_active("a", from_name="b", reason="manual"))
        m = vault.load()
        # Candidate excluding active should be 'b' (it has the older last_used)
        assert vault.candidate(m, exclude="a") == "b"

    def test_candidate_excludes_rate_limited(self, vault, claude_cred_file):
        asyncio.run(vault.add("a"))
        claude_cred_file.write_text(json.dumps(CRED_B))
        asyncio.run(vault.add("b"))
        asyncio.run(vault.update_rate_limit("b", 9999999999.0))
        m = vault.load()
        assert vault.candidate(m, exclude="a") is None  # only 'b' remains, but rate-limited

    def test_candidate_includes_expired_rate_limit(self, vault, claude_cred_file):
        asyncio.run(vault.add("a"))
        claude_cred_file.write_text(json.dumps(CRED_B))
        asyncio.run(vault.add("b"))
        asyncio.run(vault.update_rate_limit("b", 1.0))  # epoch 1970
        m = vault.load()
        assert vault.candidate(m, exclude="a") == "b"


class TestSnapshotRoundtrip:
    def test_writeback_then_restore(self, vault, claude_cred_file):
        asyncio.run(vault.add("work"))
        # Simulate OAuth refresh — live file changes
        claude_cred_file.write_text(json.dumps(CRED_B))
        # Writeback captures the refreshed content
        vault.snapshot_to_vault("work")
        # Now restore should bring back the refreshed content
        claude_cred_file.unlink()
        vault.restore_to_active_path("work")
        with open(claude_cred_file) as f:
            assert json.load(f) == CRED_B
        assert claude_cred_file.stat().st_mode & 0o777 == 0o600


class TestKeychainIntegration:
    """macOS keychain interaction. Mocked to avoid touching real keychain."""

    def test_read_live_prefers_keychain(self, monkeypatch, claude_cred_file):
        from gits.core.subscription import _read_live_credentials

        # File has CRED_A; keychain has CRED_B → keychain wins
        kc_payload = json.dumps(CRED_B)
        monkeypatch.setattr("gits.core.subscription._read_keychain", lambda: kc_payload)

        out = _read_live_credentials(claude_cred_file)
        assert json.loads(out)["claudeAiOauth"]["accessToken"] == "tok-B"

    def test_read_live_falls_back_to_file(self, monkeypatch, claude_cred_file):
        from gits.core.subscription import _read_live_credentials

        monkeypatch.setattr("gits.core.subscription._read_keychain", lambda: None)
        out = _read_live_credentials(claude_cred_file)
        assert json.loads(out)["claudeAiOauth"]["accessToken"] == "tok-A"

    def test_read_live_returns_none_when_neither(self, monkeypatch, tmp_path):
        from gits.core.subscription import _read_live_credentials

        monkeypatch.setattr("gits.core.subscription._read_keychain", lambda: None)
        assert _read_live_credentials(tmp_path / "missing.json") is None

    def test_write_live_writes_both(self, monkeypatch, tmp_path):
        from gits.core.subscription import _write_live_credentials

        target = tmp_path / "creds.json"
        kc_calls: list[str] = []

        def fake_write_kc(payload: str) -> bool:
            kc_calls.append(payload)
            return True

        monkeypatch.setattr("gits.core.subscription._write_keychain", fake_write_kc)
        # Force the platform check to think we're on macOS for this test
        monkeypatch.setattr("gits.core.subscription.sys.platform", "darwin")

        payload = json.dumps(CRED_B)
        file_ok, keychain_ok = _write_live_credentials(target, payload)
        assert file_ok is True
        assert keychain_ok is True
        assert target.read_text() == payload
        assert kc_calls == [payload]
        assert target.stat().st_mode & 0o777 == 0o600

    def test_snapshot_captures_keychain_when_present(
        self, monkeypatch, vault, claude_cred_file
    ):
        # File holds CRED_A but keychain holds CRED_B → snapshot should write CRED_B.
        kc_payload = json.dumps(CRED_B)
        monkeypatch.setattr("gits.core.subscription._read_keychain", lambda: kc_payload)
        vault.snapshot_to_vault("work")
        with open(vault.credentials_path("work")) as f:
            stored = json.load(f)
        assert stored["claudeAiOauth"]["accessToken"] == "tok-B"

    def test_restore_writes_keychain_too(
        self, monkeypatch, vault, claude_cred_file
    ):
        from gits.core.subscription import sys as _sub_sys
        # add a subscription using current creds (CRED_A)
        asyncio.run(vault.add("work"))
        # change file to something else, then restore vault[work]
        claude_cred_file.write_text(json.dumps(CRED_B))

        kc_writes: list[str] = []
        monkeypatch.setattr(
            "gits.core.subscription._write_keychain",
            lambda p: kc_writes.append(p) or True,
        )
        monkeypatch.setattr("gits.core.subscription.sys.platform", "darwin")

        vault.restore_to_active_path("work")
        assert json.loads(claude_cred_file.read_text())["claudeAiOauth"]["accessToken"] == "tok-A"
        assert len(kc_writes) == 1
        assert json.loads(kc_writes[0])["claudeAiOauth"]["accessToken"] == "tok-A"


class TestParseCredentialFile:
    def test_valid_file(self, tmp_path):
        p = tmp_path / "creds.json"
        p.write_text(json.dumps(CRED_A))
        oauth = parse_credential_file(p)
        assert oauth["accessToken"] == "tok-A"
        assert oauth["subscriptionType"] == "max"

    def test_missing_file(self, tmp_path):
        assert parse_credential_file(tmp_path / "nope.json") == {}

    def test_malformed_file(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        assert parse_credential_file(p) == {}
