"""Tests for ``/account-switch`` autocomplete rank rendering.

Task [[wn0yqz]] — the dropdown surfaces the dispatcher's load-balanced
rank token alongside the existing tag list so the operator can pick the
least-loaded account at a glance. ``#1 ←`` marks the row
``ghost butler dispatch --account=auto`` would land on; ``#N`` marks
eligible non-pick rows; ``—`` marks credential-gated rows the dispatcher
would skip.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gits.adapters.discord import bot as bot_mod
from gits.adapters.discord.bot import (
    DiscordAdapter,
    _clear_rank_cache,
    _compose_autocomplete_label,
)
from gits.core.account import AccountLayout
from gits.core.account_vault import AccountEntry, AccountVault, Manifest

# ─── helpers ─────────────────────────────────────────────────────────


def _iso(epoch: float) -> str:
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.UTC).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


def _write_jsonl(path: Path, events: list[dict], *, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    os.utime(path, (mtime, mtime))


def _make_creds(layout: AccountLayout, name: str) -> None:
    p = layout.credentials_file(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"claudeAiOauth": {"accessToken": "x"}}')
    p.chmod(0o600)


def _assistant_event(ts: float, input_tokens: int) -> dict:
    return {
        "type": "assistant",
        "timestamp": _iso(ts),
        "message": {"usage": {"input_tokens": input_tokens}},
    }


def _build_vault(tmp_path: Path, *, accounts, default: str):
    """Build a vault populated with ``accounts``: list of (name, weight, tier, tags)."""
    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    m = Manifest(default=default)
    for spec in accounts:
        name, weight, tier, tags = (
            spec if len(spec) == 4 else (*spec, [])  # backwards-compat
        )
        m.accounts.append(AccountEntry(
            name=name, weight=weight, subscription_type=tier,
            config_dir=str(layout.account_dir(name)),
            tags=list(tags),
        ))
    vault.save(m)
    return vault, layout


def _make_adapter(vault: AccountVault, layout: AccountLayout, *, binding_account=None,
                  bindings: list[str] | None = None) -> DiscordAdapter:
    """Construct a DiscordAdapter shell with a mocked engine for autocomplete tests."""
    adapter = DiscordAdapter.__new__(DiscordAdapter)
    engine = MagicMock()
    engine.account_vault = vault
    engine.account_layout = layout

    def _get_binding(channel_id):
        if binding_account is None:
            return None
        b = MagicMock()
        b.claude_account = binding_account
        return b

    engine.session_mgr.get_binding = MagicMock(side_effect=_get_binding)

    def _list_bindings():
        out = []
        for acct in bindings or []:
            b = MagicMock()
            b.claude_account = acct
            out.append(b)
        return out

    engine.session_mgr.list_bindings = MagicMock(side_effect=_list_bindings)
    adapter._engine = engine
    return adapter


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    _clear_rank_cache()
    yield
    _clear_rank_cache()


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    yield tmp_path


# ─── _compose_autocomplete_label ─────────────────────────────────────


class TestComposeLabel:
    def test_baseline_format(self):
        out = _compose_autocomplete_label(
            "sharon-team", "#1 ←", ["current", "default"], ["6x Team"],
        )
        assert out == "sharon-team #1 ← (current, default, 6x Team)"

    def test_eligible_non_pick(self):
        out = _compose_autocomplete_label("sharon", "#2", [], ["20x Max"])
        assert out == "sharon #2 (20x Max)"

    def test_gated_dash(self):
        out = _compose_autocomplete_label("nocreds", "—", [], ["20x Max"])
        assert "—" in out

    def test_no_tags(self):
        out = _compose_autocomplete_label("sharon", "#1 ←", [], [])
        assert out == "sharon #1 ←"

    def test_truncation_drops_user_tags_first(self):
        """Long user tags drop; rank + state tags survive."""
        out = _compose_autocomplete_label(
            "sharon",
            "#1 ←",
            ["current", "default"],
            ["a" * 200],  # huge user tag pushes label over 100
        )
        assert len(out) <= 100
        assert "#1 ←" in out                 # rank survives
        assert "(current, default)" in out  # state tags survive
        assert "aaaa" not in out             # user tag was dropped

    def test_truncation_falls_back_to_name_ellipsis(self):
        """When state tags alone don't fit, truncate the name; never drop rank."""
        out = _compose_autocomplete_label(
            "a" * 200, "#1 ←", ["current", "default"], ["20x Max"],
        )
        assert len(out) <= 100
        assert "#1 ←" in out                 # rank survives
        assert "(current, default)" in out  # state tags survive
        assert "…" in out                    # name was ellipsis-truncated

    def test_truncation_never_drops_rank_even_with_no_state_tags(self):
        out = _compose_autocomplete_label("a" * 200, "#3", [], [])
        assert len(out) <= 100
        assert "#3" in out
        assert "…" in out


