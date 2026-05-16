"""Smoke tests for ``ghost discord`` noun-verb argparse layout (task i1arb2).

These tests exercise the argparser only — every Discord REST call is
mocked via ``api()``. No network. Covers each new noun-verb form, plus
a negative check that the old hyphenated names are gone.
"""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import pytest

from gits.butler import discord_cli


def _build_parser() -> argparse.ArgumentParser:
    """Construct a top-level parser with the discord subparser installed."""
    parser = argparse.ArgumentParser(prog="ghost")
    sub = parser.add_subparsers(dest="command", required=True)
    discord_cli.install_parser(sub)
    return parser


# ---------------------------------------------------------------------------
# parser-only checks — each new verb routes to the right handler with the
# right args (no api() call here; pure argparse).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected_func,expected_attrs",
    [
        (
            ["discord", "thread", "create", "111", "my-thread"],
            discord_cli.cmd_thread_create,
            {"channel_id": "111", "name": "my-thread", "private": False, "auto_archive": 1440},
        ),
        (
            ["discord", "thread", "create", "111", "private-one", "--private", "--auto-archive", "60"],
            discord_cli.cmd_thread_create,
            {"channel_id": "111", "name": "private-one", "private": True, "auto_archive": 60},
        ),
        (
            ["discord", "thread", "archive", "222"],
            discord_cli.cmd_thread_archive,
            {"thread_id": "222"},
        ),
        (
            ["discord", "thread", "read", "333", "--limit", "10", "--json"],
            discord_cli.cmd_thread_read,
            {"thread_id": "333", "limit": 10, "after": None, "json": True},
        ),
        (
            ["discord", "channel", "create", "new-room"],
            discord_cli.cmd_channel_create,
            {"name": "new-room"},
        ),
        (
            ["discord", "channel", "show", "444"],
            discord_cli.cmd_channel_show,
            {"channel_id": "444"},
        ),
        (
            ["discord", "channel", "list", "--guild", "555"],
            discord_cli.cmd_channel_list,
            {"guild_id": "555"},
        ),
        (
            ["discord", "message", "send", "666", "hello"],
            discord_cli.cmd_message_send,
            {"target_id": "666", "content": "hello"},
        ),
        (
            ["discord", "whoami"],
            discord_cli.cmd_whoami,
            {},
        ),
        (
            ["discord", "setup"],
            discord_cli.cmd_setup,
            {"token": None, "no_start": False},
        ),
    ],
)
def test_new_noun_verb_routes(argv, expected_func, expected_attrs):
    parser = _build_parser()
    args = parser.parse_args(argv)
    assert args.func is expected_func
    for attr, value in expected_attrs.items():
        assert getattr(args, attr) == value, f"{attr}: got {getattr(args, attr)!r}, want {value!r}"


# ---------------------------------------------------------------------------
# Round-trip happy path per verb: parser → handler → mocked api() call.
# Asserts the handler invokes Discord REST with the correct path + body.
# ---------------------------------------------------------------------------


