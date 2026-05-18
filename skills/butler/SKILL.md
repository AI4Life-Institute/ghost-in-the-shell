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

#### Upgrade: CEO-mode autonomous driver

When the operator explicitly says "you're the CEO" (or one of the
trigger phrases below), the read-only monitor above evolves into an
**autonomous driver**: same lifecycle, same dynamic enumeration, with
an action layer on top — auto-ack tactical executor questions,
auto-merge green PRs, auto-dispatch dependent tasks. Without an
explicit trigger, default behavior stays monitor-only.

> **Why this section exists** (2026-05-18 lesson). An earlier
> session-only driver prompt hardcoded a list of thread IDs at
> cron-creation time. As new tasks dispatched throughout the night,
> their thread IDs never entered the cron's view, so the driver
> silently skipped ~6 executors waiting on plan-phase Qs for 7+
> hours. The fix is **dynamic enumeration via grep on `^status:
> dispatched` in task frontmatter** at every fire — never hardcode
> thread IDs in the prompt.

##### Trigger phrases

| Language | Phrase examples |
|---|---|
| English | "you're the CEO", "drive autonomously", "auto-merge what's green", "I'm going to sleep — keep moving" |
| Chinese | "你是 CEO, 自己做主", "能自己做主就自己做主", "我要睡觉了, 你推进", "自动驱动" |

##### Lifecycle & monitor↔driver transition

Driver inherits the monitor's lifecycle rule (run iff at least one task
is `dispatched*`; self-delete when none remain) and adds a CEO-mode
on/off transition. The driver **replaces** the monitor — they do not
coexist; the 10-min driver cadence already covers the monitor's report
work plus actions.

| Trigger | Action |
|---|---|
| Operator says a CEO-mode trigger phrase AND ≥1 task is `dispatched*` | If a monitor cron is running, `CronDelete <monitor-job-id>` and remove `.vault-session/monitor-cron-id`. Then `CronCreate` the driver (prompt below, 10-min schedule); persist returned job-id to `.vault-session/driver-cron-id`. Initialise `.vault-session/driver-state.json`. |
| Operator says "退出 CEO 模式" / "back to monitor" / "stop driver" | `CronDelete <driver-job-id>`, remove `.vault-session/driver-cron-id`. If ≥1 task is still `dispatched*`, recreate the monitor cron per the section above. |
| Driver's own scan finds 0 tasks in `dispatched*` state | Self-terminate: `CronDelete <driver-job-id>`, remove the file, exit. (Same rule as monitor — no dispatched task → no driver.) |

##### Cadence

