# The org model — how it works

A concept-level overview of the org subsystem: the per-agent directory, the two
orthogonal layers that govern it, and the three ways nodes interact. Read this
first to build the mental model; then drop to the field-level rules.

> **Authority.** The org's *data and semantics* live in the **vault**, not in
> ghost. The single authority is `Org/<org>/_meta.yaml` (its `rules` block +
> header comments) plus the per-node `Org/<org>/<alias>.yaml` files. This doc
> *describes* that model; it never overrides it. For the file format and the
> exact lint invariants see the normative companion **`org-schema.md`**. Where
> the two disagree, `_meta.yaml` wins and this doc is the bug.

---

## 1. What the org tree IS

An org is a **directory of one-file-per-agent**:

```
Org/<org>/                  one directory per org (e.g. Org/ai4life/)
  _meta.yaml                org-level config + rules (NOT a node)
  <alias>.yaml              one file per agent/node
```

Each node is a short YAML file (`alias`, `channel_id`, `human`, `reports_to`,
`scope`, `desc`). The **filename stem == the node's `alias`**, so alias
uniqueness is enforced by the filesystem — you cannot have two `kathy.yaml` in
one directory. Multiple orgs coexist as sibling subdirectories, each with its
own `_meta.yaml` (own guild, category, root, rules); the resolver globs a single
org's `*.yaml` (skipping `_meta`) and rebuilds the tree from each file's
`reports_to`. There is no central registry file — **the directory *is* the
registry**, and `_meta.tree_snapshot` is only a derived view-cache of it.

## 2. Two orthogonal layers

The single most important idea: a node carries **two independent relationships**,
and conflating them is the classic mistake.

- **The reporting / dispatch tree — `reports_to`.** This answers *who may assign
  work to whom*. It is a strict tree rooted at the federation root, and
  authority flows **down it only**: a node may dispatch to a **descendant**,
  never to a peer and never laterally across subsidiaries. This is **AUTHORITY
  flow**.

- **`scope` — vault folder area-ownership.** This answers *who is the default
  owner of tasks under a given folder*. It is a set of vault-root-relative folder
  paths attached to a node. Resolution is **deepest-prefix-match wins**; a
  sub-folder may reassign ownership to a different node; and any single task may
  override with an explicit `owner:`. `scope` is **orthogonal** to the tree — a
  node deep in the hierarchy can own a top-level folder, and an umbrella node
  (like `weiliu`) can own nothing at all while its children own everything.

These two layers do **not** have to line up, and usually don't. Keep them
separate in your head: **`reports_to` is about authority; `scope` is about
folders.**

```
 reports_to tree  (AUTHORITY — dispatch flows DOWN this)        scope (FOLDER OWNERSHIP — orthogonal)
 ─────────────────────────────────────────────────────         ─────────────────────────────────────
 Ai4Life                       (root · chair: sharon)           (root = UNASSIGNED fallback)
 │
 ├── weiliu                                                     scope: []          ← umbrella, owns nothing directly
 │   ├── weiliu-ghost-dev                                       scope: Projects/Ghost/
 │   ├── weiliu-algo-dev                                        scope: Projects/Algo/ , Projects/Arena/
 │   │   ├── weiliu-algo-acct                                   scope: (none — inherits/ reassigns under algo-dev)
 │   │   ├── weiliu-algo-forum                                  scope: (none)
 │   │   └── weiliu-algo-proj                                   scope: (none)
 │   ├── weiliu-algo-data                                       scope: []
 │   ├── weiliu-vibo-dev                                        scope: []
 │   └── sync-man                                               scope: []  (职能 node, no task folder)
 └── … (ada-ghost, kathy, niki, harry, …)

 ── dispatch:  parent ──▶ descendant only (down the tree)
 ── send:      any ◀──▶ any  (ignores the tree entirely; the lateral/upward arrows are legal here, never for dispatch)
```

> *Illustrative.* This tree is a snapshot of the `weiliu` subtree for shape; the
> **live** structure is `Org/ai4life/_meta.yaml`'s `tree_snapshot` (derived) and
> the per-node `reports_to` fields (source). If the org reorgs, that YAML is
> right and this picture is stale — check the vault, not this page.

## 3. Three interaction primitives

Nodes interact three ways. Naming them and keeping them distinct is the whole
point of the model:

- **pull status** — read a node's channel, or `ghost butler read-thread <id>`.
  Observe a subordinate's progress **non-blocking**: no message is sent, nothing
  is assigned. Pulling flows **along the `reports_to` tree, the same direction as
  dispatch** — a parent can pull anything downstream of it; peers cannot see each
  other's threads. This is **OBSERVATION**.

- **send** — `ghost butler send <alias> "..."`. A plain message with **no task
  lifecycle**: no thread is opened, no frontmatter is written, nothing is bound.
  It is **UNCONSTRAINED**: any node may send to any node — up, down, or lateral.
  Subordinates report up, peers coordinate, a parent nudges without opening a
  task. This is **INFORMATION flow**.

