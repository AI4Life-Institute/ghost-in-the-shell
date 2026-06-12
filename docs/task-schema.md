# Task schema

This is the schema any vault-like repo follows when ghost manages its tasks. It defines what a project page looks like, what a task page looks like, the status lifecycle, archive rules, and the conventions that ghost's dispatch tool and lint expect.

See also: [role](role.md) (what the butler does), [dispatch lifecycle](dispatch-lifecycle.md) (how a task moves from idea to archived).

## IDs

Every project and every task gets a **6-character random alphanumeric ID** (a-z + 0-9). 36⁶ ≈ 2.2 billion combinations — collisions don't happen in practice.

Generate with:

```bash
python3 -c "import secrets; print(''.join(secrets.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(6)))"
```

The ID lives in the `id:` frontmatter field and is **prefixed into task filenames** for quick scan/sort. Vaults using Obsidian-style wiki links typically reference tasks as `[[<id>]]`; vaults using plain markdown can use any reference style — the ID itself is what's load-bearing, the link syntax is a per-vault choice.

> **Filename form**: dash-separated, no spaces — `<YYYY-MM-DD>-<id>-<title-with-dashes>.md`. Butler also accepts the legacy space-separated form (`<YYYY-MM-DD> <id> <Title>.md`) on disk indefinitely, since older task pages predate the convention; new files use dashes.

## Directory layout

```
Projects/
├── Active.md / Backlog.md / Archive.md           ← cross-project indices
└── <Project Name>/                                ← one folder per project
    ├── README.md                                  ← project info (id, repo, paths, etc.)
    ├── tasks/                                     ← active / in-progress / under review
    │   └── <area>/YYYY-MM/                        ← area = subsystem (defined in project README)
    │       ├── <YYYY-MM-DD>-<id>-<title>.md           ← atomic task
    │       └── <YYYY-MM-DD>-<id>-<title>/             ← epic (folder)
    │           ├── README.md                          ← epic overview
    │           ├── spec.md                            ← locked before subtasks
    │           └── <YYYY-MM-DD>-<subid>-<subtitle>.md ← subtasks
    ├── archive/                                   ← completed / cancelled tasks (same shape)
    │   └── <area>/YYYY-MM/
    │       └── <YYYY-MM-DD>-<id>-<title>.md
    └── docs/                                      ← optional: deep dives, design notes
```

The `archive/` tree mirrors `tasks/`. When a task's status becomes `done` or `cancelled`, `git mv` the file (or folder, if epic) from `tasks/<area>/<month>/` to `archive/<area>/<month>/`. The path mirrors so future "what discord-bot work happened in 2026-05" queries work uniformly across active + archive. Cross-references by ID keep working regardless of path.

## Project README — required frontmatter

```yaml
---
id: <6-char>
tags: [project, <status>, <other>]
local_path: /absolute/path/to/repo
repo: <github-org>/<repo-name>     # if applicable
status: active | paused | archived
updated: YYYY-MM-DD
areas: [discord-bot, tmux-engine, packaging, ...]   # subsystems used for tasks/<area>/
---
```

Recommended body sections: Summary, Locations table, Architecture, How it runs, Configuration, Operating it, Troubleshooting playbook, Discord binding, Active task threads, Related.

## Task page — required frontmatter

```yaml
---
id: <6-char>
project: <Project Name>
project_id: <project's 6-char id>
area: <area-name>                # must match one of the project's areas
parent_id: null                  # or <epic's id> if this task is a subtask of an epic
thread: null                     # set by `ghost butler dispatch` on dispatch
cli: <claude|codex|copilot|opencode>
status: draft                    # see lifecycle below
created: YYYY-MM-DD
dispatched: null                 # set on dispatch
dispatch_msg_id: null            # set on dispatch
owner: null                      # set on dispatch (butler that dispatched it)
completed: null                  # set on terminal state (done | cancelled)
personas: [<persona-1>, <persona-2>, ...]
test_script: <filename of .test.sh alongside, or null if none>
---
```

Field semantics worth pinning down:

