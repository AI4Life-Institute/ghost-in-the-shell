# Design — add-dispatch-model-pin

## Context

The change spans three layers (butler CLI → Discord `/bind` message → engine
launch command) and deliberately mirrors the existing `--account` plumbing
introduced by `add-multi-account-hotswap` / task [[gbraq8]]. The interesting
decisions are where it does NOT mirror `--account`.

## Goals / Non-Goals

- Goals: pin the model for a freshly dispatched claude worker in one step;
  let task authors declare the model grade on the task page.
- Non-Goals: per-binding model persistence; model hot-switching of a live
  session (already covered by the `/model` command, `engine.py:2134`);
  model support for codex/copilot/opencode bases; auto-selection of a model
  by task complexity.

## Decisions

- **Task-page `model:` is READ on input (unlike `account:`).**
  `account:` is a write-only record because account choice is an
  infrastructure concern (load balancing) that must not be steered by a
  stale page value. Model grade, by contrast, is a property of the task
  itself ("this only needs sonnet") — the task author is the right person
  to declare it, so the page field participates in resolution:
  flag > page field > none.
- **Stamp the resolved model back to the page.** Mirrors `account:`
  stamping so the page faithfully records what the last dispatch actually
  used (relevant when the flag overrode the page value).
- **Fresh launches only.** All four respawn/resume call sites of
  `build_launch_command` pass a `session_id`, and `claude --resume`
  restores the session's own model; injecting `--model` there could
  silently override a mid-session `/model` switch. So the append happens
  in `_create_bind` next to `_append_permission_flag` (`engine.py:640`),
  not inside `build_launch_command` — resume paths stay untouched by
  construction.
- **Charset validation, not allowlist.** Claude Code accepts stable
  aliases and arbitrary full model IDs; an allowlist would drift as models
  ship (the same reasoning as `MODEL_HELP` in `engine.py:43`). The name is
  embedded in a shell command string, so validate
  `^[A-Za-z0-9][A-Za-z0-9._-]*$` (reject anything shell-meta) and bound
  the length. Helper lives next to `validate_account_name` in
  `core/account.py`; both dispatch (fail fast, before thread creation) and
  the `/bind` parser (defense in depth) call it.
- **Non-claude bases ignore the model.** Same silent-ignore semantics as
  `claude_account`; codex/copilot have their own model mechanisms.

## Risks / Trade-offs

- Stale page `model:` silently downgrades a re-dispatched task → mitigated
  by the dispatch summary printing a `model:` line with its source
  (`flag` / `task page`), same pattern as the `account:` line.
- An invalid-but-charset-clean name (e.g. typo `sonet`) reaches the claude
  CLI, which errors at startup inside tmux → visible in the thread via the
  normal output monitor; identical failure mode to a bad name passed to
  the existing `/model` command. Accepted.

## Migration Plan

Purely additive; no existing pages or bindings change. Rollback = remove
the flag/field handling; stamped `model:` lines on task pages are inert.

## Open Questions

None.
