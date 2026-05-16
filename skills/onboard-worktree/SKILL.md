---
name: onboard-worktree
description: Use when the user wants to onboard a new contributor to a vault-style repo by creating a personal git worktree, a dedicated Discord channel, and wiring ghost's /bind so messages in that channel route to a Claude session in the worktree. Trigger phrases include "onboard <name>", "给 <name> 创建 worktree", "给 <name> 开个 worktree", "新建 worktree for <name>", "<name> 接入 vault", "set up <name> on this vault".
---

# Onboard worktree

Bootstrap a new personal worktree end-to-end: git worktree + branch +
dedicated Discord channel + butler home-channel binding + ghost `/bind` so
messages in the channel forward to a Claude session in the worktree. After
this runs, the new contributor can `cd` into their worktree, `ghost butler
send "..."` works immediately, and posting in the Discord channel triggers a
Claude session there. See `../butler/SKILL.md` for the underlying primitives.

## Placeholders

These appear in the steps below; resolve them once at the top of a run.

- `<vault-root>` — path to the main vault repo (the one all personal
  worktrees are siblings of). Typically `~/src/vault-main/` or the user's
  configured equivalent.
- `<ai4life-src>` — parent directory where personal worktrees live (the
  sibling-of-`<vault-root>` parent). New worktrees go in
  `<ai4life-src>/vault-<name>/`.

## When to use

Trigger phrases (any language):

- "给 <name> 创建一个 worktree" / "给 <name> 开个 worktree"
- "onboard <name>" / "set up <name> on this vault"
- "新建 worktree for <name>"
- "<name> 接入" / "<name> 加入这个 vault"

If the user says something close to this but doesn't give a `<name>`, ask:
"用什么名字？(英文小写，对应 git 分支 `<name>/work` 和 worktree dir `vault-<name>`)"

## Inputs

- `<name>` — the contributor's identity. Must match `[a-z][a-z0-9_-]*` and
  not be `main` / `master` / `head`.

The recipe reads these from the environment / config (no need to ask the
user):

- Vault main repo path → `<vault-root>` (see Placeholders)
- Worktree parent dir → `<ai4life-src>` (see Placeholders)
- Discord category for new channels → `~/.gits/butler-onboarding.json`

## Halt conditions

Stop and report to the user (don't continue) if any of these hit:

1. `<name>` fails the regex check
2. A worktree at `<ai4life-src>/vault-<name>` already exists (don't
   overwrite)
3. A git branch `<name>/work` already exists locally or on origin (don't
   reuse silently)
4. `~/.gits/butler-onboarding.json` doesn't exist → tell the user to run
   `ghost butler config-onboarding --category <id>` first
5. Discord API call fails for any non-conflict reason
6. Ghost doesn't react to the `/bind` within ~5s — likely down or the
   gateway loop isn't recognizing butler-prefixed commands. Report so the
   user can investigate; the worktree + channel + butler binding are still
   left in place.

If a channel matching the configured template already exists under the
category (common case: re-onboarding on a new machine), **don't fail** —
reuse the existing channel id and skip the create step.

## Steps

Run from `<vault-root>` (the main repo, not an existing worktree).

### 1. Validate `<name>`

```bash
# Reject if not a clean identifier
[[ "<name>" =~ ^[a-z][a-z0-9_-]*$ ]] || halt "invalid name"
case "<name>" in main|master|head) halt "reserved name" ;; esac
```

### 2. Check git state — no existing worktree/branch

```bash
git -C <vault-root> worktree list | grep -q "vault-<name>" && halt "worktree exists"
git -C <vault-root> rev-parse --verify "<name>/work" 2>/dev/null && halt "branch exists locally"
git -C <vault-root> ls-remote --exit-code origin "<name>/work" 2>/dev/null && halt "branch exists on origin"
```

### 3. Load onboarding config

```bash
# Halts inside ghost butler if missing — error message tells user to run config-onboarding
ghost butler whoami > /dev/null   # cheap token + connectivity check first
cat ~/.gits/butler-onboarding.json   # to read guild_id + category_id + channel_name_template
```

### 4. Compute channel name + check if it already exists

Apply the template from `butler-onboarding.json` to `<name>` (e.g. template
`{name}` + name `ada` → `ada`).

```bash
# List channels in the guild; filter to the category and the computed name
ghost discord channel list --guild <guild_id> \
  | awk -v p=parent=<category_id> '$NF==p && $4=="<channel_name>" {print $1}'
```

If found → record its id as `<channel_id>`, skip step 5.
If not found → continue to step 5.

### 5. Create the channel

```bash
channel_id=$(ghost discord channel create "<channel_name>" 2>/dev/null)
```

### 6. Create the worktree

```bash
git -C <vault-root> worktree add <ai4life-src>/vault-<name> -b "<name>/work"
```

### 6.5. Push the new branch to origin

So the branch is visible on GitHub for backup and for other machines the
contributor may use:

```bash
git -C <ai4life-src>/vault-<name> push -u origin "<name>/work"
```

### 7. Bind home channel from inside the new worktree

```bash
cd <ai4life-src>/vault-<name>
ghost butler bind "$channel_id"
```

### 7.5. Bind ghost to the channel (so `/bind` takes effect)

The new channel is empty — ghost isn't watching it for any path yet. Send a
`/bind` via ghost butler so ghost wires the channel up to this worktree:

```bash
# ghost butler send auto-prefixes [butler:<name>]; ghost recognizes the prefix
# and treats messages starting with '/' as slash commands.
ghost butler send "/bind <ai4life-src>/vault-<name> claude"
```

Ghost should add a 🔗 reaction within a second or two confirming the bind.
If no reaction appears within ~5s, halt and report: ghost may be down or the
gateway-side butler-prefix command handler may be missing on this
deployment.

The `claude` argument picks the CLI; omit it to take whatever ghost's
default is, or pass `codex` / `copilot` / `opencode` if the contributor uses
a different CLI.

### 8. Verify

```bash
ghost butler whoami
# Expect:
#   outgoing_prefix_user: <name>  (source: worktree branch (<name>/work))
#   home_channel:         <channel_id> (#<channel_name>)
```

If either line is wrong, halt and report (don't try to auto-fix — usually
means the worktree dir or branch convention got mangled).

### 9. Summary back to user

Tell the user in one block:

- ✓ worktree at `<ai4life-src>/vault-<name>` on branch `<name>/work`
- ✓ home channel `#<channel_name>` (`<channel_id>`) — bound for butler
- ✓ ghost bound `#<channel_name>` → `<ai4life-src>/vault-<name>` (claude) — confirmed by 🔗
- Next: `cd <ai4life-src>/vault-<name>` — `ghost butler send "msg"` /
  `ghost butler dispatch "<task-id>"` works; messages posted to
  `#<channel_name>` in Discord get forwarded to a Claude session in this
  worktree.

## Related

- `../butler/SKILL.md` — underlying CLI; this recipe orchestrates
  `ghost discord channel create` + `ghost butler bind` + a `/bind` send.
- `ghost discord --help` — raw transport primitives used in steps 4–5.