# ─── _build_account_switch_choices ───────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


class TestBuildAccountSwitchChoices:
    def test_rank_token_on_every_eligible_row(self, fake_home):
        vault, layout = _build_vault(
            fake_home,
            accounts=[
                ("alpha", 20.0, "max", ["20x Max"]),
                ("beta", 6.0, "max", ["6x Team"]),
                ("gamma", 20.0, "max", ["20x Max"]),
            ],
            default="alpha",
        )
        for n in ("alpha", "beta", "gamma"):
            _make_creds(layout, n)

        # alpha heaviest, gamma medium, beta lightest → beta = #1
        now = time.time()
        recent = now - 300
        _write_jsonl(
            layout.projects_dir("alpha") / "h" / "s.jsonl",
            [_assistant_event(recent, 100_000_000)],
            mtime=recent,
        )
        _write_jsonl(
            layout.projects_dir("gamma") / "h" / "s.jsonl",
            [_assistant_event(recent, 10_000_000)],
            mtime=recent,
        )
        _write_jsonl(
            layout.projects_dir("beta") / "h" / "s.jsonl",
            [_assistant_event(recent, 1_000)],
            mtime=recent,
        )

        adapter = _make_adapter(vault, layout, binding_account="alpha")
        choices = _run(adapter._build_account_switch_choices("ch1", ""))

        labels = {c.value: c.name for c in choices}
        assert "#1 ←" in labels["beta"]      # lowest score gets the arrow
        assert "#2" in labels["gamma"]
        assert "#3" in labels["alpha"]
        # Existing tag logic preserved
        assert "current" in labels["alpha"]  # binding_account
        assert "default" in labels["alpha"]
        assert "20x Max" in labels["alpha"]
        assert "6x Team" in labels["beta"]

    def test_credential_gated_row_shows_dash(self, fake_home):
        vault, layout = _build_vault(
            fake_home,
            accounts=[
                ("creds", 1.0, "max", []),
                ("nocreds", 1.0, "max", []),
            ],
            default="creds",
        )
        _make_creds(layout, "creds")  # nocreds intentionally missing

        adapter = _make_adapter(vault, layout, binding_account="creds")
        with patch(
            "gits.core.account_load._macos_keychain_entry_exists",
            return_value=False,
        ):
            choices = _run(adapter._build_account_switch_choices("ch1", ""))

        labels = {c.value: c.name for c in choices}
        assert "—" in labels["nocreds"]
        assert "#1 ←" in labels["creds"]

    def test_single_account_install_renders_pick_arrow(self, fake_home):
        """Operator decision (Q2): single-account install shows ``#1 ←``."""
        vault, layout = _build_vault(
            fake_home,
            accounts=[("only", 1.0, "max", [])],
            default="only",
        )
        _make_creds(layout, "only")

        adapter = _make_adapter(vault, layout, binding_account="only")
        choices = _run(adapter._build_account_switch_choices("ch1", ""))

        assert len(choices) == 1
        assert "#1 ←" in choices[0].name

    def test_substring_filter_preserves_global_rank(self, fake_home):
        """Filtering the visible rows must not renumber — rank is dispatcher-truth."""
        vault, layout = _build_vault(
            fake_home,
            accounts=[
                ("alpha", 20.0, "max", []),
                ("beta", 6.0, "max", []),
                ("gamma", 20.0, "max", []),
            ],
            default="alpha",
        )
        for n in ("alpha", "beta", "gamma"):
            _make_creds(layout, n)

        now = time.time()
        recent = now - 300
        _write_jsonl(
            layout.projects_dir("alpha") / "h" / "s.jsonl",
            [_assistant_event(recent, 100_000_000)],
            mtime=recent,
        )
        _write_jsonl(
            layout.projects_dir("gamma") / "h" / "s.jsonl",
            [_assistant_event(recent, 10_000_000)],
            mtime=recent,
        )
        _write_jsonl(
            layout.projects_dir("beta") / "h" / "s.jsonl",
            [_assistant_event(recent, 1_000)],
            mtime=recent,
        )

        adapter = _make_adapter(vault, layout, binding_account="alpha")
        # Type "lp" — substring-matches only alpha. Despite hiding the
        # other two rows, alpha's token should still say ``#3`` (its
        # rank in the *full* manifest), because the dispatcher's pick
        # space is the full manifest, not the filter view.
        choices = _run(adapter._build_account_switch_choices("ch1", "lp"))
        names = {c.value for c in choices}
        assert names == {"alpha"}
        assert "#3" in choices[0].name

    def test_truncation_documented_label_fits_100_chars(self, fake_home):
        """Acceptance: choice labels fit Discord's 100-char cap.

        Account names are capped at 32 chars by the vault validator, so
        the realistic overflow path is a long user tag. We verify that
        the composer drops user tags first, preserving the rank token.
        """
        name = "x" * 32  # max-length name allowed by AccountVault
        long_tag = "this-is-a-very-long-user-tag-that-pushes-the-label-past-100-chars"
        vault, layout = _build_vault(
            fake_home,
            accounts=[(name, 1.0, "max", [long_tag])],
            default=name,
        )
        _make_creds(layout, name)

        adapter = _make_adapter(vault, layout, binding_account=name)
        choices = _run(adapter._build_account_switch_choices("ch1", ""))
        assert len(choices) == 1
        assert len(choices[0].name) <= 100
        assert "#1 ←" in choices[0].name      # rank survives
        assert long_tag not in choices[0].name  # user tag was dropped

    def test_twenty_five_choice_cap(self, fake_home):
        """Discord's app_commands cap is 25 choices — autocomplete must respect it."""
        accounts = [(f"acct{i:02}", 1.0, "max", []) for i in range(30)]
        vault, layout = _build_vault(fake_home, accounts=accounts, default="acct00")
        for spec in accounts:
            _make_creds(layout, spec[0])

        adapter = _make_adapter(vault, layout, binding_account="acct00")
        choices = _run(adapter._build_account_switch_choices("ch1", ""))
        assert len(choices) == 25

    def test_no_oauth_usage_api_in_autocomplete_path(self, fake_home):
        """Acceptance: autocomplete must not reach the network.

        Block AF_INET/AF_INET6 only — asyncio's event loop creates an
        AF_UNIX socketpair for its wakeup pipe, which is not what this
        acceptance check is guarding against.
        """
        vault, layout = _build_vault(
            fake_home,
            accounts=[("alpha", 1.0, "max", []), ("beta", 1.0, "max", [])],
            default="alpha",
        )
        _make_creds(layout, "alpha")
        _make_creds(layout, "beta")

        adapter = _make_adapter(vault, layout, binding_account="alpha")

        import socket as _socket
        _real_socket = _socket.socket

        def _maybe_block(family=_socket.AF_INET, *a, **kw):
            if family in (_socket.AF_INET, _socket.AF_INET6):
                raise AssertionError(
                    f"autocomplete opened a network socket — "
                    f"family={family!r} args={a!r}"
                )
            return _real_socket(family, *a, **kw)

        with patch.object(_socket, "socket", _maybe_block):
            choices = _run(adapter._build_account_switch_choices("ch1", ""))
        assert len(choices) == 2


