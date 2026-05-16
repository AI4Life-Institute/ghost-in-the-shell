# Dispatch lifecycle

How a task moves from "idea in a vault-like repo" to "diff applied and task archived." This is the workflow the butler session follows; the [task schema](task-schema.md) defines the artifacts it produces, and [role](role.md) explains why the butler dispatches rather than implements.

## 1. Write the task page in PM style

Author the task page (see [task schema](task-schema.md) for the frontmatter and body sections).

Write it like a PM ticket, not a tech lead spec:

- State the **WHAT** — the goal and acceptance criteria, observable from outside
- State the **WHY** when non-obvious — the constraint or motivation
- **Do NOT prescribe HOW** — no file paths, function names, specific code changes, or design choices. The executor reads the actual code and knows it better than the butler session does at dispatch time.
- **Do NOT pre-decide tradeoffs** — leave space for the executor to propose

A good task description reads like a Jira ticket: outcome-focused, technology-agnostic where possible.

## 2. Dispatch

```bash
ghost butler dispatch <task-id>
```

The dispatch verb's behavior:

- Reads the task page's frontmatter
- Creates a Discord thread under the operator's home channel (resolved from the worktree's `.butler.json`, not from the task page — your dispatch threads land under your channel regardless of which project the task is about)
- Posts the dispatch message as the first thread message
- Writes `thread:` / `dispatched:` / `dispatch_msg_id:` back into the task frontmatter **atomically** (rename-from-tmp; no partial writes)
- **Rolls back** on writeback failure — archives the just-created thread so the filesystem and Discord stay consistent
- Flips status `draft` → `dispatched (plan-phase)`
- Runs lint at the end; lint failure exits non-zero but leaves the dispatch landed

Raw Discord-primitive calls (creating threads and posting messages by hand to bypass this flow) are blocked by a PreToolUse hook that steers you back to `ghost butler dispatch`. See [role — hook-gated dispatch](role.md#hook-gated-dispatch).

## 3. Plan-first phase

The dispatch message ends with the plan-first instruction:

> Please respond with your plan first — which files/areas you'd touch, the design choice you'd take and why, any open questions or risks you spotted. Do not implement yet.

The executor returns a plan. Read it carefully. Watch for:

- Missed edge cases
- Wrong assumptions about the codebase or constraint
- Ambiguity in the task that the executor surfaced (good — fix at port time, not later)
- Scope creep (executor proposing more than the task asked for)

Review the plan with the operator. The operator decides: **go**, **refine**, or **drop**. If refine, dispatch a follow-up message in the thread with the corrections.

## 4. Greenlight — phase 2

Once the plan is approved:

> Plan approved. Proceed. Output a unified diff only — do not commit, do not install, do not restart anything.

The executor returns a diff. Status flips to `review` (set by the executor before handing back).

## 5. Acceptance

- **Apply the diff locally** (or in a separate session). Don't apply blind — read it against the acceptance criteria first.
- **Run the task's `.test.sh`** (or the manual verification noted in the Test plan section if `test_script: null`).
- **Fill the `Acceptance review` section** of the task page: what worked, what surprised, what to do differently next time. This is what makes the playbook compound over time — skipping it costs the whole project.
- Flip status to `done` and set `completed:` to today's ISO date.

Approving a plausible-looking diff without verifying defeats the whole loop. The diff is a proposal; acceptance is the contract.

## 6. Archive

When status is `done` or `cancelled`, `git mv` the task file (and its `.test.sh`, if any) from `tasks/<area>/<month>/` to `archive/<area>/<month>/`:

```bash
git mv "Projects/<P>/tasks/<area>/<month>/<task-file>.md" \
       "Projects/<P>/archive/<area>/<month>/"
git mv "Projects/<P>/tasks/<area>/<month>/<task-file>.test.sh" \
       "Projects/<P>/archive/<area>/<month>/"   # if present
```

For epic tasks, `git mv` the whole epic folder. The archive tree mirrors `tasks/`, so cross-references by ID and "what work happened in `<month>`" queries keep working uniformly across active + archive.