- **dispatch** — `ghost butler dispatch <task-id>`. A formal task assignment: it
  opens a Discord thread, binds a fresh executor session to the task's repo
  worktree, and drives the two-phase plan → impl lifecycle with frontmatter
  writeback (see [dispatch lifecycle](dispatch-lifecycle.md)). It is
  **tree-bound**: a node may dispatch only to a strict descendant. This is
  **AUTHORITY / labor dispatch**.

The one-liner that ties it together, mirroring `_meta.yaml`'s `rules.dispatch`
vs `rules.messaging`:

> **Assigning work is tree-bound; talking is free.**

The descendant-only guard applies to **dispatch only** — `send` is never
constrained by the tree.

> **Not-yet-wired.** The dispatch guard (`can_dispatch` / `is_descendant`) and
> `owner_resolution` below ship as pure, tested functions but are **not yet
> wired into live dispatch** — that is the follow-up code task `dw3k9p`. This doc
> describes the *intended* model; current dispatch does not yet enforce it.

## 4. owner_resolution

When a task has **no explicit `owner:`**, ownership is resolved from `scope`:

1. Among all nodes' `scope` folders that are a path-prefix of the task's path,
   the **deepest** match wins — that node is the owner.
2. If no folder matches, walk **up the parent folders** of the task path and
   retry at each level.
3. If nothing matches all the way up, the owner falls back to the **root**,
   which is **UNASSIGNED**. The root is abstract (no `channel_id`), so dispatch
   **must not** auto-open a thread on it — the task is surfaced to the chair for
   an explicit `owner:` / `--reports-to`.
4. An explicit task `owner: <alias>` **overrides** all of the above.

Critical, and easy to get wrong: the "walk up" in step 2 is up the **parent
FOLDERS** of the path — **not** up the `reports_to` tree. Folder ancestry and
org hierarchy are unrelated here (this is the orthogonality of §2 again).

## 5. Responsibility split — vault data vs ghost code

- **The vault owns the DATA + semantics.** The `*.yaml` files, the `reports_to`
  structure, `scope` assignment, and `desc` are hand-edited by the PM/human
  directly in the vault. Org YAML is vault *data*, not source — mutating it there
  is expected and allowed.

- **Ghost owns the CODE** that reads, writes, and enforces: the resolver
  (`alias` → `channel_id`), `owner_resolution` (task path → owner), the dispatch
  guard (descendant-only), `onboard`, and `lint`.

Ghost's **only write path** into the org is `onboard`, and it only ever
**creates one new node file** (plus surgically regenerating the derived
`tree_snapshot`). Ghost **never** batch-rewrites or hand-edits an existing node's
`scope` / `reports_to` — re-parenting and reassignment are semantic decisions
that belong to the vault/human.

## 6. Enforcement — at the git boundary, not just at onboard

`ghost org lint` is the gate. It runs **both** inside `onboard` (a trial lint
before any write) **and** as a **commit-time git gate** in the vault (a
`.githooks/pre-commit` stanza + CI). A broken tree therefore **cannot be
committed**: duplicate alias, duplicate `channel_id`, dangling `reports_to`, a
second root, a cycle, overlapping `scope` folders, or `tree_snapshot` drift are
all hard rejects. For the full invariant list and the ready-to-paste pre-commit
stanza, see [org-schema.md → Invariants / Enforcement](org-schema.md).

## 7. onboarding — a new desk, not a one-off task

`ghost butler onboard <name>` creates a **new persistent subordinate**: a Discord
channel, a git worktree, and a new node file. `reports_to` **defaults to the
caller's own node** (resolved from the calling worktree's `.butler.json` home
channel), so onboarding from a parent's session wires the new hire under that
parent automatically; override with `--reports-to <alias>`.

Use the right tool: **a one-off piece of work is a thread (dispatch), not an
onboard.** Onboard is for standing up a node that will exist and take dispatches
over time. For the exact ordered step list (channel create, atomic node write +
lint + commit, worktree add, bind), see
[org-schema.md → `ghost butler onboard`](org-schema.md).

## 8. The send signature — `📨 **[butler:<alias>]**`

Outgoing butler messages are stamped with the sending node's **alias**, not the
human operator:

```
📨 **[butler:weiliu-ghost-dev]** …message…
```

The alias is used (not the human) because one human runs several nodes — the
bracket uniquely identifies the sending **desk**. It is also the **recognition
anchor**: the gateway keys on the `[butler:…]` bracket so vault-sent bot messages
are not dropped as noise.

---

**See also:** [org-schema.md](org-schema.md) (normative field format + lint) ·
[role.md](role.md) (the butler/PM role) ·
[dispatch-lifecycle.md](dispatch-lifecycle.md) ·
[task-schema.md](task-schema.md).
