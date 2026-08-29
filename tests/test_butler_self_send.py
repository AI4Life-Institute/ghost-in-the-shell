"""Publishing into the caller's own bound channel is refused at the REST
chokepoint.

A bound channel already carries everything the session says — the gateway
tails the CLI transcript and posts each assistant text there. Publishing the
same words again puts a second copy in the channel, and the butler-prefixed
copy is forwarded back into the session's own pane, where it reads as a fresh
instruction.

The guard lives in :func:`gits.butler.http.api`, not on a verb, because
``ghost butler send`` and ``ghost discord message send`` both reach Discord
through it — guarding one verb only teaches the caller to use the other.
These tests pin both verbs, the routes that must stay untouched, and every
fail-open path.
"""

from __future__ import annotations

import argparse
import json
import subprocess

import pytest

from gits.butler import butler_cli, discord_cli, http

OWN = "1541713284296871956"
OTHER = "1541714141889171546"
THREAD = "1541723658869805058"


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, str, dict]]:
    """Capture what would have gone over the wire.

    Only the transport is stubbed — ``http.api`` itself runs for real, so
    these tests exercise the guard exactly where it is wired in.
    """
    log: list[tuple[str, str, dict]] = []

    class _Resp:
        status = 200

        def read(self):
            return b'{"id": "msg-1"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, *a, **kw):
        body = json.loads(req.data.decode()) if req.data else {}
        log.append((req.full_url.replace(http.API, ""), req.get_method(), body))
        return _Resp()

    monkeypatch.setattr(http, "load_token", lambda: "token")
    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    return log


def _bound_to(monkeypatch, channel_id: str | None):
    """Pretend the calling pane is the session bound to *channel_id*."""
    monkeypatch.setattr(http, "self_bound_channel", lambda: channel_id)


def _butler_send(target=OWN, content="hello"):
    return argparse.Namespace(
        target_id=target, content=content, prefix=None, user="weiliu", raw=False,
    )


def _discord_send(channel=OWN, content="hello"):
    return argparse.Namespace(target_id=channel, content=content, reply_to=None)


class TestRefusal:
    def test_butler_send_to_own_channel_is_refused(self, monkeypatch, sent):
        _bound_to(monkeypatch, OWN)

        with pytest.raises(SystemExit) as e:
            butler_cli.cmd_send(_butler_send())

        assert sent == []  # the whole point: no second copy in the channel
        msg = str(e.value)
        assert "own bound channel" in msg
        assert OWN in msg
        # The refusal must state the fact that makes re-sending pointless,
        # and must not hand the caller another way to do it.
        assert "already posted there" in msg
        assert "--force" not in msg

    def test_discord_message_send_to_own_channel_is_refused(self, monkeypatch, sent):
        """The workaround the guard exists to close: when `butler send` was
        blocked, the caller reached for the low-level transport verb."""
        _bound_to(monkeypatch, OWN)

        with pytest.raises(SystemExit):
            discord_cli.cmd_message_send(_discord_send())
        assert sent == []

    def test_no_force_escape_hatch_on_the_parser(self):
        """A discoverable override is not a guard: the first version offered
        `--force` in its error text and the caller simply started passing it."""
        parser = argparse.ArgumentParser()
        butler_cli.install_parser(parser.add_subparsers())
        with pytest.raises(SystemExit):
            parser.parse_args(["butler", "send", OWN, "hi", "--force"])

    def test_decorated_prose_is_refused(self, monkeypatch, sent):
        """By the time a dispatch reaches the chokepoint the body already
        carries the butler prefix — stripping it must not make prose look
        like a command."""
        _bound_to(monkeypatch, OWN)

        with pytest.raises(SystemExit):
            http.api(
                f"/channels/{OWN}/messages", method="POST",
                body={"content": "📨 **[butler:weiliu]** 进展汇报"},
            )
        assert sent == []


