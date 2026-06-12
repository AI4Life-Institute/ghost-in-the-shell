# model-selection — delta for add-dispatch-model-pin

## ADDED Requirements

### Requirement: Dispatch Model Resolution

`ghost butler dispatch` SHALL accept a `--model <name>` flag and SHALL read
an optional `model:` task-page frontmatter field, resolving the model with
precedence: flag > task-page field > none. When neither is present, no model
option is emitted and the worker launches with the account's CLI default.
The dispatch summary SHALL print the resolved model and its source
(`flag` or `task page`) when one was used.

#### Scenario: Flag pins the model

- **WHEN** the operator runs `ghost butler dispatch abc123 --model sonnet`
- **THEN** the `/bind` message posted to the new thread includes `--model=sonnet`
- **AND** the dispatch summary prints `model: sonnet (source: flag)`

#### Scenario: Task page declares the model grade

- **WHEN** the task page frontmatter contains `model: sonnet` and the
  operator dispatches without `--model`
- **THEN** the `/bind` message includes `--model=sonnet`
- **AND** the dispatch summary prints `model: sonnet (source: task page)`

#### Scenario: Flag overrides the page field

- **WHEN** the task page contains `model: haiku` and the operator passes
  `--model opus`
- **THEN** the `/bind` message includes `--model=opus`

#### Scenario: No model anywhere

- **WHEN** neither the flag nor the page field is present
- **THEN** the `/bind` message contains no `--model=` token and the worker
  launches with the account's default model

### Requirement: Model Name Validation

Model names MUST match `^[A-Za-z0-9][A-Za-z0-9._-]*$` and a sane length
bound before being embedded in a `/bind` message or a launch command.
Dispatch MUST reject an invalid name before any thread is created; the
`/bind` parser MUST independently reject an invalid `--model=` value with a
usage reply.

#### Scenario: Invalid name fails fast at dispatch

- **WHEN** the operator runs `ghost butler dispatch abc123 --model 'sonnet; rm -rf /'`
- **THEN** dispatch exits non-zero with a validation error
- **AND** no Discord thread is created and the task page is unchanged

#### Scenario: Invalid name rejected at /bind

- **WHEN** a `/bind <path> claude --model=bad$name` message arrives
- **THEN** the bot replies with an invalid `--model` value error
- **AND** no binding is created

### Requirement: Bind Launches Claude With the Pinned Model

`/bind` SHALL accept a `--model=<name>` option. For a fresh claude-base
session the engine SHALL append `--model <name>` to the launch command.
Resume and respawn paths MUST NOT inject the model (the resumed session
keeps its own model). Non-claude CLI bases SHALL ignore the option, matching
`--account` semantics.

#### Scenario: Fresh bind launches with the model

- **WHEN** `/bind ~/proj claude --model=sonnet` creates a fresh session
- **THEN** the tmux window's launch command ends with `--model sonnet`

#### Scenario: Resume ignores the pin

- **WHEN** a binding with a pinned model is later resumed via `claude --resume <id>`
- **THEN** the resume command contains no `--model` injected by ghost

#### Scenario: Non-claude CLI ignores the model

- **WHEN** `/bind ~/proj codex --model=sonnet` is processed
- **THEN** the codex launch command is unchanged by the model option

### Requirement: Resolved Model Stamped on the Task Page

When a model was used for dispatch, the dispatcher SHALL write the resolved
name into the task page's `model:` frontmatter field during the existing
atomic writeback, overwriting any stale value, so the page records what the
most recent dispatch actually used. When no model was used, the field MUST
be left untouched.

#### Scenario: Flag value stamped over stale page value

- **WHEN** the page contains `model: haiku` and dispatch runs with `--model opus`
- **THEN** after writeback the page reads `model: opus`

#### Scenario: No stamp without a model

- **WHEN** dispatch runs with no flag and no page field
- **THEN** writeback adds no `model:` line
