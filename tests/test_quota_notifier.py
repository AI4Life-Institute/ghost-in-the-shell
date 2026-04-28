"""Tests for QuotaNotifier."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from gits.core.quota import QuotaCategory, QuotaExhaustedEvent, QuotaMatch
from gits.core.quota_notifier import QuotaNotifier
from gits.core.subscription import LastSwitch, Manifest, Subscription, SubscriptionVault


@pytest.fixture(autouse=True)
def _no_keychain(monkeypatch):
    monkeypatch.setattr("gits.core.subscription._read_keychain", lambda: None)
    monkeypatch.setattr("gits.core.subscription._write_keychain", lambda payload: True)
    monkeypatch.setattr("gits.core.subscription._delete_keychain", lambda: True)


@pytest.fixture
def vault_with_two(tmp_path):
    vault_dir = tmp_path / "subscriptions"
    vault_dir.mkdir(parents=True)
    (vault_dir / "alice").mkdir()
    (vault_dir / "alice" / "credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "ra"}})
    )
    (vault_dir / "bob").mkdir()
    (vault_dir / "bob" / "credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "b", "refreshToken": "rb"}})
    )
    vault = SubscriptionVault(vault_dir, claude_credentials_path=tmp_path / ".cred")
    manifest = Manifest(
        active="alice",
        subscriptions=[Subscription(name="alice"), Subscription(name="bob")],
    )
    asyncio.run(vault.save(manifest))
    return vault


def _event(reset_at: float | None) -> QuotaExhaustedEvent:
    return QuotaExhaustedEvent(
        channel_id="chan",
        match=QuotaMatch(
            category=QuotaCategory.HARD_LIMIT,
            matched_text="rate limit reached, resets at 2026-01-01T00:00:00Z",
            pattern_index=0,
            reset_at=reset_at,
        ),
    )


class TestQuotaNotifier:
    def test_event_with_reset_marks_rate_limit_and_notifies(self, vault_with_two):
        sent: list[str] = []

        async def notify(text: str) -> None:
            sent.append(text)

        notifier = QuotaNotifier(vault_with_two, notify=notify)
        reset = time.time() + 3600
        asyncio.run(notifier._handle_event(_event(reset)))

        manifest = vault_with_two.load()
        alice = manifest.get("alice")
        assert alice is not None
        assert alice.rate_limited_until == pytest.approx(reset, abs=1)
        assert any("alice" in s and "resets at" in s for s in sent)

    def test_event_without_reset_only_notifies(self, vault_with_two):
        sent: list[str] = []

        async def notify(text: str) -> None:
            sent.append(text)

        notifier = QuotaNotifier(vault_with_two, notify=notify)
        asyncio.run(notifier._handle_event(_event(None)))

        manifest = vault_with_two.load()
        alice = manifest.get("alice")
        assert alice is not None
        assert alice.rate_limited_until is None
        assert any("reset time unknown" in s for s in sent)

    def test_no_active_subscription_drops_event(self, tmp_path):
        vault_dir = tmp_path / "subscriptions"
        vault_dir.mkdir(parents=True)
        vault = SubscriptionVault(vault_dir, claude_credentials_path=tmp_path / ".c")
        asyncio.run(vault.save(Manifest()))

        sent: list[str] = []

        async def notify(text: str) -> None:
            sent.append(text)

        notifier = QuotaNotifier(vault, notify=notify)
        asyncio.run(notifier._handle_event(_event(time.time() + 3600)))
        assert sent == []

    def test_notifier_does_not_call_switch_primitive(self, vault_with_two):
        """Sanity check: notifier has no reference to a switch primitive."""
        notifier = QuotaNotifier(vault_with_two)
        # The whole point of this rewrite: no switch_primitive attribute exists.
        assert not hasattr(notifier, "primitive")
        assert not hasattr(notifier, "switch_primitive")
