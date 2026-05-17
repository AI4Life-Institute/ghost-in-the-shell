"""Tests for ``gits.core.account_vault``."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from gits.core.account import AccountLayout
from gits.core.account_vault import (
    AccountEntry,
    AccountVault,
    AccountVaultError,
    Manifest,
    decode_access_token_metadata,
    extract_account_metadata,
)


# ----------------------------------------------------------------------
# JWT decode
# ----------------------------------------------------------------------


def _make_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = "sig"
    return f"{header}.{body}.{sig}"


def test_decode_jwt_valid() -> None:
    token = _make_jwt({"email": "a@b.com", "orgId": "org-1"})
    out = decode_access_token_metadata(token)
    assert out == {"email": "a@b.com", "orgId": "org-1"}


def test_decode_jwt_malformed() -> None:
    assert decode_access_token_metadata("not-a-jwt") == {}
    assert decode_access_token_metadata("") == {}
    assert decode_access_token_metadata("a.b") == {}
    assert decode_access_token_metadata("a.b.c.d") == {}


def test_decode_jwt_non_json_payload() -> None:
    payload = base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode()
    token = f"hdr.{payload}.sig"
    assert decode_access_token_metadata(token) == {}


def test_decode_jwt_payload_not_dict() -> None:
    payload = base64.urlsafe_b64encode(b'["array","not","dict"]').rstrip(b"=").decode()
    token = f"hdr.{payload}.sig"
    assert decode_access_token_metadata(token) == {}


# ----------------------------------------------------------------------
# extract_account_metadata
# ----------------------------------------------------------------------


def test_extract_metadata_full(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    token = _make_jwt({"email": "user@example.com", "orgId": "org-x"})
    creds.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": token,
                    "refreshToken": "ref",
                    "subscriptionType": "max",
                }
            }
        )
    )
    out = extract_account_metadata(creds)
    assert out["email"] == "user@example.com"
    assert out["orgId"] == "org-x"
    assert out["subscriptionType"] == "max"


def test_extract_metadata_missing_file(tmp_path: Path) -> None:
    assert extract_account_metadata(tmp_path / "does-not-exist") == {}


def test_extract_metadata_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{ not json")
    assert extract_account_metadata(p) == {}


def test_extract_metadata_no_oauth_key(tmp_path: Path) -> None:
    p = tmp_path / "creds.json"
    p.write_text('{"foo": "bar"}')
    assert extract_account_metadata(p) == {}


def test_extract_metadata_organization_id_alias(tmp_path: Path) -> None:
    creds = tmp_path / ".credentials.json"
    token = _make_jwt({"organizationId": "org-alt"})
    creds.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": token, "subscriptionType": "pro"}})
    )
    out = extract_account_metadata(creds)
    assert out.get("orgId") == "org-alt"


# ----------------------------------------------------------------------
# Manifest dataclass round-trip
# ----------------------------------------------------------------------


def test_manifest_round_trip() -> None:
    m = Manifest(
        default="alpha",
        accounts=[
            AccountEntry(
                name="alpha",
                email="a@x.com",
                org_id="o1",
                subscription_type="max",
                config_dir="/home/u/.claude-alpha",
                last_used="2026-04-28T00:00:00",
                tags=["personal"],
            )
        ],
        last_switch={"at": "x", "binding_id": "b1", "from": None, "to": "alpha", "reason": ""},
        last_import=None,
    )
    d = m.to_dict()
    assert d["default"] == "alpha"
    assert d["accounts"][0]["orgId"] == "o1"
    assert d["accounts"][0]["subscriptionType"] == "max"
    m2 = Manifest.from_dict(d)
    assert m2.accounts[0].org_id == "o1"
    assert m2.accounts[0].subscription_type == "max"
    assert m2.default == "alpha"


def test_manifest_drops_legacy_rate_limited_until() -> None:
    raw = {
        "default": "alpha",
        "accounts": [
            {
                "name": "alpha",
                "config_dir": "/x",
                "rateLimitedUntil": "2099-01-01T00:00:00",  # legacy field
            }
        ],
    }
    m = Manifest.from_dict(raw)
    # Round-trip: legacy field is gone
    assert "rateLimitedUntil" not in m.to_dict()["accounts"][0]


# ----------------------------------------------------------------------
# AccountVault CRUD
# ----------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> AccountVault:
    state_dir = tmp_path / ".gits"
    layout = AccountLayout(home=tmp_path)
    return AccountVault(state_dir, layout=layout)


def test_vault_uninitialized(vault: AccountVault) -> None:
    assert not vault.is_initialized()
    m = vault.load()
    assert m.default is None
    assert m.accounts == []


def test_vault_add_first_sets_default(vault: AccountVault) -> None:
    entry = AccountEntry(name="alpha", config_dir="/tmp/.claude-alpha")
    m = vault.add(entry)
    assert m.default == "alpha"
    assert vault.is_initialized()
    # Persisted
    m2 = vault.load()
    assert len(m2.accounts) == 1
    assert m2.default == "alpha"


def test_vault_add_second_keeps_existing_default(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    vault.add(AccountEntry(name="beta", config_dir="/y"))
    m = vault.load()
    assert m.default == "alpha"
    assert [a.name for a in m.accounts] == ["alpha", "beta"]


def test_vault_add_duplicate_raises(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    with pytest.raises(AccountVaultError, match="already in manifest"):
        vault.add(AccountEntry(name="alpha", config_dir="/y"))


def test_vault_remove_non_default(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x", last_used="2026-04-28T01"))
    vault.add(AccountEntry(name="beta", config_dir="/y", last_used="2026-04-28T02"))
    m = vault.remove("beta")
    assert [a.name for a in m.accounts] == ["alpha"]
    assert m.default == "alpha"  # unchanged


def test_vault_remove_default_picks_next(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x", last_used="2026-04-28T01"))
    vault.add(AccountEntry(name="beta", config_dir="/y", last_used="2026-04-28T05"))
    vault.add(AccountEntry(name="gamma", config_dir="/z", last_used="2026-04-28T03"))
    # Default is alpha (first). Remove alpha → next default is beta (most recent lastUsed)
    m = vault.remove("alpha")
    assert m.default == "beta"


def test_vault_remove_default_when_empty(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    m = vault.remove("alpha")
    assert m.default is None
    assert m.accounts == []


def test_vault_remove_unknown(vault: AccountVault) -> None:
    with pytest.raises(AccountVaultError, match="no such account"):
        vault.remove("ghost")


def test_vault_set_default(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    vault.add(AccountEntry(name="beta", config_dir="/y"))
    m = vault.set_default("beta")
    assert m.default == "beta"


def test_vault_set_default_unknown(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    with pytest.raises(AccountVaultError):
        vault.set_default("ghost")


def test_vault_set_default_clear(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    m = vault.set_default(None)
    assert m.default is None


def test_vault_update_last_used(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    m = vault.update_last_used("alpha", when="2026-04-28T10:00:00")
    assert m.accounts[0].last_used == "2026-04-28T10:00:00"


def test_vault_set_tags(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    m = vault.set_tags("alpha", ["20x Max", "primary"])
    assert m.accounts[0].tags == ["20x Max", "primary"]
    # Clear
    m = vault.set_tags("alpha", [])
    assert m.accounts[0].tags == []


def test_vault_set_tags_unknown_account(vault: AccountVault) -> None:
    with pytest.raises(AccountVaultError):
        vault.set_tags("ghost", ["x"])


def test_vault_record_switch(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    vault.add(AccountEntry(name="beta", config_dir="/y"))
    m = vault.record_switch(
        binding_id="b1", from_="alpha", to="beta", reason="user", when="2026-04-28T12"
    )
    assert m.last_switch["binding_id"] == "b1"
    assert m.last_switch["to"] == "beta"
    assert m.last_switch["partial"] is False
    # Per add-default-account-native-and-refresh: record_switch must NOT
    # mutate manifest.default. Default stays sticky on the user's
    # primary account; only set_default() changes it.
    assert m.default == "alpha"
    # beta lastUsed bumped
    beta = next(a for a in m.accounts if a.name == "beta")
    assert beta.last_used == "2026-04-28T12"


def test_vault_record_switch_partial(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    vault.add(AccountEntry(name="beta", config_dir="/y"))
    m = vault.record_switch(
        binding_id="b1", from_="alpha", to="beta", reason="user", partial=True,
    )
    assert m.last_switch["partial"] is True


def test_vault_record_import(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    vault.add(AccountEntry(name="beta", config_dir="/y"))
    m = vault.record_import(
        session_id="abc-123", from_="alpha", to="beta", when="2026-04-28T13"
    )
    assert m.last_import == {
        "at": "2026-04-28T13",
        "session_id": "abc-123",
        "from": "alpha",
        "to": "beta",
    }


def test_vault_atomic_write_mode(vault: AccountVault) -> None:
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    st = vault.manifest_path.stat()
    # 0600 file mode
    assert (st.st_mode & 0o777) == 0o600


def test_vault_atomic_write_no_partial_on_crash(tmp_path: Path, monkeypatch) -> None:
    vault = AccountVault(tmp_path / ".gits", layout=AccountLayout(home=tmp_path))
    vault.add(AccountEntry(name="alpha", config_dir="/x"))
    original = vault.load()

    # Simulate a write failure mid-save by making Path.replace raise
    original_replace = Path.replace

    def boom(self, *args, **kwargs):
        if str(self).endswith(".tmp"):
            raise OSError("disk full")
        return original_replace(self, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        vault.add(AccountEntry(name="beta", config_dir="/y"))

    # Restore so reload works
    monkeypatch.setattr(Path, "replace", original_replace)

    # Original manifest still readable; beta was not added
    m = vault.load()
    assert [a.name for a in m.accounts] == [a.name for a in original.accounts]
    # No leftover .tmp files
    leftover = list(vault.manifest_path.parent.glob("*.tmp"))
    assert leftover == []


def test_vault_load_corrupted_raises(vault: AccountVault) -> None:
    vault.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    vault.manifest_path.write_text("{ not json")
    with pytest.raises(AccountVaultError, match="unreadable"):
        vault.load()


def test_vault_load_non_object_raises(vault: AccountVault) -> None:
    vault.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    vault.manifest_path.write_text("[]")
    with pytest.raises(AccountVaultError, match="not a JSON object"):
        vault.load()