`*/10 * * * *`, or `3-59/10 * * * *` to offset from the top of the hour.
10 min keeps polling cost trivial and leaves room for operator messages
on Discord to land between fires; a 1-min cadence (the monitor's)
over-polls when actions are involved. Operators with stronger latency
needs can override the cron expression.

##### Quality bar for auto-merge

ALL of the following must hold before the driver merges a PR:

- `gh pr view <N> --json statusCheckRollup` shows every **required**
  check green (advisory checks may remain in-progress).
- `mergeable: MERGEABLE` (no conflicts).
- Diff ≤ 10 files AND ≤ 500 LOC. Exception: a lockfile-only catch-up
  from a freshly merged neighbour PR — flag it in
  `<vault-root>/log.md` and merge.
- For UI / code PRs: open added test files and **read the
  assertions** — stub bodies (`assert True`, empty `it("...", () => {})`)
  fail the bar; escalate.
- For docs / audit PRs: open the changed files and read the content,
  not just the file count.
- Scope deviation from the task spec ≤ 20%.
- No schema-loss or data-loss surface — any `DROP TABLE`,
  column-remove, or destructive migration: escalate.

See `[[feedback-ceo-mode-merge-autonomy]]` memory for the original
rationale.

##### Per-fire workflow (paste-ready)

The substring `[butler:<your-butler-user>]` below is operator-specific
— substitute the user returned by `ghost butler whoami` at
session-start time.

````
Silent unless an action is taken or an escalation is needed.

STEP 1 — Enumerate in-flight tasks DYNAMICALLY (the KEY FIX):
  in_flight=$(grep -lr "^status: dispatched" Projects/*/tasks/ 2>/dev/null)
  for f in $in_flight; do
    extract `id:` and `thread:` from frontmatter
  done
  # NEVER hardcode thread IDs in this prompt — they go stale within hours.

STEP 2 — Check open PRs from every source repo bound to this vault.
  Source-repo list is the placeholder table in <vault-root>/MACHINES.md
  (e.g. <ghost-repo>, <ai4stock-repo>, <vibo-repo>, <stock-arena-repo>);
  resolve each to its GitHub org/repo via that table.
  For each bound repo:
    gh -R <org>/<repo> pr list --state open \
      --json number,title,mergeable,statusCheckRollup,headRefName,additions,deletions,files
    For each open PR:
      - If the quality bar above holds → gh pr merge <N> --squash --delete-branch
      - If CONFLICTING → dispatch a rebase task to that branch
      - If CI red AND cause is a lockfile cascade from a just-merged neighbour PR
        → dispatch a rebase task

STEP 3 — For each in-flight task's thread:
  ghost butler read-thread <tid> --limit 5
  Filter out 🔧 tool-call streaming + scan-agent "no new gaps" noise.
  If the executor surfaced plan-phase questions:
    - Check the task page for an `## Operator answers` section
      (pre-baked defaults).
    - If pre-baked → ack with a short pointer: "spec updated, re-read .md, go".
    - If not pre-baked AND the questions are tactical (not strategic) →
      ack the executor's own recommended defaults inline; cite the
      escalation rule.
    - If strategic OR spend-bearing OR a hard block → ESCALATE (see below).
  If the executor reports STOP / unrecoverable → ESCALATE.
  If the executor opened a PR → it will get picked up in STEP 2 on the
  next fire.

STEP 4 — Push-forward (dependency chain):
  If a PR just merged AND a task with status `draft` exists whose
  `parent_id` matches the merged task's id:
    ghost butler dispatch <dependent-id>
  (Example: M1 w3g3hs merges → immediately dispatch M2 zxvm49.)

STEP 5 — Vault hygiene:
  Read .vault-session/driver-state.json; update merge_count_since_last_commit
  and last_fire_ts. If merge_count_since_last_commit ≥ 3 OR
  (now - last_vault_commit_ts) > 1h:
    cd <vault-root> && git add -A && git commit -m "..." && git push
    reset merge_count_since_last_commit = 0; set last_vault_commit_ts = now
  Don't let task-page status flips and memory updates pile up uncommitted.

STEP 6 — Heartbeat (always, even on no-op):
  Append one line to .vault-session/driver-heartbeat.log:
    [<ISO8601 now>] fire | pr_open=N | in_flight=M | actions=K
  Meta-fix for the 2026-05-18 silent-failure mode — operators can
  `tail -20` to confirm the driver is alive.
````

##### Hard guardrails (never, even in CEO mode)

- ❌ Merge a PR with any **required** check red.
- ❌ Merge a PR with `CONFLICTING` / `DIRTY` mergeable status.
- ❌ Push directly to `master` / `main` — always via PR.
- ❌ Force-push to a branch you don't own (no force-with-lease on
  `master` / `main`).
- ❌ Delete branches that still have unpushed work.
- ❌ Skip operator escalation for: scope > 20% deviation, schema loss,
  security / privacy surface, hard stop from an executor.
- ❌ Touch the operator's canonical worktree (`git -C` for **read**
  only; never write).
- ❌ Bypass vault hooks (`block-source-repo-mutations`,
  `block-long-thread-message`, `block-spaces-in-md-filenames`).
- ❌ Re-litigate decisions the operator already made — consult
  `<vault-root>/log.md` and your memory index before acting.

##### Escalation format

When the driver must surface to the operator, emit one line:

```
🚨 ESCALATE: <task-id> — <reason>; my recommendation: <action>; awaiting your call
```

…and append the same line to `<vault-root>/log.md`. Do not proceed past the escalation point on the affected task until the operator answers (other tasks may continue in parallel). Per `[[feedback-operator-never-runs-commands]]`, phrase the recommendation as a decision (A/B/C choices), not as a shell command the operator should run.

##### Output rules

- **0 actions taken AND no new substantive thread activity AND no PRs to merge**: output absolutely nothing. The STEP 6 heartbeat line is still appended.
- **Actions taken**: one line per action in `⚙️ <action> on <target>` form.
- **Escalation needed**: the 🚨 ESCALATE block above + the `log.md` entry + stop on the escalated task.

##### Future improvements

- Trigger phrases above also live in the
  `[[feedback-ceo-mode-merge-autonomy]]` memory and in vault
  `CLAUDE.md`; consolidating to a single source is future work —
  flagged here so the drift risk is visible.

##### See also

- `#### Monitor prompt (paste-ready)` above — the read-only baseline this section upgrades.
- `ghost butler dispatch <task-id>` — the upstream half.
- `docs/dispatch-lifecycle.md` — full plan → greenlight → acceptance flow.

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
