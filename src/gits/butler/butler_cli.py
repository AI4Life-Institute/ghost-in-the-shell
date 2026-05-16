"""``ghost butler <verb>`` — PM-semantic layer atop ``ghost discord``.

Calls the same low-level :mod:`gits.butler.http` helpers as the
transport group — no duplicated REST code. The differences vs.
``ghost discord``:

- Identity resolution from caller's cwd (5-step chain in
  :mod:`gits.butler.identity`); no OS-user fallback.
- Per-worktree home channel (``<cwd>/.butler.json``), used as default
  target when ``--channel`` / ``target_id`` is omitted.
- Outgoing prefix decoration (:func:`gits.butler.prefix.decorate`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import identity, onboarding
from .http import api, die
from .prefix import LABEL_DEFAULT, VAULT_DISPATCH_RE, decorate


def _resolve_or_refuse(explicit: str | None) -> str:
    """Resolve outgoing user from caller's cwd, or exit with guidance."""
    user, _ = identity.resolve_user(explicit=explicit, cwd=os.getcwd())
    if user is None:
        sys.exit(
            "ghost butler: cannot determine outgoing user identity.\n"
            "  Provide one of:\n"
            "    --user <name>  /  --as <name>\n"
            "    BUTLER_USER=<name> env var\n"
            "    cd into a personal worktree "
            "(<name>/work branch or vault-<name> dir)"
        )
    return user


def cmd_whoami(args: argparse.Namespace) -> None:
    """Show bot identity + resolved outgoing-prefix user + bound home channel."""
    status, body = api("/users/@me")
    if status != 200:
        die(status, body)
    for k in ("id", "username", "discriminator", "bot", "verified"):
        if k in body:
            print(f"{k}: {body[k]}")
    resolved, source = identity.resolve_user(cwd=os.getcwd())
    if resolved is None:
        print(
            "outgoing_prefix_user: (unresolved — `ghost butler send` would "
            "refuse; set BUTLER_USER, pass --user, or cd into a personal "
            "worktree)"
        )
    else:
        print(f"outgoing_prefix_user: {resolved}  (source: {source})")
    binding = identity.load_binding(cwd=os.getcwd())
    if binding.get("channel_id"):
        print(
            f"home_channel: {binding['channel_id']} "
            f"(#{binding.get('channel_name', '?')})"
        )
    else:
        print("home_channel: (unbound — `ghost butler bind <channel_id>`)")


def cmd_bind(args: argparse.Namespace) -> None:
    """Bind a channel as this worktree's home channel.

    Validates by GETting the channel, then writes ``<worktree>/.butler.json``.
    """
    status, body = api(f"/channels/{args.channel_id}")
    if status != 200:
        die(status, body)
    cwd = os.getcwd()
    binding = identity.load_binding(cwd=cwd)
    binding["channel_id"] = args.channel_id
    binding["channel_name"] = body.get("name")
    binding["guild_id"] = body.get("guild_id")
    identity.save_binding(binding, cwd=cwd)
    print(
        f"bound: {args.channel_id} (#{body.get('name')}) "
        f"in guild {body.get('guild_id')}"
    )


def cmd_unbind(args: argparse.Namespace) -> None:
    """Remove this worktree's home channel binding."""
    cwd = os.getcwd()
    binding = identity.load_binding(cwd=cwd)
    if not binding.get("channel_id"):
        print("no home channel bound")
        return
    identity.clear_binding(cwd=cwd)
    print(
        f"unbound: was {binding.get('channel_id')} "
        f"(#{binding.get('channel_name', '?')})"
    )


def cmd_home(args: argparse.Namespace) -> None:
    """Show this worktree's home channel binding."""
    binding = identity.load_binding(cwd=os.getcwd())
    if not binding.get("channel_id"):
        print("(no home channel bound — `ghost butler bind <channel_id>`)")
        return
    print(f"channel_id:   {binding['channel_id']}")
    print(f"channel_name: #{binding.get('channel_name', '?')}")
    print(f"guild_id:     {binding.get('guild_id', '?')}")


