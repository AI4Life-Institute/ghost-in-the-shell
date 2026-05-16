"""``ghost discord <verb>`` — raw Discord REST primitives.

No butler semantics here (no identity resolution, no prefix decoration,
no home channel defaults). Use ``ghost butler <verb>`` for those.

The one mild layering exception is ``create-channel``, which reads
``gits.butler.onboarding`` for guild_id + parent category — that matches
the prior behavior of ``butler create-channel`` and lets the onboard
skill stay a one-arg call.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import onboarding
from .http import api, die


def cmd_whoami(args: argparse.Namespace) -> None:
    status, body = api("/users/@me")
    if status != 200:
        die(status, body)
    for k in ("id", "username", "discriminator", "bot", "verified"):
        if k in body:
            print(f"{k}: {body[k]}")


def cmd_guild_channels(args: argparse.Namespace) -> None:
    status, body = api(f"/guilds/{args.guild_id}/channels")
    if status != 200:
        die(status, body)
    types = {
        0: "text", 2: "voice", 4: "category", 5: "ann",
        10: "ann-thr", 11: "pub-thr", 12: "priv-thr", 13: "stage", 15: "forum",
    }
    for c in sorted(body, key=lambda x: (x.get("parent_id") or "", x.get("position", 0))):
        t = types.get(c.get("type"), str(c.get("type")))
        print(f"{c['id']:20} [{t:8}] {c['name']:30} parent={c.get('parent_id')}")


def cmd_channel(args: argparse.Namespace) -> None:
    status, body = api(f"/channels/{args.channel_id}")
    if status != 200:
        die(status, body)
    print(json.dumps(body, indent=2, ensure_ascii=False))


def cmd_send(args: argparse.Namespace) -> None:
    """RAW send — no prefix decoration, no home-channel fallback.

    For butler-style decorated sends, use ``ghost butler send``.
    """
    content = args.content
    if content == "-":
        content = sys.stdin.read()
    status, resp = api(
        f"/channels/{args.target_id}/messages",
        method="POST",
        body={"content": content},
    )
    if status not in (200, 201):
        die(status, resp)
    print(resp["id"])
    print(f"# sent to {args.target_id} ({len(content)} chars)", file=sys.stderr)


def cmd_create_thread(args: argparse.Namespace) -> None:
    body = {
        "name": args.name,
        "type": 12 if args.private else 11,  # 11=public, 12=private
        "auto_archive_duration": args.auto_archive,
    }
    status, resp = api(
        f"/channels/{args.channel_id}/threads", method="POST", body=body
    )
    if status not in (200, 201):
        die(status, resp)
    print(resp["id"])
    print(
        f"# created thread '{resp['name']}' in channel {args.channel_id}",
        file=sys.stderr,
    )


def cmd_read_thread(args: argparse.Namespace) -> None:
    status, body = api(
        f"/channels/{args.thread_id}/messages",
        query={"limit": args.limit, "after": args.after},
    )
    if status != 200:
        die(status, body)
    # Discord returns newest first; flip to chronological for human reading.
    messages = list(reversed(body))
    if args.json:
        print(json.dumps(messages, indent=2, ensure_ascii=False))
        return
    for m in messages:
        author = m["author"].get("username", "?")
        ts = m["timestamp"]
        content = m.get("content") or ""
        print(f"--- [{ts}] {author} (msg {m['id']}) ---")
        if content:
            print(content)
        for a in (m.get("attachments") or []):
            print(f"  📎 {a.get('filename')} {a.get('url')}")
        for e in (m.get("embeds") or []):
            if e.get("title"):
                print(f"  📋 {e['title']}")
        print()


def cmd_archive_thread(args: argparse.Namespace) -> None:
    status, resp = api(
        f"/channels/{args.thread_id}",
        method="PATCH",
        body={"archived": True, "locked": False},
    )
    if status != 200:
        die(status, resp)
    print(f"archived: {resp.get('id')} ({resp.get('name')})")


def cmd_create_channel(args: argparse.Namespace) -> None:
    """Create a text channel under the configured onboarding category.

    Reads ``~/.gits/butler-onboarding.json`` for guild + parent.
    """
    cfg = onboarding.require()
    status, resp = api(
        f"/guilds/{cfg['guild_id']}/channels",
        method="POST",
        body={
            "name": args.name,
            "type": 0,  # 0 = guild text
            "parent_id": cfg["category_id"],
        },
    )
    if status not in (200, 201):
        die(status, resp)
    print(resp["id"])
    print(
        f"# created #{resp['name']} ({resp['id']}) "
        f"under category {cfg['category_id']} ({cfg.get('category_name')})",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Setup wizard (relocated from `ghost discord` → `ghost discord setup`).
# Delegates to the existing interactive flow in __main__ to preserve all
# behavior (token prompt, guild-setup helper, launchd restart).
# ---------------------------------------------------------------------------


def cmd_setup(args: argparse.Namespace) -> None:
    """Interactive Discord setup wizard — was bare ``ghost discord``."""
    from .. import __main__ as _gits_main

    _gits_main._cmd_discord(args)


# ---------------------------------------------------------------------------
# argparse registration + dispatch
# ---------------------------------------------------------------------------


def install_parser(sub: argparse._SubParsersAction) -> None:
    """Register the ``discord`` subparser group under ``ghost``."""
    p = sub.add_parser(
        "discord",
        help="Discord transport (REST primitives) + setup wizard",
        description=(
            "Discord transport primitives. For butler-decorated sends and "
            "PM-semantic commands, use `ghost butler <verb>` instead."
        ),
    )
    verbs = p.add_subparsers(dest="discord_verb", required=True)

    sp = verbs.add_parser(
        "setup",
        help="Interactive Discord setup wizard (token + guild + restart)",
    )
    sp.add_argument("--token", default=None, help="Discord bot token")
    sp.add_argument("--no-start", action="store_true", help="Skip restart after setup")
    sp.set_defaults(func=cmd_setup)

    sp = verbs.add_parser("whoami", help="Print bot identity (id/username/discriminator)")
    sp.set_defaults(func=cmd_whoami)

    sp = verbs.add_parser("send", help="Send a raw message (no prefix decoration)")
    sp.add_argument("target_id", help="Channel or thread ID")
    sp.add_argument("content", help="Message content; use '-' to read from stdin")
    sp.set_defaults(func=cmd_send)

    sp = verbs.add_parser("read-thread", help="Read recent messages from a thread/channel")
    sp.add_argument("thread_id")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--after", help="Only return messages after this message ID")
    sp.add_argument("--json", action="store_true", help="Output raw JSON")
    sp.set_defaults(func=cmd_read_thread)

    sp = verbs.add_parser(
        "create-channel",
        help="Create a text channel under the configured onboarding category",
    )
    sp.add_argument("name", help="Channel name (literal — caller applies any template)")
    sp.set_defaults(func=cmd_create_channel)

    sp = verbs.add_parser("create-thread", help="Create a thread in a text channel")
    sp.add_argument("channel_id", help="Channel ID to create the thread under")
    sp.add_argument("name")
    sp.add_argument("--private", action="store_true", help="Create private thread")
    sp.add_argument(
        "--auto-archive", type=int, default=1440,
        help="Auto-archive minutes (60/1440/4320/10080)",
    )
    sp.set_defaults(func=cmd_create_thread)

    sp = verbs.add_parser("archive-thread", help="Archive (close) a thread")
    sp.add_argument("thread_id")
    sp.set_defaults(func=cmd_archive_thread)

    sp = verbs.add_parser("guild-channels", help="List channels in a guild")
    sp.add_argument("guild_id")
    sp.set_defaults(func=cmd_guild_channels)

    sp = verbs.add_parser("channel", help="Inspect a channel by ID")
    sp.add_argument("channel_id")
    sp.set_defaults(func=cmd_channel)


def dispatch(args: argparse.Namespace) -> None:
    args.func(args)
