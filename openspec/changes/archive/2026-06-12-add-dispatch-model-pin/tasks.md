# Tasks — add-dispatch-model-pin

## 1. Validation helper

- [x] 1.1 Add `validate_model_name(name: str) -> str` next to
      `validate_account_name` in `src/gits/core/account.py`
      (charset `^[A-Za-z0-9][A-Za-z0-9._-]*$`, length ≤ 128, clear ValueError)
- [x] 1.2 Unit tests: valid aliases (`sonnet`, `haiku`), full IDs
      (`claude-sonnet-4-6`), rejects shell metacharacters / spaces / empty

## 2. /bind plumbing (Discord adapter + engine)

- [x] 2.1 Parse `--model=<name>` in the butler `/bind` handler
      (`src/gits/adapters/discord/bot.py:550`), validate, reply with usage
      on bad value; extend the usage string at `bot.py:581`
- [x] 2.2 Thread `model: str | None = None` through
      `engine.handle_bind` → `_create_bind` (`src/gits/core/engine.py:464,610`)
- [x] 2.3 In `_create_bind`, append `--model <name>` to the launch command
      for claude-base fresh launches only, next to the
      `_append_permission_flag` call (`engine.py:644`); ignore for
      non-claude bases; never on resume paths
- [x] 2.4 Tests: fresh claude launch command carries the flag; codex
      ignores; resume command unaffected; invalid value replies usage

## 3. Dispatch flag + task-page field

- [x] 3.1 Add `--model <name>` to the dispatch argparse block
      (`src/gits/butler/butler_cli.py:313`) with help text documenting the
      precedence (flag > task-page `model:` > none)
- [x] 3.2 Resolve in `dispatch_task` (`src/gits/butler/dispatch_task.py:535`):
      flag > `fm.get("model")` > None; validate before thread creation;
      append `--model=<name>` to `bind_msg` (`dispatch_task.py:605`)
- [x] 3.3 Stamp resolved `model:` into the writeback dict (next to the
      `account:` stamp, `dispatch_task.py:657`) only when a model was used
- [x] 3.4 Print `model: <name> (source: flag|task page)` in the dispatch
      summary, mirroring the `account:` line
- [x] 3.5 Tests: precedence matrix (flag/page/neither), fail-fast on bad
      name before any REST call, writeback stamping, no stamp when absent

## 4. Validation & docs

- [x] 4.1 `openspec validate add-dispatch-model-pin --strict` passes
- [x] 4.2 `uv run ruff check` and `uv run pytest` pass (1030 tests; the only
      new-code lint finding was fixed — remaining ruff hits in touched files
      pre-date this change)
- [x] 4.3 Update butler dispatch `--help` epilog / docs: `docs/task-schema.md`
      gains the `model:` field semantics; `skills/butler/SKILL.md` documents
      `--model` and its precedence