def cmd_config_onboarding(args: argparse.Namespace) -> None:
    """Write ``~/.gits/butler-onboarding.json`` (guild + category for new
    worktree channels). Guild ID is auto-detected from the category.
    """
    status, body = api(f"/channels/{args.category}")
    if status != 200:
        die(status, body)
    if body.get("type") != 4:  # 4 = guild category
        sys.exit(
            f"ghost butler: {args.category} is type={body.get('type')}, "
            f"not a category (expected type=4). Pass a category id, not a "
            f"text channel."
        )
    cfg = {
        "guild_id": body["guild_id"],
        "category_id": args.category,
        "category_name": body.get("name"),
        "channel_name_template": args.template,
    }
    onboarding.save(cfg)
    print(f"wrote {onboarding.user_path_str()}")
    print(f"  guild_id:              {cfg['guild_id']}")
    print(f"  category_id:           {cfg['category_id']} ({cfg['category_name']})")
    print(f"  channel_name_template: {cfg['channel_name_template']}")


def cmd_send(args: argparse.Namespace) -> None:
    """Send with butler prefix decoration; defaults to bound home channel.

    Auto-prefixes so the gateway bot's _handle_message recognizes this as
    vault-dispatched (otherwise it'd be silently dropped under the
    "ignore my own bot's messages" rule).
    """
    cwd = os.getcwd()
    target_id = args.target_id or identity.require_home_channel("send", cwd=cwd)
    content = args.content
    if content == "-":
        content = sys.stdin.read()
    if not args.raw and not VAULT_DISPATCH_RE.match(content):
        label = args.prefix or os.environ.get("BUTLER_PREFIX", LABEL_DEFAULT)
        user = _resolve_or_refuse(args.user)
        content = decorate(label, user, content)
    status, resp = api(
        f"/channels/{target_id}/messages",
        method="POST",
        body={"content": content},
    )
    if status not in (200, 201):
        die(status, resp)
    print(resp["id"])
    print(f"# sent to {target_id} ({len(content)} chars)", file=sys.stderr)


def cmd_dispatch(args: argparse.Namespace) -> None:
    """Create a thread in home channel + post first message; prints new thread id.

    Stdout = thread id (capture-friendly); stderr = one-line summary.
    """
    cwd = os.getcwd()
    channel_id = args.channel or identity.require_home_channel("dispatch", cwd=cwd)

    # Read content up front so we don't leave an empty thread on failure.
    if args.file:
        with open(args.file) as f:
            content = f.read()
    else:
        content = sys.stdin.read()
    if not content.strip():
        sys.exit("ghost butler: dispatch content is empty (read from stdin or --file)")

    if not args.raw and not VAULT_DISPATCH_RE.match(content):
        label = args.prefix or os.environ.get("BUTLER_PREFIX", LABEL_DEFAULT)
        user = _resolve_or_refuse(args.user)
        content = decorate(label, user, content)

    # 1. Create thread under (home or overridden) channel
    status, thread = api(
        f"/channels/{channel_id}/threads",
        method="POST",
        body={
            "name": args.name,
            "type": 12 if args.private else 11,
            "auto_archive_duration": args.auto_archive,
        },
    )
    if status not in (200, 201):
        die(status, thread)
    tid = thread["id"]

    # 2. Post the first message
    status, msg = api(
        f"/channels/{tid}/messages",
        method="POST",
        body={"content": content},
    )
    if status not in (200, 201):
        die(status, msg)

    print(tid)
    print(
        f"# dispatched: thread '{args.name}' ({tid}) in channel {channel_id}, "
        f"first message {msg['id']} ({len(content)} chars)",
        file=sys.stderr,
    )


def cmd_read_thread(args: argparse.Namespace) -> None:
    """Thin passthrough — same behavior as ``ghost discord thread read``."""
    from . import discord_cli

    discord_cli.cmd_thread_read(args)


