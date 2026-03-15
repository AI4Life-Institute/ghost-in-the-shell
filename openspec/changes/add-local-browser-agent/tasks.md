<!-- SUPERSEDED NOTE (update-ghost-product-vision, 2026-03-15)
  Section 4 (Tasks View UI) is fully superseded by update-ghost-product-vision:
  browser-agent is now a type within Agents view, not a separate "Tasks" mode.
  Tasks 4.1-4.8 (Tasks nav item, task list, task detail, step streaming, artifact
  list, HITL input, New Task button, Chrome profile selector) are replaced by the
  Agents view fleet cards, agent detail drawer, and Chrome profile card design.
  Section 5 (Chat Integration): /browse command is replaced by /agent command.
  Sections 1-3 (OpenClaw, SQLite, Browser Agent Executor) and Section 6 (Testing)
  remain relevant — backend implementation is unaffected.
-->

## 1. OpenClaw Setup
- [ ] 1.1 Bundle OpenClaw Chrome extension (`.crx`) in `Contents/Resources/extensions/openclaw.crx`
- [ ] 1.2 Add `openclaw` CLI binary to `Contents/Helpers/openclaw` (or detect system-installed version)
- [ ] 1.3 Add Setup Wizard step: detect `openclaw doctor` → if missing, show "Install Extension" button → open Chrome extensions page with `.crx` pre-loaded
- [ ] 1.4 Verify `openclaw browser create-profile --name gits-agent --color #6366f1` works from within `.app`

## 2. SQLite Storage Layer
- [ ] 2.1 Create `src/gits/storage/sqlite.py` — connection pool, schema migrations, `TaskRepo`, `StepRepo`, `ArtifactRepo`, `MemoryRepo`
- [ ] 2.2 Implement schema from spec (tasks, steps, artifacts, observations tables)
- [ ] 2.3 Add migration system: version table + `migrate()` function that runs new DDL safely
- [ ] 2.4 Write unit tests for all repo methods using an in-memory SQLite DB

## 3. Browser Agent Executor
- [ ] 3.1 Create `src/gits/adapters/browser/openclaw.py` — wrap each primitive (navigate, snapshot, click, type, evaluate) as a Python function calling `openclaw` CLI via `subprocess`
- [ ] 3.2 Parse `snapshot --labels` output into `list[ElementRef]` with fields `role`, `label`, `ref`
- [ ] 3.3 Create `src/gits/adapters/browser/agent.py` — the think-act loop:
       - Build system prompt including current URL, snapshot, and working memory
       - Call Claude to pick next action (structured output: `{action, params, reasoning}`)
       - Execute action via `openclaw.py` primitives
       - Store step in SQLite
       - Repeat until `action == "done"` or `seq >= max_steps`
- [ ] 3.4 Handle human-in-the-loop: when Claude returns `action == "ask_user"`, pause task and emit event to UI
- [ ] 3.5 Save artifacts: detect file downloads, evaluate-extracted data → write to `~/.gits/artifacts/<task-id>/`
- [ ] 3.6 Add `/browse <goal>` command handler in the chat adapter

## 4. Tasks View (UI)
- [ ] 4.1 Add "Tasks" nav item to sidebar (between Chat and Dashboard)
- [ ] 4.2 Build task list: each task shows goal, status badge (queued/running/done/failed), elapsed time
- [ ] 4.3 Build task detail panel: step-by-step action log with action type icon, input summary, output summary, timestamp
- [ ] 4.4 Add live step streaming: backend emits step events → UI appends rows without refresh
- [ ] 4.5 Show artifact list per task: filename, type icon, size, "Show in Finder" button
- [ ] 4.6 Add "Human-in-the-loop" inline input: when task is paused, show a text field in the task detail for user to respond
- [ ] 4.7 Add "New Task" button with goal input field (alias for `/browse` in chat)
- [ ] 4.8 Add Chrome profile selector in new-task modal (calls `openclaw browser list-profiles`)

## 5. Chat Integration
- [ ] 5.1 Parse `/browse <goal>` in chat input → create task + switch to Tasks view
- [ ] 5.2 When AI response mentions a browseable action, optionally render a "Browse" CTA button in the chat bubble

## 6. Testing
- [ ] 6.1 Integration test: agent navigates to example.com, extracts `<h1>` text, saves as artifact
- [ ] 6.2 Integration test: agent handles a login wall (snapshot → fill form → submit → verify redirect)
- [ ] 6.3 Test SQLite migration: upgrade from schema v1 to v2 without data loss
- [ ] 6.4 Test human-in-the-loop: agent pauses, user responds, agent resumes
- [ ] 6.5 Test max_steps cutoff: task marked "needs_review" at step 30
