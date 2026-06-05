"""Tests for ``gits account bench / unbench`` (task [[5wuazc]]).

CLI-level coverage of the bench verbs: ``--for``/``--until`` parsing
(good + bad), indefinite default, friendly unbench no-op, the
last-launchable-account warning, and the warn-but-proceed override
helpers used by ``account switch`` / ``dispatch --account``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
from unittest.mock import patch

import pytest

from gits.cli_account import (
    _parse_bench_for,
    _parse_bench_until,
    cmd_bench,
    cmd_unbench,
)
from gits.core.account import AccountLayout
from gits.core.account_load import bench_warning
from gits.core.account_vault import (
    BENCH_FOREVER,
    AccountEntry,
    AccountVault,
    Manifest,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Pin HOME and state_dir so AccountLayout() / Settings() land in tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GITS_STATE_DIR", str(tmp_path / ".gits"))
    yield tmp_path


def _make_creds(layout: AccountLayout, name: str) -> None:
    p = layout.credentials_file(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"claudeAiOauth": {"accessToken": "x"}}')
    p.chmod(0o600)


def _build_vault(tmp_path, names, default):
    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    m = Manifest(default=default)
    for name in names:
        m.accounts.append(AccountEntry(
            name=name, weight=1.0, config_dir=str(layout.account_dir(name)),
        ))
    vault.save(m)
    return vault, layout


def _ns(**kw) -> argparse.Namespace:
    base = {"until": None, "for_": None}
    base.update(kw)
    return argparse.Namespace(**base)


# ─── --for parsing ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec, seconds",
    [("3d", 3 * 86400), ("12h", 12 * 3600), ("45m", 45 * 60), ("1m", 60)],
)
def test_parse_bench_for_valid(spec, seconds):
    assert _parse_bench_for(spec).total_seconds() == seconds


@pytest.mark.parametrize("spec", ["", "3", "d", "3w", "1.5h", "-2d", "0m", "3 d", "3dd"])
def test_parse_bench_for_invalid(spec):
    with pytest.raises(ValueError):
        _parse_bench_for(spec)


# ─── --until parsing ─────────────────────────────────────────────────────


def test_parse_bench_until_valid_local_offset_aware():
    dt = _parse_bench_until("2030-06-08 00:00")
    assert dt.tzinfo is not None  # offset-aware (local zone attached)
    # Round-trips through the local zone: same wall-clock fields.
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2030, 6, 8, 0, 0)


@pytest.mark.parametrize(
    "spec", ["not-a-date", "2030-06-08", "00:00", "2030/06/08 00:00", "2030-06-08T00:00:00Z"],
)
def test_parse_bench_until_invalid(spec):
    with pytest.raises(ValueError):
        _parse_bench_until(spec)


# ─── cmd_bench / cmd_unbench ─────────────────────────────────────────────


def test_cmd_bench_no_flag_is_indefinite(fake_home, capsys):
    vault, layout = _build_vault(fake_home, ["a", "b"], default="a")
    _make_creds(layout, "a"); _make_creds(layout, "b")
    cmd_bench(_ns(name="b"))
    assert vault.get("b").benched_until == BENCH_FOREVER
    assert "benched indefinitely" in capsys.readouterr().out


def test_cmd_bench_for_stores_absolute_offset_aware(fake_home):
    vault, layout = _build_vault(fake_home, ["a", "b"], default="a")
    _make_creds(layout, "a"); _make_creds(layout, "b")
    before = _dt.datetime.now().astimezone()
    cmd_bench(_ns(name="b", for_="3d"))
    stored = vault.get("b").benched_until
    parsed = _dt.datetime.fromisoformat(stored)
    assert parsed.tzinfo is not None
    delta = parsed - before
    assert _dt.timedelta(days=3) - _dt.timedelta(minutes=1) < delta <= _dt.timedelta(days=3, minutes=1)


def test_cmd_bench_until_stores_instant(fake_home):
    vault, layout = _build_vault(fake_home, ["a", "b"], default="a")
    _make_creds(layout, "a"); _make_creds(layout, "b")
    cmd_bench(_ns(name="b", until="2030-06-08 00:00"))
    parsed = _dt.datetime.fromisoformat(vault.get("b").benched_until)
    local = parsed.astimezone()
    assert (local.year, local.month, local.day, local.hour) == (2030, 6, 8, 0)


def test_cmd_bench_bad_until_exits_nonzero(fake_home, capsys):
    _build_vault(fake_home, ["a", "b"], default="a")
    with pytest.raises(SystemExit) as exc:
        cmd_bench(_ns(name="b", until="not-a-date"))
    assert exc.value.code != 0
    assert "invalid --until" in capsys.readouterr().err


def test_cmd_bench_bad_for_exits_nonzero(fake_home, capsys):
    _build_vault(fake_home, ["a", "b"], default="a")
    with pytest.raises(SystemExit) as exc:
        cmd_bench(_ns(name="b", for_="3w"))
    assert exc.value.code != 0
    assert "invalid --for" in capsys.readouterr().err


def test_cmd_bench_unknown_account_exits_nonzero(fake_home, capsys):
    _build_vault(fake_home, ["a"], default="a")
    with pytest.raises(SystemExit):
        cmd_bench(_ns(name="ghost"))
    assert "no such account" in capsys.readouterr().err


def test_cmd_bench_rebench_updates_expiry(fake_home):
    vault, layout = _build_vault(fake_home, ["a", "b"], default="a")
    _make_creds(layout, "a"); _make_creds(layout, "b")
    cmd_bench(_ns(name="b"))
    cmd_bench(_ns(name="b", until="2030-06-08 00:00"))  # no error
    assert vault.get("b").benched_until != BENCH_FOREVER


def test_cmd_bench_last_launchable_warns_but_allows(fake_home, capsys):
    vault, layout = _build_vault(fake_home, ["a", "b"], default="a")
    _make_creds(layout, "a"); _make_creds(layout, "b")
    with patch(
        "gits.core.account_load._macos_keychain_entry_exists",
        return_value=False,
    ):
        cmd_bench(_ns(name="a"))
        captured_first = capsys.readouterr()
        assert "NO un-benched launchable account" not in captured_first.err
        cmd_bench(_ns(name="b"))  # benches the LAST launchable account
        captured_last = capsys.readouterr()
    assert "NO un-benched launchable account" in captured_last.err
    # …but the bench landed anyway (operator may want everything paused).
    assert vault.get("b").benched_until == BENCH_FOREVER


def test_cmd_unbench_clears(fake_home, capsys):
    vault, layout = _build_vault(fake_home, ["a", "b"], default="a")
    vault.set_bench("b", BENCH_FOREVER)
    cmd_unbench(_ns(name="b"))
    assert vault.get("b").benched_until is None
    assert "unbenched" in capsys.readouterr().out


def test_cmd_unbench_non_benched_is_friendly_noop(fake_home, capsys):
    _build_vault(fake_home, ["a", "b"], default="a")
    cmd_unbench(_ns(name="b"))  # must not raise SystemExit
    assert "was not benched" in capsys.readouterr().out


def test_cmd_unbench_unknown_account_exits_nonzero(fake_home, capsys):
    _build_vault(fake_home, ["a"], default="a")
    with pytest.raises(SystemExit):
        cmd_unbench(_ns(name="ghost"))
    assert "no such account" in capsys.readouterr().err


# ─── warn-but-proceed override helper ────────────────────────────────────


def test_bench_warning_for_benched_account():
    e = AccountEntry(name="x", config_dir="/x", benched_until=BENCH_FOREVER)
    msg = bench_warning(e, now=0.0)
    assert msg is not None
    assert "benched" in msg
    assert "proceeding" in msg


def test_bench_warning_none_when_not_benched_or_expired():
    e = AccountEntry(name="x", config_dir="/x")
    assert bench_warning(e, now=0.0) is None
    e.benched_until = "1990-01-01T00:00:00+00:00"
    assert bench_warning(e, now=_dt.datetime.now(_dt.UTC).timestamp()) is None