# ---------------------------------------------------------------------------
# argparse registration + dispatch
# ---------------------------------------------------------------------------


def install_parser(sub: argparse._SubParsersAction) -> None:
    """Register the ``butler`` subparser group under ``ghost``."""
    p = sub.add_parser(
        "butler",
        help="Vault PM dispatch (identity-aware, decorated, defaults to home channel)",
        description=(
            "PM-semantic layer atop `ghost discord`. Resolves outgoing "
            "identity from caller's cwd; stamps the butler prefix the bot "
            "recognizes; defaults target to this worktree's bound home channel."
        ),
    )
    verbs = p.add_subparsers(dest="butler_verb", required=True)

    sp = verbs.add_parser(
        "whoami",
        help="Bot identity + resolved outgoing user + bound home channel",
    )
    sp.set_defaults(func=cmd_whoami)

    sp = verbs.add_parser("bind", help="Bind a channel as this worktree's home channel")
    sp.add_argument("channel_id")
    sp.set_defaults(func=cmd_bind)

    sp = verbs.add_parser("unbind", help="Clear this worktree's home channel binding")
    sp.set_defaults(func=cmd_unbind)

    sp = verbs.add_parser("home", help="Show this worktree's home channel binding")
    sp.set_defaults(func=cmd_home)

    sp = verbs.add_parser(
        "config-onboarding",
        help=(
            "Write ~/.gits/butler-onboarding.json "
            "(guild + category for new worktree channels)"
        ),
    )
    sp.add_argument(
        "--category", required=True,
        help="Discord category ID to nest new channels under (guild auto-detected)",
    )
    sp.add_argument(
        "--template", default="{name}",
        help="Channel name template (default '{name}')",
    )
    sp.set_defaults(func=cmd_config_onboarding)

    sp = verbs.add_parser(
        "send",
        help="Send a butler-decorated message (default target: bound home channel)",
    )
    sp.add_argument(
        "target_id", nargs="?", default=None,
        help="Channel or thread ID (omit to use this worktree's home channel)",
    )
    sp.add_argument("content", help="Message content; use '-' to read from stdin")
    sp.add_argument(
        "--prefix",
        help="Tool label in prefix (default: $BUTLER_PREFIX or 'butler'). e.g. --prefix 管家",
    )
    sp.add_argument(
        "--user", "--as", dest="user",
        help="User in prefix (default: worktree-resolved identity)",
    )
    sp.add_argument("--raw", action="store_true", help="Send content as-is, no auto-prefix")
    sp.set_defaults(func=cmd_send)

    sp = verbs.add_parser(
        "dispatch",
        help="Create a thread in home channel + post first message; prints new thread id",
    )
    sp.add_argument("name", help="Thread name")
    sp.add_argument("--file", help="Read content from this file (default: stdin)")
    sp.add_argument("--channel", help="Override home channel for this dispatch")
    sp.add_argument("--private", action="store_true", help="Create private thread")
    sp.add_argument(
        "--auto-archive", type=int, default=1440,
        help="Auto-archive minutes (60/1440/4320/10080)",
    )
    sp.add_argument("--prefix", help="Tool label in prefix (default: 'butler')")
    sp.add_argument(
        "--user", "--as", dest="user",
        help="User in prefix (defaults to worktree identity)",
    )
    sp.add_argument(
        "--raw", action="store_true",
        help="Send first message as-is, no auto-prefix",
    )
    sp.set_defaults(func=cmd_dispatch)

    sp = verbs.add_parser(
        "read-thread",
        help="Thin passthrough to `ghost discord thread read`",
    )
    sp.add_argument("thread_id")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--after", help="Only return messages after this message ID")
    sp.add_argument("--json", action="store_true", help="Output raw JSON")
    sp.set_defaults(func=cmd_read_thread)


def dispatch(args: argparse.Namespace) -> None:
    args.func(args)
