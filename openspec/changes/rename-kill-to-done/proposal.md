# Change: Rename /kill to /done — Fix Thread Archive Locking

## Why

The `/kill` command ends a work session and archives the Discord thread, but two bugs
prevent the thread from actually disappearing from the Discord sidebar:

1. **`archive_thread` does not lock the thread** — `thread.edit(archived=True)` alone
   is insufficient. Discord auto-unarchives a thread the moment any message is posted
   to it. The confirmation reply sent by `/kill` lands *after* the archive call,
   immediately unarchiving the thread and causing it to reappear in the sidebar.

2. **Reply is sent after archive** — `handle_kill` calls `_kill_single` (which
   archives) and then calls `_reply`. Discord replies via `interaction.followup.send()`
   post to the thread channel. If the thread is archived without being locked, this
   re-opens it; if it is locked, the followup fails.

The fix is:
- Always set both `archived=True` and `locked=True` when closing a thread so that
  no subsequent message can re-open it.
- Send the reply to the interaction *before* archiving.

Additionally, the command name `/kill` implies forceful termination. The correct
semantic for a completed work session is *done* — the session finished, the thread
should close. Renaming to `/done` makes the intent clear.

## What Changes

| Area | Before | After |
|---|---|---|
| Discord slash command | `/kill` | `/done` |
| `archive_thread` | `thread.edit(archived=True)` | `thread.edit(archived=True, locked=True)` |
| `handle_kill` reply ordering | reply after archive | reply before archive |
| Engine method name | `handle_kill` | `handle_done` |
| Docstrings / log messages | "kill" | "done" |

`_kill_single` is an internal method invoked from non-command paths (thread archive
event, thread delete event) and keeps its name — only the public command surface and
`handle_kill` are renamed.

## Scope

Single capability delta in `discord-interactions`.