def _run(argv, api_return):
    """Parse argv, call the resolved handler with api() patched.

    Returns (api_calls, stdout, stderr) — api_calls is the recorded list of
    (path, method, body, query) tuples observed by the mock.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    calls = []

    def fake_api(path, method="GET", body=None, query=None):
        calls.append((path, method, body, query))
        return api_return

    out, err = io.StringIO(), io.StringIO()
    with patch.object(discord_cli, "api", side_effect=fake_api), \
         redirect_stdout(out), redirect_stderr(err):
        args.func(args)
    return calls, out.getvalue(), err.getvalue()


def test_thread_create_hits_threads_endpoint():
    calls, stdout, _ = _run(
        ["discord", "thread", "create", "111", "my-thread"],
        (201, {"id": "t-1", "name": "my-thread"}),
    )
    assert calls == [(
        "/channels/111/threads",
        "POST",
        {"name": "my-thread", "type": 11, "auto_archive_duration": 1440},
        None,
    )]
    assert "t-1" in stdout


def test_thread_create_private_flag_flips_type():
    calls, _, _ = _run(
        ["discord", "thread", "create", "111", "p", "--private"],
        (201, {"id": "t-2", "name": "p"}),
    )
    assert calls[0][2]["type"] == 12  # private


def test_thread_archive_patches_with_archived_true():
    calls, stdout, _ = _run(
        ["discord", "thread", "archive", "222"],
        (200, {"id": "222", "name": "old"}),
    )
    assert calls == [(
        "/channels/222",
        "PATCH",
        {"archived": True, "locked": False},
        None,
    )]
    assert "archived: 222" in stdout


def test_thread_read_lists_messages_in_chronological_order():
    # api returns newest-first; handler should flip to oldest-first.
    calls, stdout, _ = _run(
        ["discord", "thread", "read", "333"],
        (200, [
            {"id": "m2", "author": {"username": "b"}, "timestamp": "2026-05-16T01:00:00", "content": "second"},
            {"id": "m1", "author": {"username": "a"}, "timestamp": "2026-05-16T00:00:00", "content": "first"},
        ]),
    )
    assert calls[0][0] == "/channels/333/messages"
    assert calls[0][3] == {"limit": 50, "after": None}
    # 'first' must appear before 'second' in printed output.
    assert stdout.index("first") < stdout.index("second")


def test_channel_show_dumps_json_body():
    calls, stdout, _ = _run(
        ["discord", "channel", "show", "444"],
        (200, {"id": "444", "name": "general"}),
    )
    assert calls == [("/channels/444", "GET", None, None)]
    assert '"general"' in stdout


def test_channel_list_hits_guild_channels_endpoint():
    calls, stdout, _ = _run(
        ["discord", "channel", "list", "--guild", "555"],
        (200, [
            {"id": "c1", "type": 0, "name": "general", "parent_id": None, "position": 0},
        ]),
    )
    assert calls == [("/guilds/555/channels", "GET", None, None)]
    assert "general" in stdout


def test_message_send_posts_to_target():
    calls, stdout, _ = _run(
        ["discord", "message", "send", "666", "hi"],
        (201, {"id": "msg-1"}),
    )
    assert calls == [(
        "/channels/666/messages",
        "POST",
        {"content": "hi"},
        None,
    )]
    assert "msg-1" in stdout


def test_channel_create_reads_onboarding_then_posts():
    parser = _build_parser()
    args = parser.parse_args(["discord", "channel", "create", "room-x"])
    api_calls = []

    def fake_api(path, method="GET", body=None, query=None):
        api_calls.append((path, method, body, query))
        return (201, {"id": "c-new", "name": "room-x"})

    with patch.object(discord_cli, "api", side_effect=fake_api), \
         patch.object(
             discord_cli.onboarding, "require",
             return_value={"guild_id": "G", "category_id": "CAT", "category_name": "cat"},
         ), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        args.func(args)

    assert api_calls == [(
        "/guilds/G/channels",
        "POST",
        {"name": "room-x", "type": 0, "parent_id": "CAT"},
        None,
    )]


def test_whoami_prints_identity_fields():
    _, stdout, _ = _run(
        ["discord", "whoami"],
        (200, {"id": "bot-1", "username": "ghost", "discriminator": "0001"}),
    )
    assert "id: bot-1" in stdout
    assert "username: ghost" in stdout


# ---------------------------------------------------------------------------
# Negative: old hyphenated verb names must be gone (hard cut, no aliases).
# argparse exits with code 2 on unrecognized choices.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["discord", "create-thread", "111", "x"],
        ["discord", "archive-thread", "222"],
        ["discord", "read-thread", "333"],
        ["discord", "create-channel", "n"],
        ["discord", "guild-channels", "555"],
        # Old bare `send` (now `message send`) — bare `send` is no longer a verb.
        ["discord", "send", "666", "hi"],
        # Old bare `channel <id>` (now `channel show <id>`) — bare positional
        # after `channel` must be rejected because the next position now
        # requires a verb name (create/show/list).
        ["discord", "channel", "444"],
    ],
)
def test_old_verb_forms_rejected(argv, capsys):
    parser = _build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(argv)
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    # argparse error language: "invalid choice".
    assert "invalid choice" in captured.err


# ---------------------------------------------------------------------------
# Missing required pieces produce argparse errors (not Python errors).
# ---------------------------------------------------------------------------


def test_channel_list_requires_guild_flag(capsys):
    parser = _build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["discord", "channel", "list"])
    assert excinfo.value.code == 2
    assert "--guild" in capsys.readouterr().err


def test_noun_without_verb_errors(capsys):
    parser = _build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["discord", "thread"])
    assert excinfo.value.code == 2