# ─── _cached_rank_accounts (TTL behavior) ────────────────────────────


class TestRankCache:
    def test_second_call_within_ttl_hits_cache(self, fake_home):
        """Per-keystroke calls must not re-run the JSONL scan."""
        vault, layout = _build_vault(
            fake_home,
            accounts=[("alpha", 1.0, "max", []), ("beta", 1.0, "max", [])],
            default="alpha",
        )
        _make_creds(layout, "alpha")
        _make_creds(layout, "beta")

        call_count = {"n": 0}
        real_rank = bot_mod.rank_accounts

        def _counting_rank(*args, **kwargs):
            call_count["n"] += 1
            return real_rank(*args, **kwargs)

        adapter = _make_adapter(vault, layout, binding_account="alpha")
        with patch.object(bot_mod, "rank_accounts", side_effect=_counting_rank):
            _run(adapter._build_account_switch_choices("ch1", "a"))
            _run(adapter._build_account_switch_choices("ch1", "al"))
            _run(adapter._build_account_switch_choices("ch1", "alp"))

        # Three keystrokes, but only one underlying scan.
        assert call_count["n"] == 1

    def test_clear_cache_forces_refresh(self, fake_home):
        vault, layout = _build_vault(
            fake_home,
            accounts=[("alpha", 1.0, "max", [])],
            default="alpha",
        )
        _make_creds(layout, "alpha")

        call_count = {"n": 0}
        real_rank = bot_mod.rank_accounts

        def _counting_rank(*args, **kwargs):
            call_count["n"] += 1
            return real_rank(*args, **kwargs)

        adapter = _make_adapter(vault, layout, binding_account="alpha")
        with patch.object(bot_mod, "rank_accounts", side_effect=_counting_rank):
            _run(adapter._build_account_switch_choices("ch1", ""))
            _clear_rank_cache()
            _run(adapter._build_account_switch_choices("ch1", ""))

        assert call_count["n"] == 2