- **`thread:`** — leave `null` (or omit) at author time. The dispatch tool fills it atomically with a markdown link of the form `"[<title>](https://discord.com/channels/<guild_id>/<thread_id>)"`.
- **`dispatched:`, `dispatch_msg_id:`** — also written by the dispatch tool. Don't pre-fill.
- **`owner:`** — name of the butler that dispatched the task (from `ghost butler whoami`'s `outgoing_prefix_user`, e.g. `weiliu-algo-data`, `kathy`). Auto-filled by `ghost butler dispatch`; don't pre-fill manually. **Permanent record of who dispatched** — re-assigning a task to a different butler is operator-driven and out of scope of this field.
- **`account:`** — **dispatcher-written record (output only)** — like `thread:`/`dispatched:`/`owner:`, it is written by the dispatch tool, **not authored**. Leave it absent. On dispatch, `ghost butler dispatch` auto-balances via the local-JSONL load-balancer and writes the chosen account here, so the page records where it ran. The dispatcher **does not read this field back** — a value sitting here never influences account selection (it's overwritten with whatever account is actually used on the next dispatch). To force a specific account, pass `--account <name>` at dispatch time (a conscious per-dispatch act). **Never hand-set `account:` on a task page** — it has no effect on routing and only risks looking authoritative when it isn't.
- **`model:`** — optional, **author-readable input** (unlike `account:`). Declares the model grade the task needs (alias like `sonnet`/`haiku`, or a full model ID) — model grade is a property of the task, so the author is the right person to set it. Resolution precedence at dispatch: `--model` flag > this field > none (the account's default model). The dispatcher stamps the model actually used back into this field (a flag override overwrites a stale value). Applies to fresh claude launches only; non-claude CLIs ignore it.
- **`completed:`** — set to today's ISO date when status flips to `done` or `cancelled`. Both terminal states get it.
- **`personas:`** — required for any substantive task. If the field is omitted entirely, the default is `[senior engineer]`; lint warns on missing.

### Body sections

Goal · Context · Acceptance criteria · Out-of-scope · Dispatch message · **Test plan** · Updates · Result · Acceptance review.

A `Why:` line or subsection under Goal is fine for non-obvious motivation. `Acceptance review` is filled after the diff lands and tests pass — what worked, what surprised, what to do differently next time.

## Status lifecycle

```
draft ──► dispatched (plan-phase) ──► in-progress ──► review ──► done
   │              │                                                  │
   │              └──────────────► cancelled ◄─────────┬─────────────┘
   └──────────────────────────────► cancelled ◄────────┘
```

| Status | Meaning | Who sets it |
|---|---|---|
| `draft` | Task page exists; not yet dispatched | Task author |
| `dispatched (plan-phase)` | Dispatched; awaiting plan from executor | Dispatch tool |
| `in-progress` | Plan approved; executor implementing | Operator on plan greenlight |
| `review` | Executor has produced a diff; awaiting operator acceptance | Executor |
| `done` | Diff applied, tests pass, acceptance review filled | Operator |
| `cancelled` | Task abandoned (from any prior state) | Operator |

Terminal states (`done`, `cancelled`) require `completed:` set to the date of transition, and the file/folder is `git mv`'d to `archive/<area>/<month>/`.

## Atomic vs epic

- **Atomic task** = a single `.md` file. Use when the task is self-contained — no spec needed, no subtasks.
- **Epic task** = a folder named `<YYYY-MM-DD>-<id>-<title>/` containing `README.md` (overview), an optional `spec.md` (locked before any subtask dispatch), and one or more subtask `.md` files.

**Promotion rule:** start as atomic. If a task grows complex enough to need a spec or subtasks, promote it — move the `.md` file to `<...>/README.md` inside a new folder of the same name (minus `.md`).

## Spec convention (epic tasks)

For epic tasks where the design isn't obvious:

1. Create the epic folder with `README.md` stating the goal
2. **First action:** dispatch a "write spec" task. `spec.md` gets locked before any implementation subtasks
3. Review `spec.md` with the operator. Iterate until approved
4. Break the spec into atomic subtasks (each its own `.md` inside the epic folder, with `parent_id:` pointing at the epic's id)
5. Dispatch subtasks one-by-one or in parallel, as appropriate
6. Final acceptance is against the spec, not against the original dispatch message

## Personas

`personas:` is a **list** (always plural even with one item) of expert lenses the dispatched session should embody simultaneously. The dispatch message must begin with a persona prelude:

> You are simultaneously: **a senior X**, **a senior Y**, **a senior Z**. Approach this task with all these lenses at once — flag tradeoffs as a PM would, design as the architect, audit risks as the reviewer.

This is required for substantive tasks. A task with the field omitted entirely defaults to `[senior engineer]`; lint warns when the field is missing.

### Catalog (extend as needed)

| Task type | Recommended personas |
|---|---|
| Spec / architecture design | senior software architect + senior product manager |
| Implementation | senior engineer in `<stack>` + senior code reviewer |
| Bug fix / debug | senior debugger + senior engineer |
| Research / selection | senior researcher + senior product manager |
| Documentation | technical writer + subject expert |
| Tooling / CLI | senior platform engineer |
| Security audit | senior security reviewer + senior engineer |

### Phase-shift personas (optional)

Plan phase and implementation phase can use different personas:

- Plan phase: heavy on architect + PM
- Diff phase: heavy on engineer + reviewer

Document either way in the task page's `Updates` section so future readers see the reasoning.

## Automated tests

A task with externally-observable behavior **must** have an automated, re-runnable test before its status can flip to `done`.

- Test file lives alongside the task `.md`, named `<task-filename>.test.sh` (or `.test.py`)
- Test creates and cleans up its own throwaway state (e.g. test threads in Discord); no shared fixtures
- Pass criterion: process exits 0; failures print which case failed and what was expected
- Test gets `git mv`'d to archive together with the task `.md`
- The test serves as a permanent regression guard

Canonical pattern: a bash matrix of cases composing ghost CLI verbs (e.g. `ghost discord thread create` + `ghost butler send` + `ghost discord thread read`) with grep-based assertions and trap-based cleanup.

### Why required

Manual verification doesn't compound: each future change re-tests by hand, slowly, error-prone. An automated test means `bash <file>.test.sh` confirms the feature still works after any future patch. The convention is what makes the system durable as more tasks pile in.

### Exception

Some tasks have no externally-observable behavior to verify — a doc rewrite, a rename, a folder reorg. For these, set `test_script: null` and note "purely descriptive task — no externally-observable behavior to verify" in the `Acceptance review` section.

## Index files

- `Active.md` — list of links to active projects
- `Backlog.md` — projects not yet started
- `Archive.md` — completed / shelved projects

When a new project or epic is created, link it from the appropriate index.

## Lint invariants

The schema imposes these frontmatter coherence rules. A vault's lint tool should check them across `Projects/*/tasks/**/*.md` and `Projects/*/archive/**/*.md`:

- `status: dispatched*` ⇒ `thread:`, `dispatched:`, `dispatch_msg_id:` all present and non-empty
- `status: done` or `cancelled` ⇒ `completed:` present
- `status: done` and `test_script:` not `null` ⇒ the named test file exists alongside the task `.md`
- `personas:` is set (warn if missing — schema requires it for substantive tasks)
- `owner:` is set on any task with `status` ≥ `dispatched (plan-phase)` (warn if missing — populated automatically by `ghost butler dispatch`; legacy rows can be repaired with `ghost butler backfill-owners`)
- `area:` is one of the project's declared `areas:` in its README

## Creating a new project / task

**New project:**

1. Generate a 6-char ID (recipe above)
2. Create `Projects/<Name>/README.md` with the required frontmatter and an `areas:` list (the subsystems you'll categorize tasks under). `tasks/` and `archive/` get created when the first task arrives.
3. Link the project from `Active.md` (or `Backlog.md` / `Archive.md`)

**New task:**

1. Generate a 6-char ID
2. Create the file at `Projects/<P>/tasks/<area>/<YYYY-MM>/<YYYY-MM-DD>-<id>-<title>.md` with the required frontmatter (`status: draft`)
3. Fill the body: Goal, Context, Acceptance criteria, Out-of-scope, Dispatch message, Test plan
4. Dispatch via `ghost butler dispatch <task-id>` — see [dispatch lifecycle](dispatch-lifecycle.md)
