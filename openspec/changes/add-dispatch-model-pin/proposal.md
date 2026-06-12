# Change: Pin the CLI model at dispatch time

## Why

Not every task needs the strongest (most expensive, most rate-limited) model.
Today a dispatched worker always launches with the account's default Claude
Code model; the only way to downgrade is to manually post `/model <name>`
into the thread after dispatch — racy (the CLI may still be booting, and tmux
text injection would land in the shell) and easy to forget. The operator
should be able to say "this is a sonnet-grade task" once, at dispatch time.

## What Changes

- `ghost butler dispatch` gains `--model <name>`; the task page gains an
  optional `model:` frontmatter field. Resolution precedence:
  flag > task-page field > none (account's CLI default).
- `/bind` gains a `--model=<name>` option (mirrors `--account=<name>`); the
  engine appends `--model <name>` to the claude launch command for fresh
  sessions. Non-claude CLI bases (codex/copilot/opencode) ignore it, same as
  `--account`.
- Model names are validated against a shell-safe charset before being
  embedded in the `/bind` message or the launch command.
- Dispatch stamps the resolved `model:` back into the task-page frontmatter
  (same write-only-record semantics as `account:` stamping) and prints a
  `model:` line in the dispatch summary.

## Impact

- Affected specs: `model-selection` (new capability)
- Affected code:
  - `src/gits/butler/butler_cli.py` (argparse: `--model`)
  - `src/gits/butler/dispatch_task.py` (resolution, `/bind` message, writeback, summary)
  - `src/gits/adapters/discord/bot.py` (`/bind` parser, usage string)
  - `src/gits/core/engine.py` (`handle_bind` / `_create_bind` plumbing, launch-command append)
  - `src/gits/core/account.py` (model-name validation helper alongside `validate_account_name`)
- Not affected: resume/respawn paths — `claude --resume` restores the
  session's own model; the pin only applies to fresh launches.
