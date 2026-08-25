"""``ghost butler send`` must refuse the caller's own bound channel.

A bound channel already carries everything the session says — the gateway
tails the CLI transcript and posts each assistant text there. A ``butler
send`` to that same channel publishes the words a second time and feeds a
butler-prefixed copy back into the session's own pane, where it reads as a
fresh instruction. These tests pin the refusal, and pin every fail-open path
so a broken lookup can never swallow a real report.
"""

from __future__ import annotations

import argparse
import subprocess
from unittest.mock import patch

import pytest

from gits.butler import butler_cli


@pytest.fixture(autouse=True)
def _no_real_http(monkeypatch):
    """Any unmocked REST call must fail loudly rather than reach Discord."""
    def boom(*a, **kw):
        raise RuntimeError(f"unmocked api() call leaked through: {a!r} {kw!r}")

    monkeypatch.setattr(butler_cli, "api", boom)


def _args(target: str | None = "1541713284296871956", content: str = "hello", **over):
    ns = argparse.Namespace(
        target_id=target, content=content, prefix=None, user="weiliu",
        raw=False, force=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _bound_to(monkeypatch, channel_id: str | None):
    """Pretend the calling pane is the session bound to *channel_id*."""
    monkeypatch.setattr(butler_cli, "_self_bound_channel", lambda: channel_id)


def _record_sends(monkeypatch) -> list[tuple[str, str]]:
    """Capture (target, content) instead of sending; returns the log."""
    sent: list[tuple[str, str]] = []

    def fake_send(target_id, content, **kw):
        sent.append((target_id, content))
        return "msg-1"

    monkeypatch.setattr(butler_cli, "send_decorated", fake_send)
    return sent


class TestRefusal:
    def test_own_channel_is_refused_and_nothing_is_sent(self, monkeypatch, capsys):
        sent = _record_sends(monkeypatch)
        _bound_to(monkeypatch, "1541713284296871956")

        with pytest.raises(SystemExit) as e:
            butler_cli.cmd_send(_args(target="1541713284296871956"))

        assert sent == []  # the whole point: no second copy in the channel
        msg = str(e.value)
        assert "own bound channel" in msg
        assert "1541713284296871956" in msg
        assert "--force" in msg  # the escape hatch is discoverable

    def test_default_target_is_guarded_too(self, monkeypatch):
        """Omitting the target resolves to the home channel, which for a PM
        session IS its own bound channel — the guard runs on the *resolved*
        id, not on what the operator typed."""
        sent = _record_sends(monkeypatch)
        monkeypatch.setattr(
            butler_cli, "_resolve_target", lambda t, cwd: "1541713284296871956"
        )
        _bound_to(monkeypatch, "1541713284296871956")

        with pytest.raises(SystemExit):
            butler_cli.cmd_send(_args(target=None))
        assert sent == []

    def test_stdin_content_is_guarded(self, monkeypatch):
        """``send - <<'MSG'`` is the shape that actually caused the duplicate
        posts, so the guard must run after stdin is read."""
        sent = _record_sends(monkeypatch)
        _bound_to(monkeypatch, "1541713284296871956")
        stdin = type("S", (), {"read": staticmethod(lambda: "报告全文")})()
        monkeypatch.setattr("sys.stdin", stdin)

        with pytest.raises(SystemExit):
            butler_cli.cmd_send(_args(target="1541713284296871956", content="-"))
        assert sent == []


class TestStillAllowed:
    def test_other_channel_passes(self, monkeypatch):
        sent = _record_sends(monkeypatch)
        _bound_to(monkeypatch, "1541713284296871956")

        butler_cli.cmd_send(_args(target="1541714141889171546"))
        assert sent == [("1541714141889171546", "hello")]

    def test_thread_of_own_channel_passes(self, monkeypatch):
        """Dispatch reporting into a task thread must keep working — a thread
        has its own id, so it is never the caller's bound channel."""
        sent = _record_sends(monkeypatch)
        _bound_to(monkeypatch, "1541713284296871956")

        butler_cli.cmd_send(_args(target="1541723658869805058"))
        assert sent == [("1541723658869805058", "hello")]

    def test_unbound_caller_passes(self, monkeypatch):
        """A cron, a plain shell, a dispatch worker — no binding, no guard."""
        sent = _record_sends(monkeypatch)
        _bound_to(monkeypatch, None)

        butler_cli.cmd_send(_args(target="1541713284296871956"))
        assert sent == [("1541713284296871956", "hello")]

    def test_slash_payload_passes(self, monkeypatch):
        """``/bind`` and friends are control messages the gateway routes, not
        prose the transcript relay would have duplicated (org-schema.md:195
        onboarding flow sends `/bind` through this verb)."""
        sent = _record_sends(monkeypatch)
        _bound_to(monkeypatch, "1541713284296871956")

        butler_cli.cmd_send(_args(target="1541713284296871956", content="/bind /src/x claude"))
        assert sent == [("1541713284296871956", "/bind /src/x claude")]

    def test_force_overrides(self, monkeypatch):
        sent = _record_sends(monkeypatch)
        _bound_to(monkeypatch, "1541713284296871956")

        butler_cli.cmd_send(_args(target="1541713284296871956", force=True))
        assert sent == [("1541713284296871956", "hello")]


class TestSelfBoundChannelLookup:
    """`_self_bound_channel` resolves pane → window → binding, and fails open
    on every error: a broken lookup must never block a report."""

    def test_resolves_binding_for_this_pane(self, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%260")
        monkeypatch.setattr(
            butler_cli.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, "@260\n", ""),
        )
        binding = type("B", (), {"channel_id": "1541713284296871956"})()
        mgr = type("M", (), {
            "get_binding_by_window": lambda self, w: binding if w == "@260" else None,
        })
        with patch("gits.core.session.SessionManager", lambda *a, **kw: mgr()):
            assert butler_cli._self_bound_channel() == "1541713284296871956"

    def test_no_tmux_pane_fails_open(self, monkeypatch):
        monkeypatch.delenv("TMUX_PANE", raising=False)
        assert butler_cli._self_bound_channel() is None

    def test_tmux_failure_fails_open(self, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%260")
        monkeypatch.setattr(
            butler_cli.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "no server"),
        )
        assert butler_cli._self_bound_channel() is None

    def test_tmux_missing_fails_open(self, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%260")

        def boom(*a, **kw):
            raise OSError("tmux not found")

        monkeypatch.setattr(butler_cli.subprocess, "run", boom)
        assert butler_cli._self_bound_channel() is None

    def test_state_read_failure_fails_open(self, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%260")
        monkeypatch.setattr(
            butler_cli.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, "@260\n", ""),
        )

        def boom(*a, **kw):
            raise RuntimeError("state.json is garbage")

        with patch("gits.core.session.SessionManager", boom):
            assert butler_cli._self_bound_channel() is None

    def test_pane_with_no_binding_fails_open(self, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%9")
        monkeypatch.setattr(
            butler_cli.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, "@9\n", ""),
        )
        mgr = type("M", (), {"get_binding_by_window": lambda self, w: None})
        with patch("gits.core.session.SessionManager", lambda *a, **kw: mgr()):
            assert butler_cli._self_bound_channel() is None
