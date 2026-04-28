"""Backward-compatibility guards for the multi-subscription feature.

The feature must be purely additive: when ``~/.gits/subscriptions/`` does not
exist, ghost behaves exactly as before this change.
"""

import asyncio
from pathlib import Path

import pytest

from gits.config import Settings
from gits.core.subscription import SubscriptionVault


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    monkeypatch.setattr("gits.core.subscription._read_keychain", lambda: None)
    monkeypatch.setattr("gits.core.subscription._write_keychain", lambda payload: True)
    monkeypatch.setattr("gits.core.subscription._delete_keychain", lambda: True)


class TestVaultDormant:
    def test_exists_false_on_fresh_install(self, tmp_path):
        v = SubscriptionVault(tmp_path / "subscriptions")
        assert v.exists() is False

    def test_load_empty_when_dormant(self, tmp_path):
        v = SubscriptionVault(tmp_path / "subscriptions")
        m = v.load()
        assert m.active is None
        assert m.subscriptions == []

    def test_no_files_created_unless_explicitly_used(self, tmp_path):
        SubscriptionVault(tmp_path / "subscriptions")
        # Construction alone must not create directories
        assert not (tmp_path / "subscriptions").exists()


class TestEngineDormant:
    def test_engine_starts_without_vault(self, tmp_path):
        # Engine should construct fine; the vault dir is allowed to be missing
        from gits.core.engine import Engine

        settings = Settings(gits_dir=tmp_path, gits_discord_token="x")
        engine = Engine(settings)
        # Vault is wired but dormant
        assert engine.subscription_vault.exists() is False
        assert engine.switch_primitive is not None
        # No subscriptions/ directory created during init
        assert not (tmp_path / "subscriptions").exists()

    # Note: the previous ``handle_subscriptions_list`` Discord handler was
    # removed when the deprecated ``/subscriptions`` and ``/sub-switch`` slash
    # commands were dropped (per openspec change ``add-multi-account-hotswap``,
    # Phase 0.12). Replacement Discord surface: ``/accounts`` +
    # ``/account-switch``. Account-management Discord handlers are tested in
    # ``tests/test_switch_account.py`` (auto-import) and via the
    # ``handle_account_switch`` / ``handle_accounts_list`` smoke paths in the
    # engine module itself.


class TestRollback:
    def test_remove_vault_dir_restores_dormant_state(self, tmp_path, monkeypatch):
        """Deleting ~/.gits/subscriptions/ MUST not break ghost — vault.exists()
        flips to False and engine treats subscriptions as not configured."""
        import json
        import shutil

        # Set up a fake claude credentials file
        claude_cred = tmp_path / "claude" / ".credentials.json"
        claude_cred.parent.mkdir(parents=True)
        claude_cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "x"}}))

        vault_dir = tmp_path / "subscriptions"
        vault = SubscriptionVault(vault_dir, claude_credentials_path=claude_cred)
        asyncio.run(vault.add("alice"))
        assert vault.exists() is True

        # User wipes the vault
        shutil.rmtree(vault_dir)

        v2 = SubscriptionVault(vault_dir, claude_credentials_path=claude_cred)
        assert v2.exists() is False
        # Live credentials file is left untouched
        assert claude_cred.exists()
