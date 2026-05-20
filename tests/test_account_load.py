"""Tests for ``gits.core.account_load`` — local-JSONL load + picker."""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from gits.core.account import AccountLayout
from gits.core.account_load import (
    WINDOW_5H,
    WINDOW_7D,
    AccountRank,
    account_load,
    account_load_dual,
    pick_account,
    rank_accounts,
)
from gits.core.account_vault import AccountEntry, AccountVault, Manifest

# Fixed epoch to keep the fixture deterministic.
_FAKE_NOW = 1_700_000_000.0


def _iso(epoch: float) -> str:
    return (
        _dt.datetime.fromtimestamp(epoch, tz=_dt.UTC)
        .strftime("%Y-%m-%dT%H:%M:%S.000Z")
    )


def _assistant(ts: float, **usage) -> dict:
    return {
        "type": "assistant",
        "timestamp": _iso(ts),
        "message": {
            "usage": {
                "input_tokens": usage.get("input", 0),
                "output_tokens": usage.get("output", 0),
                "cache_creation_input_tokens": usage.get("cache_create", 0),
                "cache_read_input_tokens": usage.get("cache_read", 0),
            }
        },
    }


def _write_jsonl(path: Path, events: list, *, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for e in events:
            if isinstance(e, str):
                f.write(e + "\n")
            else:
                f.write(json.dumps(e) + "\n")
    os.utime(path, (mtime, mtime))


def _make_creds(layout: AccountLayout, name: str) -> None:
    p = layout.credentials_file(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"claudeAiOauth": {"accessToken": "x"}}')
    p.chmod(0o600)


def _vault_with(tmp_path, accounts, default):
    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    m = Manifest(default=default)
    for name, weight in accounts:
        m.accounts.append(AccountEntry(
            name=name, weight=weight,
            config_dir=str(layout.account_dir(name)),
        ))
    vault.save(m)
    return vault, layout


# ─── account_load ────────────────────────────────────────────────────────


def test_account_load_basic_window(tmp_path):
    layout = AccountLayout(home=tmp_path)
    proj = layout.projects_dir("alpha") / "abc"
    _write_jsonl(
        proj / "s.jsonl",
        [
            _assistant(_FAKE_NOW - 100, input=10, output=20),
            _assistant(_FAKE_NOW - 10 * 86400, input=1000, output=2000),  # out
        ],
        mtime=_FAKE_NOW - 50,
    )
    load = account_load("alpha", WINDOW_7D, now=_FAKE_NOW, layout=layout)
    # In-window only: 10*1 + 20*5 = 110
    assert load == pytest.approx(110.0)


def test_account_load_mtime_prefilter_skips_old_file(tmp_path):
    layout = AccountLayout(home=tmp_path)
    proj = layout.projects_dir("alpha") / "abc"
    _write_jsonl(
        proj / "old.jsonl",
        [_assistant(_FAKE_NOW - 100, input=1, output=1)],
        mtime=_FAKE_NOW - 30 * 86400,  # file mtime far outside window
    )
    assert account_load("alpha", WINDOW_7D, now=_FAKE_NOW, layout=layout) == 0.0


def test_account_load_skips_malformed_and_missing_fields(tmp_path):
    layout = AccountLayout(home=tmp_path)
    proj = layout.projects_dir("alpha") / "abc"
    proj.mkdir(parents=True)
    p = proj / "mixed.jsonl"
    _write_jsonl(
        p,
        [
            "not json",
            _assistant(_FAKE_NOW - 50, input=4),
            {"type": "user", "message": {"content": "hi"}},
            {"type": "assistant", "timestamp": _iso(_FAKE_NOW)},  # no usage
        ],
        mtime=_FAKE_NOW - 10,
    )
    assert account_load("alpha", WINDOW_7D, now=_FAKE_NOW, layout=layout) == pytest.approx(4.0)


def test_account_load_missing_dir_is_zero(tmp_path):
    layout = AccountLayout(home=tmp_path)
    assert account_load("absent", WINDOW_7D, now=_FAKE_NOW, layout=layout) == 0.0


def test_weighting_formula(tmp_path):
    layout = AccountLayout(home=tmp_path)
    proj = layout.projects_dir("alpha") / "abc"
    _write_jsonl(
        proj / "f.jsonl",
        [_assistant(_FAKE_NOW - 50,
                    input=100, output=10, cache_create=80, cache_read=200)],
        mtime=_FAKE_NOW - 50,
    )
    # 100*1 + 10*5 + 80*1.25 + 200*0.1 = 100 + 50 + 100 + 20 = 270
    assert account_load("alpha", WINDOW_7D, now=_FAKE_NOW, layout=layout) == pytest.approx(270.0)


def test_account_load_dual_single_pass(tmp_path):
    layout = AccountLayout(home=tmp_path)
    proj = layout.projects_dir("alpha") / "abc"
    _write_jsonl(
        proj / "f.jsonl",
        [
            _assistant(_FAKE_NOW - 60, input=10),         # in 5h ∩ 7d
            _assistant(_FAKE_NOW - 6 * 3600, input=100),  # in 7d only
            _assistant(_FAKE_NOW - 8 * 86400, input=999), # out
        ],
        mtime=_FAKE_NOW - 60,
    )
    s, l = account_load_dual("alpha", WINDOW_5H, WINDOW_7D,
                             now=_FAKE_NOW, layout=layout)
    assert s == pytest.approx(10.0)
    assert l == pytest.approx(110.0)


# ─── pick_account ────────────────────────────────────────────────────────


def test_pick_account_lowest_score_wins(tmp_path):
    vault, layout = _vault_with(
        tmp_path, [("alpha", 1.0), ("beta", 1.0)], default="alpha",
    )
    _make_creds(layout, "alpha"); _make_creds(layout, "beta")
    # alpha heavier than beta
    _write_jsonl(
        layout.projects_dir("alpha") / "h" / "s.jsonl",
        [_assistant(_FAKE_NOW - 100, input=1_000_000)],
        mtime=_FAKE_NOW - 100,
    )
    assert pick_account(vault, layout=layout, now=_FAKE_NOW) == "beta"


def test_pick_account_capacity_flips_winner(tmp_path):
    vault, layout = _vault_with(
        tmp_path, [("big", 20.0), ("small", 1.0)], default="big",
    )
    _make_creds(layout, "big"); _make_creds(layout, "small")
    for name in ("big", "small"):
        _write_jsonl(
            layout.projects_dir(name) / "h" / "s.jsonl",
            [_assistant(_FAKE_NOW - 100, input=100_000)],
            mtime=_FAKE_NOW - 100,
        )
    # util(big) = 100000/20 = 5000; util(small) = 100000/1 = 100000 → big wins
    assert pick_account(vault, layout=layout, now=_FAKE_NOW) == "big"


def test_pick_account_single_account_returns_none(tmp_path):
    vault, layout = _vault_with(tmp_path, [("solo", 1.0)], default="solo")
    _make_creds(layout, "solo")
    assert pick_account(vault, layout=layout, now=_FAKE_NOW) is None


def test_pick_account_uninitialized_vault_returns_none(tmp_path):
    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    assert pick_account(vault, layout=layout, now=_FAKE_NOW) is None


def test_pick_account_skips_account_without_credentials(tmp_path):
    vault, layout = _vault_with(
        tmp_path, [("creds", 1.0), ("nocreds", 1.0)], default="creds",
    )
    _make_creds(layout, "creds")  # nocreds intentionally has none
    with patch(
        "gits.core.account_load._macos_keychain_entry_exists",
        return_value=False,
    ):
        assert pick_account(vault, layout=layout, now=_FAKE_NOW) == "creds"


def test_pick_account_default_can_use_keychain_when_native_missing(tmp_path):
    vault, layout = _vault_with(
        tmp_path, [("def", 1.0), ("other", 1.0)], default="def",
    )
    # 'def' has neither isolated nor native credential file present.
    _make_creds(layout, "other")
    with patch(
        "gits.core.account_load._macos_keychain_entry_exists",
        return_value=True,
    ):
        pick = pick_account(vault, layout=layout, now=_FAKE_NOW)
    # Both launchable, equal scores+bindings+last_used → name asc → "def"
    assert pick == "def"


def test_pick_account_isolated_keychain_only_passes_gate(tmp_path):
    """Regression: an isolated (non-default) account whose token lives ONLY
    in the macOS keychain — no `.credentials.json` file at all — must pass
    the credential gate. The previous version checked the keychain only for
    the default account and only under the no-suffix service, so isolated
    keychain-only accounts (the common case on real hosts) were wrongly
    skipped and the picker could never balance off the default.

    Setup makes 'iso' the only correct choice:
      - 'def' has a credential file AND heavy load.
      - 'iso' has NO file, only a keychain entry under the suffix-keyed
        service `Claude Code-credentials-<sha256(config_dir)[:8]>`, and
        zero load.
    Pre-fix: 'iso' was skipped (no file, wrong-service keychain check) →
    picker returned 'def'. Post-fix: 'iso' passes the gate → picker
    returns 'iso'.
    """
    import hashlib as _h
    vault, layout = _vault_with(
        tmp_path, [("def", 1.0), ("iso", 1.0)], default="def",
    )
    _make_creds(layout, "def")
    # Load 'def' up so any working gate must pick 'iso'.
    _write_jsonl(
        layout.projects_dir("def") / "h" / "s.jsonl",
        [_assistant(_FAKE_NOW - 100, input=1_000_000)],
        mtime=_FAKE_NOW - 100,
    )
    expected_suffix = _h.sha256(
        str(layout.account_dir("iso")).encode()
    ).hexdigest()[:8]
    expected_iso_service = f"Claude Code-credentials-{expected_suffix}"

    queried: list[str] = []

    def _fake_exists(service: str) -> bool:
        queried.append(service)
        # Only the suffix-keyed service for 'iso' has an entry. The
        # no-suffix `Claude Code-credentials` is NOT present (matches
        # the real sharon/sharon-team setup the operator described).
        return service == expected_iso_service

    with patch(
        "gits.core.account_load._macos_keychain_entry_exists",
        side_effect=_fake_exists,
    ):
        pick = pick_account(vault, layout=layout, now=_FAKE_NOW)

    assert expected_iso_service in queried, (
        f"never checked the suffix-keyed service; queried={queried!r}"
    )
    assert pick == "iso", (
        f"expected 'iso' (keychain-only, zero load); got {pick!r}. "
        f"This is the PR #4 regression — credential gate dropped the "
        f"keychain-only isolated account and the picker fell back to "
        f"the loaded default."
    )


def test_pick_account_tiebreak_by_bindings(tmp_path):
    vault, layout = _vault_with(
        tmp_path, [("a", 1.0), ("b", 1.0)], default="a",
    )
    _make_creds(layout, "a"); _make_creds(layout, "b")
    pick = pick_account(
        vault, layout=layout, now=_FAKE_NOW,
        live_binding_counts={"a": 2, "b": 0},
    )
    assert pick == "b"


# ─── rank_accounts ───────────────────────────────────────────────────────


def test_rank_accounts_full_table_shape(tmp_path):
    vault, layout = _vault_with(
        tmp_path, [("alpha", 1.0), ("beta", 2.0)], default="alpha",
    )
    _make_creds(layout, "alpha"); _make_creds(layout, "beta")
    # alpha heavier than beta → beta should rank #1
    _write_jsonl(
        layout.projects_dir("alpha") / "h" / "s.jsonl",
        [_assistant(_FAKE_NOW - 100, input=1_000_000)],
        mtime=_FAKE_NOW - 100,
    )
    ranked = rank_accounts(vault, layout=layout, now=_FAKE_NOW)

    assert [r.name for r in ranked] == ["beta", "alpha"]
    assert all(isinstance(r, AccountRank) for r in ranked)
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2
    # weight surfaced from the AccountEntry
    assert ranked[0].weight == 2.0
    assert ranked[1].weight == 1.0
    # score = (load_5h + load_7d) / weight — alpha must be much higher
    assert ranked[1].score > ranked[0].score


def test_rank_accounts_credential_gated_row_has_no_rank(tmp_path):
    vault, layout = _vault_with(
        tmp_path, [("creds", 1.0), ("nocreds", 1.0)], default="creds",
    )
    _make_creds(layout, "creds")  # nocreds intentionally has none
    with patch(
        "gits.core.account_load._macos_keychain_entry_exists",
        return_value=False,
    ):
        ranked = rank_accounts(vault, layout=layout, now=_FAKE_NOW)

    by_name = {r.name: r for r in ranked}
    assert by_name["creds"].rank == 1
    assert by_name["nocreds"].rank is None
    # gated row still carries load/score numbers so the UI can show them
    assert by_name["nocreds"].load_5h == pytest.approx(0.0)


def test_rank_accounts_single_account_returns_one_row(tmp_path):
    vault, layout = _vault_with(tmp_path, [("solo", 1.0)], default="solo")
    _make_creds(layout, "solo")
    ranked = rank_accounts(vault, layout=layout, now=_FAKE_NOW)

    assert len(ranked) == 1
    assert ranked[0].name == "solo"
    assert ranked[0].rank == 1
    # pick_account preserves the legacy ≤1-account contract — None.
    assert pick_account(vault, layout=layout, now=_FAKE_NOW) is None


def test_rank_accounts_uninitialized_vault_returns_empty(tmp_path):
    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    assert rank_accounts(vault, layout=layout, now=_FAKE_NOW) == []


def test_pick_account_matches_rank_one(tmp_path):
    """Round-trip — the dispatcher and the CLI table walk the same code path.

    Acceptance criterion from task [[4h3v7j]]: ``--account=auto`` must
    pick the same account ``ghost account list`` shows as ``#1``.
    """
    vault, layout = _vault_with(
        tmp_path, [("a", 1.0), ("b", 1.0), ("c", 1.0)], default="a",
    )
    for name in ("a", "b", "c"):
        _make_creds(layout, name)
    _write_jsonl(
        layout.projects_dir("a") / "h" / "s.jsonl",
        [_assistant(_FAKE_NOW - 100, input=500_000)],
        mtime=_FAKE_NOW - 100,
    )
    _write_jsonl(
        layout.projects_dir("b") / "h" / "s.jsonl",
        [_assistant(_FAKE_NOW - 100, input=2_000_000)],
        mtime=_FAKE_NOW - 100,
    )
    ranked = rank_accounts(vault, layout=layout, now=_FAKE_NOW)
    rank_one = next(r for r in ranked if r.rank == 1)
    assert pick_account(vault, layout=layout, now=_FAKE_NOW) == rank_one.name