class TestStillAllowed:
    def test_other_channel_passes(self, monkeypatch, sent):
        _bound_to(monkeypatch, OWN)
        butler_cli.cmd_send(_butler_send(target=OTHER))
        assert [p for p, _, _ in sent] == [f"/channels/{OTHER}/messages"]

    def test_thread_passes(self, monkeypatch, sent):
        """Dispatch reporting into a task thread must keep working — a thread
        has its own id, so it is never the caller's bound channel."""
        _bound_to(monkeypatch, OWN)
        butler_cli.cmd_send(_butler_send(target=THREAD))
        assert [p for p, _, _ in sent] == [f"/channels/{THREAD}/messages"]

    def test_unbound_caller_passes(self, monkeypatch, sent):
        """A cron, a plain shell, a dispatch worker — no binding, no guard."""
        _bound_to(monkeypatch, None)
        butler_cli.cmd_send(_butler_send())
        assert [p for p, _, _ in sent] == [f"/channels/{OWN}/messages"]

    def test_slash_payload_passes(self, monkeypatch, sent):
        """`/bind` and friends are control messages the gateway routes through
        `_handle_butler_command` (org-schema.md:195 sends `/bind` this way),
        not prose the transcript relay would have duplicated."""
        _bound_to(monkeypatch, OWN)
        butler_cli.cmd_send(_butler_send(content="/bind /src/x claude"))
        assert len(sent) == 1

    def test_decorated_slash_payload_passes(self, monkeypatch, sent):
        _bound_to(monkeypatch, OWN)
        http.api(
            f"/channels/{OWN}/messages", method="POST",
            body={"content": "📨 **[butler:weiliu]** /bind /src/x claude"},
        )
        assert len(sent) == 1

    def test_other_routes_on_own_channel_pass(self, monkeypatch, sent):
        """Only message *publishing* is guarded. Reading the channel, creating
        a thread in it, reacting — all still work."""
        _bound_to(monkeypatch, OWN)
        http.api(f"/channels/{OWN}/messages", query={"limit": 5})
        http.api(f"/channels/{OWN}/threads", method="POST", body={"name": "t"})
        http.api(f"/channels/{OWN}/messages/9/reactions/x/@me", method="PUT")
        assert len(sent) == 3

    def test_empty_body_passes(self, monkeypatch, sent):
        _bound_to(monkeypatch, OWN)
        http.api(f"/channels/{OWN}/messages", method="POST", body={"embeds": []})
        assert len(sent) == 1


class TestSelfBoundChannelLookup:
    """`self_bound_channel` resolves pane → window → binding, and fails open
    on every error: a broken lookup must never block a report."""

    def _tmux(self, monkeypatch, rc=0, out="@260\n"):
        monkeypatch.setattr(
            http.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, rc, out, ""),
        )

    def _state(self, tmp_path, monkeypatch, bindings):
        f = tmp_path / "state.json"
        f.write_text(json.dumps({"bindings": bindings}))
        monkeypatch.setattr(http, "STATE", str(f))

    def test_resolves_binding_for_this_pane(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TMUX_PANE", "%260")
        self._tmux(monkeypatch)
        self._state(tmp_path, monkeypatch, {
            OTHER: {"window_id": "@1"},
            OWN: {"window_id": "@260"},
        })
        assert http.self_bound_channel() == OWN

    def test_no_tmux_pane_fails_open(self, monkeypatch):
        monkeypatch.delenv("TMUX_PANE", raising=False)
        assert http.self_bound_channel() is None

    def test_tmux_failure_fails_open(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TMUX_PANE", "%260")
        self._tmux(monkeypatch, rc=1, out="")
        self._state(tmp_path, monkeypatch, {OWN: {"window_id": "@260"}})
        assert http.self_bound_channel() is None

    def test_tmux_missing_fails_open(self, monkeypatch):
        monkeypatch.setenv("TMUX_PANE", "%260")

        def boom(*a, **kw):
            raise OSError("tmux not found")

        monkeypatch.setattr(http.subprocess, "run", boom)
        assert http.self_bound_channel() is None

    def test_unreadable_state_fails_open(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TMUX_PANE", "%260")
        self._tmux(monkeypatch)
        monkeypatch.setattr(http, "STATE", str(tmp_path / "missing.json"))
        assert http.self_bound_channel() is None

    def test_corrupt_state_fails_open(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TMUX_PANE", "%260")
        self._tmux(monkeypatch)
        f = tmp_path / "state.json"
        f.write_text("{not json")
        monkeypatch.setattr(http, "STATE", str(f))
        assert http.self_bound_channel() is None

    def test_stale_binding_on_a_reused_window_id_loses(self, monkeypatch, tmp_path):
        """tmux reuses window ids: several dead bindings can carry `@8` while
        the live session also sits there. Taking the first dict match resolved
        to a dead binding and let a real self-send through — the live session
        is the most recently active one."""
        monkeypatch.setenv("TMUX_PANE", "%8")
        self._tmux(monkeypatch, out="@8\n")
        self._state(tmp_path, monkeypatch, {
            "1483720907347460096": {"window_id": "@8", "last_active_at": 1775082472.4},
            "1505335695252914236": {"window_id": "@8", "last_active_at": 1781549142.9},
            OWN: {"window_id": "@8", "last_active_at": 1788032728.0},
        })
        assert http.self_bound_channel() == OWN

    def test_binding_without_last_active_still_resolves(self, monkeypatch, tmp_path):
        """A lone binding missing the field must still be found."""
        monkeypatch.setenv("TMUX_PANE", "%8")
        self._tmux(monkeypatch, out="@8\n")
        self._state(tmp_path, monkeypatch, {OWN: {"window_id": "@8"}})
        assert http.self_bound_channel() == OWN

    def test_pane_with_no_binding_fails_open(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TMUX_PANE", "%9")
        self._tmux(monkeypatch, out="@9\n")
        self._state(tmp_path, monkeypatch, {OWN: {"window_id": "@260"}})
        assert http.self_bound_channel() is None
