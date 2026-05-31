---
name: onboard-worktree
description: Use when the user wants to onboard a new contributor to a vault-style repo by creating a personal git worktree, a dedicated Discord channel, and wiring ghost's /bind so messages in that channel route to a Claude session in the worktree. Trigger phrases include "onboard <name>", "给 <name> 创建 worktree", "给 <name> 开个 worktree", "新建 worktree for <name>", "<name> 接入 vault", "set up <name> on this vault".
---

# Onboard worktree

Bootstrap a new agent end-to-end: a registry node + git worktree + branch +
dedicated Discord channel + butler home-channel binding + ghost `/bind`. After
this runs, the new contributor can `cd` into their worktree, `ghost butler
send "..."` works immediately, and posting in the Discord channel triggers a
Claude session there.

**This skill is the human-facing trigger only. It DELEGATES every mechanical
and atomic step to `ghost butler onboard`.** That command is the single code
path allowed to write the `Org/<org>/<name>.yaml` registry node — it writes the
node, regenerates `_meta.tree_snapshot`, lints, and commits **atomically**. Do
NOT hand-author the YAML node yourself: an LLM writing the registry will
eventually produce a duplicate alias or a dangling `reports_to`, and that is
exactly what the command + lint exist to prevent. See
`<ghost-repo>/docs/org-schema.md` for the schema this enforces.

## When to use

Trigger phrases (any language):

- "给 <name> 创建一个 worktree" / "给 <name> 开个 worktree"
- "onboard <name>" / "set up <name> on this vault"
- "新建 worktree for <name>"
- "<name> 接入" / "<name> 加入这个 vault"

If the user says something close but doesn't give a `<name>`, ask:
"用什么名字？(英文小写，对应 git 分支 `<name>/work`、worktree dir `vault-<name>`、
Discord channel 和注册表节点 `Org/<org>/<name>.yaml`)"

## Inputs

- `<name>` — the agent's alias. Must match `^[a-z][a-z0-9_-]*$` and not be
  `main` / `master` / `head`. (The command re-validates this.)
- Optional: who they `--reports-to` (defaults to the caller's own node), the
  `--scope` folder(s) they own, `--human`, `--cli`, `--desc`.

Everything else (org, guild, onboarding category, worktree parent dir) the
command resolves itself from the caller's vault `Org/<org>/_meta.yaml`.

## How to run it

Run from inside the **parent's** worktree — the caller's own node becomes the
new node's `reports_to` by default.

```bash
# Always preview first — prints the plan, creates nothing:
ghost butler onboard <name> --dry-run \
  [--reports-to <alias>] [--scope <folder>] [--human <name>] \
  [--cli claude|codex|copilot|opencode] [--desc "<one line>"]

# Then create for real (drop --dry-run):
ghost butler onboard <name> \
  [--reports-to <alias>] [--scope <folder>] [--human <name>] [--cli <cli>] [--desc "..."]
```

Notes:

- **From `main` / an unregistered channel** there is no caller node to default
  from, so `--reports-to <alias>` becomes **required**. Pass the org root
  (e.g. `--reports-to Ai4Life`) for a top-level subsidiary.
- The command is **idempotent**: re-running an already-onboarded name is a
  no-op ("already onboarded"); if an earlier run halted mid-way (channel or
  worktree exists but the node is missing), re-running finishes the rest.
- It does **not** `git push` — the new branch stays local and propagates to
  other worktrees via vault-sync.

## Halt conditions

`ghost butler onboard` halts (and reports what, if anything, it already
created) on: invalid/reserved name; alias already taken with a different
`reports_to`; `--reports-to` not in the org; a `<name>/work` branch already on
origin; missing guild/category config; a non-conflict Discord API error; or no
🔗 reaction within ~5s (ghost may be down — channel/worktree/node/binding are
left in place; just re-run to resume the bind).

If the command halts, relay its message to the user verbatim — it tells you
exactly what to fix or how to resume. Do not try to patch the registry by hand.

## Verify + summary

The command prints a summary (worktree path, channel, `reports_to`, scope, lint
state). To double-check from the new worktree:

```bash
cd <wt-parent>/vault-<name>
ghost butler whoami    # outgoing_prefix_user: <name>; home_channel: #<name>
ghost org lint         # the org tree must be clean
```

## Related

- `<ghost-repo>/docs/org-schema.md` — the normative registry schema this enforces.
- `ghost org lint` — standalone lint; also the vault commit-time gate.
- `../butler/SKILL.md` — the underlying `bind` / `send` / `/bind` primitives.
