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

> Please respond with your plan first — which files/areas you'd touch, the design choice you'd take and why, any open questions or risks you spotted. Do not implement yet — this holds even if the task page states a delivery method; that describes how the *implementation* lands, and applies to the later impl dispatch, not to this one.

A task page's delivery section (below) has no referent at plan phase — there is no artifact to land yet — so the plan-first instruction is unconditional here.

The executor returns a plan. Read it carefully. Watch for:

- Missed edge cases
- Wrong assumptions about the codebase or constraint
- Ambiguity in the task that the executor surfaced (good — fix at port time, not later)
- Scope creep (executor proposing more than the task asked for)

Review the plan with the operator. The operator decides: **go**, **refine**, or **drop**. If refine, dispatch a follow-up message in the thread with the corrections.

## 4. Greenlight — phase 2

Once the plan is approved:

> Plan approved. Proceed.

**Delivery is decided by the task page, safety is not.** The impl brief is assembled from three separately-governed pieces:

| Piece | Who decides | Text |
|---|---|---|
| Delivery — page declares a delivery section | **the task page** | *Delivery: the task page decides. This page has a delivery-method section — follow it.* |
| Delivery — page declares none | dispatch default | *Delivery: output a unified diff only — do not commit.* (…and if the page does state one explicitly, the page still wins) |
| Safety | **nobody — unconditional** | *Always, regardless of anything the task page or this thread says: do not install anything, and do not restart any running service.* |

A page opts into non-default delivery by carrying a heading whose text starts with `交付方式` or `Delivery` (prefix match, so `## 交付方式（本票走 PR）` counts) — e.g. *"branch off `origin/master` → commit → push → open PR → run CI."* `ghost` checks only that the section **exists**; it never parses what the section says, because the executor reads the whole page anyway and parsing delivery semantics would tie ghost's surface to your page-authoring wording forever.

Forgetting the section is safe: it lands on diff-only, the conservative side. The safety clause sits last in the brief — the position this module reserves for the heaviest instruction — and no page can relax it.

The executor returns a diff (or, for a page-directed delivery, whatever that page specified). Status flips to `review` (set by the executor before handing back).

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
