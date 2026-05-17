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

### Post-dispatch: monitor for executor replies

Dispatch is not fire-and-forget. After a task lands in a Discord thread, the
executor will eventually reply (plan → question → diff), and the butler
session needs to *notice* and surface those replies to the operator with the
right urgency. The recipe below is dispatch's downstream half — the two
belong together. It is a **session-level pattern**, not a CLI verb; there is
no `ghost butler monitor` command.

> **Lifecycle rule.** Session-internal automation should be lifecycle-tied
> to observable need — not "always on in case." Run the monitor iff at least
> one task is in a `dispatched*` state; stop it when the last one transitions
> out. Generalizes beyond this recipe — same rule applies to background
> agents, watchers, and any other always-on poll.

#### When to start / stop the monitor

| Trigger | Action |
|---|---|
| Session start: grep `Projects/*/tasks/**/*.md` for `status: dispatched` finds matches | `CronCreate` the monitor; persist returned job-id to `.vault-session/monitor-cron-id` |
| `ghost butler dispatch` succeeds AND `.vault-session/monitor-cron-id` is absent | Same — start monitor cron and persist its id |
| Monitor poll observes the last `dispatched*` task transitioned out (→ `review` / `done` / `cancelled`) | Self-terminates: `CronDelete <that-job-id>`, remove `.vault-session/monitor-cron-id`, exit (baked into the prompt below) |

Monitor scope is the current Claude session; concurrent vault sessions each
run their own. Race on `.vault-session/poll-state.json` is theoretical edge
case (YAGNI for v1).

Invoke via Claude Code's `/loop` skill at 1-minute interval — `/loop` wraps
`CronCreate` underneath, so if `/loop` is not loaded you can call
`CronCreate` directly with the same prompt body and a `*/1 * * * *`
schedule. Capture the returned job-id into `.vault-session/monitor-cron-id`
so the monitor can self-delete on its final poll.

#### Monitor prompt (paste-ready)

The `[butler:<your-butler-user>]` substring below is operator-specific —
substitute the user returned by `ghost butler whoami` at session-start time
(e.g. `[butler:weiliu]` in `vault-weiliu/`, `[butler:kathy]` in
`vault-kathy/`).

````
Scan all task pages under Projects/*/tasks/**/*.md whose frontmatter
`status` starts with `dispatched`. For each: extract the thread id from
the `thread:` frontmatter URL, run `ghost butler read-thread <tid>
--limit 20`, and identify the latest non-bot message. Classify each task
as one of: **waiting-on-me** (latest substantive message has no
`[butler:<your-butler-user>]` prefix — i.e. executor responded and I
haven't replied), **waiting-on-executor** (latest substantive message
has `[butler:<your-butler-user>]` prefix — I dispatched / replied,
executor hasn't responded yet), or **idle** (no messages in >12h).

If the scan finds **0 tasks** in `dispatched*` state AND a monitor cron
is currently running (its job-id stored at
`.vault-session/monitor-cron-id`), invoke `CronDelete <that-job-id>`,
remove the file, and exit — polling has no remaining purpose.
(Demonstrates the lifecycle-tied rule: no dispatched task → no monitor.)

Otherwise, print a compact dashboard:

```
=== thread monitor [HH:MM UTC] ===
Total active dispatched: N    waiting-on-me: M    waiting-on-executor: K
- [[id]] (status, area) → who-on, last-msg <Hh ago>: "<first-80-chars-of-last-msg>"
...
```

If any task is **newly** in waiting-on-me state (compared to the prior
snapshot — store snapshots at `.vault-session/poll-state.json`, creating
the dir if needed; key by tid, value `{last_msg_id, last_who, last_ts}`),
flag with a loud header `⚠️ NEW: <id> needs reply` so I notice. If no
state change since last check, print just `=== thread monitor [HH:MM UTC]
=== no change` and stop. Do NOT take any action on the threads — just
report.
````

#### See also

- The `ghost butler dispatch` section above — the upstream half.
- `docs/dispatch-lifecycle.md` — full dispatch → plan → greenlight →
  acceptance → archive flow.
- Future improvement (out of scope here): ghost daemon push notifications
  would replace this polling design.

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
