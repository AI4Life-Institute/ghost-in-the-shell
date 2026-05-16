---
name: butler
description: Use when the user wants to interact with Discord via the ghost butler CLI — send `[butler:<user>]`-prefixed messages, read threads, bind a worktree's home channel, or dispatch a project task (vault-aware orchestrator that creates a Discord thread, posts /bind + pointer, and atomically writes thread metadata back into the task page). Trigger phrases include "send to Discord", "派 <task-id>", "dispatch <task-id>", "bind this channel", "read thread <id>".
---

# Butler

PM-semantic Discord CLI for vault-style worktree sessions. Talks Discord REST
directly using ghost's bot token; no Gateway connection. Stamps every outgoing
message with a `📨 **[butler:<user>]** …` prefix so ghost's gateway loop
recognizes it as vault-dispatched (and won't echo it back to the CLI).

## Capabilities

| Command | Purpose |
|---|---|
| `ghost butler whoami` | Bot identity + resolved outgoing user + bound home channel |
| `ghost butler bind <channel_id>` | Bind a Discord channel as this worktree's home channel |
| `ghost butler unbind` | Clear this worktree's home channel binding |
| `ghost butler home` | Show this worktree's home channel binding |
| `ghost butler config-onboarding` | Write `~/.gits/butler-onboarding.json` (guild + category for new-worktree channels) |
| `ghost butler send [target] <content>` | Post a `[butler:<user>]`-prefixed message; target defaults to home channel; use `-` for stdin |
| `ghost butler dispatch <task-id> [--phase plan\|impl]` | Vault-aware orchestrator (see section below) |
| `ghost butler read-thread <tid> [--limit N]` | Read messages chronologically |

For raw Discord resource ops (no butler prefix, no worktree identity
resolution), use `ghost discord {thread,channel,message} <verb>` — see
`ghost discord --help`.

## Identity resolution

`ghost butler` resolves the outgoing user from the caller's `cwd` git
worktree, in this order:

1. `--user <name>` / `--as <name>` flag
2. `BUTLER_USER=<name>` env var
3. Git branch matching `<name>/work`
4. Worktree dir basename matching `vault-<name>`
5. **Refuse** — no `getpass.getuser()` fallback; identity must come from the
   worktree, never the OS user

So in `vault-weiliu/` you're `weiliu`, in `vault-kathy/` you're `kathy`.
Identity is per-cwd, not per-OS-user — supports multi-human shared machines.

## `ghost butler dispatch <task-id>` — vault-aware orchestrator

Atomically: open a Discord thread for a project task, post `/bind` then the
pointer message, **write `thread:` / `dispatched:` / `dispatch_msg_id:` /
`status:` back into the task frontmatter** in one operation, and rollback
(archive the just-created thread) if any step after thread creation fails.
Then lint and print a summary.

This is the **only** way the vault session should dispatch project tasks.
Using raw `ghost discord thread create` + `ghost butler send` for project
work leaves the thread↔task binding floating in your head instead of in the
file.

### Trigger phrases

- "派 <id-or-title>" / "派任务 <id>"
- "dispatch <id>" / "dispatch this task"
- "把 <title> 派出去"
- "开始 <id>" (only if status=draft; otherwise this is a different intent)

If the user says "派 <X>" but `<X>` is ambiguous, the orchestrator lists
candidate paths and exits non-zero; ask the user which one they meant.

### Invocation

```
ghost butler dispatch <task-id> [--phase plan|impl]
```

- `<task-id>` — 6-char task id (preferred — unique) or a fuzzy filename
  fragment (substring match against task file basenames).
- `--phase` — defaults to `plan`. Use `impl` only when the user has explicitly
  green-lit a previous plan-phase response.

Run from inside any vault-like worktree. The orchestrator resolves the repo
root from cwd via `git rev-parse --show-toplevel`.

**Before dispatching**, the vault session should have already locked
operator-level design decisions WITH the operator and baked them into the
task page as constraints, not as "executor's call".

### How Claude should use it

1. Translate the user's trigger phrase into `<task-id>` — id if known, else a
   distinctive title fragment.
2. Pick `--phase`. Default `plan`. Use `impl` only when the user has
   explicitly green-lit a previous plan-phase response.
3. Run `ghost butler dispatch <task-id> [--phase ...]`; capture stdout. The
   summary block at the end is what to relay to the user.
4. If the command exits non-zero, **do not retry blindly** — read the error,
   relay it, and ask the user how to proceed. The orchestrator already
   cleaned up Discord-side (archived the thread) for any failure between
   thread creation and writeback. Lint failures leave the dispatch landed
   but flagged for investigation.

### Halt conditions

Each exits non-zero with a clear stderr message:

1. `<task-id>` matches zero or multiple files
2. Task `status:` is not `draft` (refuse to re-dispatch silently; the user
   must change status manually or pick a different action)
3. Task `personas:` is missing or empty (schema requires it)
4. Operator has no home channel bound — tell the user to
   `ghost butler bind <channel_id>` first
5. `ghost butler whoami` fails (token / network / install issue)

…and during execution:

6. Thread creation fails → exit (no thread created, nothing to roll back)
7. Send of `/bind` or pointer fails → archive thread, exit
8. Frontmatter writeback fails (e.g. permissions on the task file's parent
   dir) → archive thread, exit (task page untouched — rename-from-tmp
   guarantees this)
9. Lint fails → exit non-zero, leave the dispatch landed for the user to
   inspect

### Troubleshooting

**Lint failed but dispatch landed.** Thread and frontmatter writeback both
succeeded; lint flagged one of: a frontmatter field missing post-write
(unexpected — file a bug), `thread:` URL tid mismatch (stale state from a
prior attempt that didn't roll back), or `ghost butler read-thread` returned
fewer than 2 messages even after the retry budget (usually Discord latency;
if persistent, something is wrong with the thread). Run
`ghost butler read-thread <tid>` manually before retrying.

**"dispatch failed".** Run `ghost butler whoami` directly. Common causes:
token rotation (re-read `~/.gits/config.env`), bot lost access to the home
channel, network blip.

**"frontmatter writeback failed; archived thread".** The task page is
unwritable or its parent dir is read-only. Fix the perms, then re-dispatch —
the just-created thread was rolled back, so the task is back to
`status: draft` and re-dispatch is safe.

**Wrong channel.** Dispatch always uses the worktree's bound home channel;
it intentionally ignores any thread/channel info on the task page itself.
Your dispatched work goes under your own channel regardless of which project
the task belongs to.

## When to use this skill

- The user asks to message a Discord channel or thread from this worktree
  session (English: "send to Discord", "post to #channel", "reply in thread";
  Chinese: "发到 Discord", "在频道里说", "回复线程").
- The user wants to dispatch a project task — see trigger phrases above.
- The user wants to read recent thread activity (`read-thread`).
- The user wants to bind / unbind / check the worktree's home channel.
- Onboarding a new contributor worktree — delegate to the `onboard-worktree`
  skill (see `../onboard-worktree/SKILL.md`).

## Related

- `ghost butler --help` — full flag reference for every verb
- `ghost discord --help` — raw Discord transport primitives (no prefix, no
  worktree identity)
- `../onboard-worktree/SKILL.md` — recipe for creating new contributor
  worktrees + channels
