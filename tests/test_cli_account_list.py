"""Smoke tests for ``gits account list`` output.

Verifies the rewritten list command (task [[4h3v7j]]):

* Columns are the load-balanced ranking columns
  (``name·tier·weight·5h load·7d load·score·pick?·bindings``),
  not the old OAuth Usage API columns.
* Number formatting matches the K/M/B contract.
* ``#1 ←`` lands on the lowest-score row.
* Credential-gated accounts surface as ``—`` with the footer note.
* No network calls — operator's ban on programmatic OAuth Usage queries.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gits.cli_account import _human_count, cmd_list
from gits.core.account import AccountLayout
from gits.core.account_vault import AccountEntry, AccountVault, Manifest


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


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Pin HOME and state_dir so AccountLayout() / Settings() land in tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GITS_STATE_DIR", str(tmp_path / ".gits"))
    yield tmp_path


# ─── _human_count ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "n, expected",
    [
        (0, "0"),
        (1, "1"),
        (999, "999"),
        (1000, "1K"),
        (947_000, "947K"),
        (1_500_000, "1.5M"),
        (322_000_000, "322M"),
        (1_600_000_000, "1.6B"),
        (12_000_000_000, "12B"),
    ],
)
def test_human_count_formatting(n, expected):
    assert _human_count(n) == expected


# ─── cmd_list output ─────────────────────────────────────────────────────


def _build_vault(tmp_path, *, accounts, default):
    layout = AccountLayout(home=tmp_path)
    vault = AccountVault(tmp_path / ".gits", layout=layout)
    m = Manifest(default=default)
    for name, weight, tier in accounts:
        m.accounts.append(AccountEntry(
            name=name, weight=weight, subscription_type=tier,
            config_dir=str(layout.account_dir(name)),
        ))
    vault.save(m)
    return vault, layout


def test_cmd_list_renders_new_columns(fake_home, capsys):
    vault, layout = _build_vault(
        fake_home,
        accounts=[("alpha", 20.0, "max"), ("beta", 6.0, "max")],
        default="alpha",
    )
    _make_creds(layout, "alpha")
    _make_creds(layout, "beta")

    # cmd_list doesn't accept a `now=` override — anchor JSONL events at
    # real wallclock so the mtime-prefilter in account_load doesn't skip
    # the file. Use a recent (5-minute-ago) timestamp.
    now = time.time()
    recent = now - 300

    # alpha heavier than beta → beta should rank #1. alpha is the default →
    # runs native, so its load lives in ~/.claude/projects (projects_dir(None)).
    _write_jsonl(
        layout.projects_dir(None) / "h" / "s.jsonl",
        [_assistant_event(recent, 100_000_000)],
        mtime=recent,
    )
    _write_jsonl(
        layout.projects_dir("beta") / "h" / "s.jsonl",
        [_assistant_event(recent, 1_000)],
        mtime=recent,
    )

    cmd_list(argparse.Namespace())
    out = capsys.readouterr().out

    # Header — new columns, no OAuth-era columns
    assert "5h load" in out
    assert "7d load" in out
    assert "score" in out
    assert "pick?" in out
    assert "email" not in out          # operator: drop the email column
    assert "resets" not in out          # operator: drop resets entirely
    assert "%" not in out               # no more 5h% / 7d%

    # Rank arrow lands on beta (lower load) — and *not* on alpha
    beta_line = next(ln for ln in out.splitlines() if "beta" in ln)
    alpha_line = next(ln for ln in out.splitlines() if "alpha" in ln)
    assert "#1 ←" in beta_line
    assert "#1 ←" not in alpha_line
    assert "#2" in alpha_line

    # Default-account marker (*) still works
    assert alpha_line.startswith("*")
    assert beta_line.startswith(" ")


def test_cmd_list_credential_gated_shows_dash_and_footer(fake_home, capsys):
    vault, layout = _build_vault(
        fake_home,
        accounts=[("creds", 1.0, "max"), ("nocreds", 1.0, "max")],
        default="creds",
    )
    _make_creds(layout, "creds")  # nocreds intentionally missing

    with patch(
        "gits.core.account_load._macos_keychain_entry_exists",
        return_value=False,
    ):
        cmd_list(argparse.Namespace())
    out = capsys.readouterr().out

    nocreds_line = next(ln for ln in out.splitlines() if "nocreds" in ln)
    assert "—" in nocreds_line          # gated row's pick? column
    assert "[note]" in out               # footer note explaining `—`
    # Eligible account still ranks
    creds_line = next(
        ln for ln in out.splitlines() if "creds" in ln and "nocreds" not in ln
    )
    assert "#1 ←" in creds_line


def test_cmd_list_no_network_calls(fake_home, capsys):
    """Acceptance: `ghost account list` must run offline. Stub socket to prove it."""
    vault, layout = _build_vault(
        fake_home,
        accounts=[("alpha", 1.0, "max"), ("beta", 1.0, "max")],
        default="alpha",
    )
    _make_creds(layout, "alpha")
    _make_creds(layout, "beta")

    import socket as _socket

    def _blocked(*a, **kw):
        raise AssertionError(
            f"`gits account list` opened a socket — must run offline. args={a!r}"
        )

    with patch.object(_socket, "socket", _blocked):
        cmd_list(argparse.Namespace())

    assert "5h load" in capsys.readouterr().out


# ─── benched marker (task [[5wuazc]]) ────────────────────────────────────


def test_cmd_list_benched_shows_marker_and_detail(fake_home, capsys):
    vault, layout = _build_vault(
        fake_home,
        accounts=[("alpha", 1.0, "max"), ("beta", 1.0, "max")],
        default="alpha",
    )
    _make_creds(layout, "alpha")
    _make_creds(layout, "beta")
    vault.set_bench("beta", "2030-06-08T00:00:00+00:00")

    cmd_list(argparse.Namespace())
    out = capsys.readouterr().out

    beta_line = next(ln for ln in out.splitlines() if ln.lstrip().startswith("beta"))
    assert "⛔" in beta_line
    # Detail footer: greppable as "bench" (acceptance test contract) with
    # local-time expiry + the unbench hint.
    assert "benched until 2030-06-0" in out
    assert "unbench beta" in out
    # The eligible account still ranks.
    alpha_line = next(ln for ln in out.splitlines() if "alpha" in ln)
    assert "#1 ←" in alpha_line


def test_cmd_list_expired_bench_shows_no_marker(fake_home, capsys):
    """Lazy expiry: a past benchedUntil renders nothing bench-related at all.

    The acceptance script greps the whole output for "bench" after expiry,
    so neither the marker nor any static text may contain the word.
    """
    vault, layout = _build_vault(
        fake_home,
        accounts=[("alpha", 1.0, "max"), ("beta", 1.0, "max")],
        default="alpha",
    )
    _make_creds(layout, "alpha")
    _make_creds(layout, "beta")
    vault.set_bench("beta", "2020-01-01T00:00:00+00:00")  # long past

    cmd_list(argparse.Namespace())
    out = capsys.readouterr().out

    assert "⛔" not in out
    assert "bench" not in out.lower()
    beta_line = next(ln for ln in out.splitlines() if ln.lstrip().startswith("beta"))
    assert "#" in beta_line  # ranks normally


def test_cmd_list_indefinite_bench_says_indefinitely(fake_home, capsys):
    vault, layout = _build_vault(
        fake_home,
        accounts=[("alpha", 1.0, "max"), ("beta", 1.0, "max")],
        default="alpha",
    )
    _make_creds(layout, "alpha")
    _make_creds(layout, "beta")
    from gits.core.account_vault import BENCH_FOREVER
    vault.set_bench("beta", BENCH_FOREVER)

    cmd_list(argparse.Namespace())
    out = capsys.readouterr().out
    assert "benched indefinitely" in out
