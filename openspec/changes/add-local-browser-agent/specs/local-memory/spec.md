## ADDED Requirements

### Requirement: Local SQLite Database
The system SHALL maintain a local SQLite database at `~/.gits/gits.db` that persists all agent tasks, step-by-step action logs, working memory, and extracted artifacts across app restarts. No data leaves the user's machine.

#### Scenario: Database created on first launch
- **WHEN** GITS starts for the first time
- **THEN** `~/.gits/gits.db` is created with the full schema (tasks, steps, observations, artifacts, browser_sessions tables)
- **AND** the app operates normally without any user action

#### Scenario: Data survives app restart
- **WHEN** the user restarts the app after running a browser agent task
- **THEN** the Tasks view shows all previous tasks with their steps and artifacts intact
- **AND** an incomplete task can be resumed from where it left off

### Requirement: Task Schema
The system SHALL store tasks with the following structure, sufficient to resume or audit any agent run:

```sql
CREATE TABLE tasks (
  id          TEXT PRIMARY KEY,   -- ulid
  goal        TEXT NOT NULL,       -- user's natural-language goal
  status      TEXT NOT NULL,       -- queued | running | done | failed | needs_review
  profile     TEXT,                -- Chrome profile name used
  created_at  INTEGER NOT NULL,    -- unix ms
  updated_at  INTEGER NOT NULL,
  summary     TEXT                 -- agent's final summary on completion
);

CREATE TABLE steps (
  id          TEXT PRIMARY KEY,
  task_id     TEXT NOT NULL REFERENCES tasks(id),
  seq         INTEGER NOT NULL,    -- step number within task
  action      TEXT NOT NULL,       -- navigate | snapshot | click | type | evaluate | think | done
  input       TEXT,                -- JSON: action parameters
  output      TEXT,                -- JSON: action result
  ts          INTEGER NOT NULL     -- unix ms
);

CREATE TABLE artifacts (
  id          TEXT PRIMARY KEY,
  task_id     TEXT NOT NULL REFERENCES tasks(id),
  type        TEXT NOT NULL,       -- pdf | csv | text | screenshot
  filename    TEXT NOT NULL,
  path        TEXT NOT NULL,       -- absolute path under ~/.gits/artifacts/
  size_bytes  INTEGER,
  created_at  INTEGER NOT NULL
);
```

#### Scenario: Step log captures full action context
- **WHEN** the browser agent executes a `click` action
- **THEN** a row is inserted into `steps` with `action='click'`, `input='{"ref":"e42","label":"Sign in button"}'`, and `output='{"ok":true}'`
- **AND** the Tasks view can replay the full action history

### Requirement: Working Memory
The system SHALL provide a key-value working memory scoped to each task, stored in SQLite, that the agent can read and write during execution to track intermediate state (e.g. extracted tokens, scraped values, progress checkpoints).

#### Scenario: Agent stores a JWT mid-task
- **WHEN** the agent extracts a JWT from `localStorage` during a task
- **THEN** it writes `memory.set(task_id, 'jwt', token)` which inserts/upserts into the `observations` table
- **AND** on the next loop iteration, the agent can read back `memory.get(task_id, 'jwt')` without re-extracting from the browser

#### Scenario: Memory is cleared on task completion
- **WHEN** a task reaches `done` or `failed` status
- **THEN** sensitive working memory keys (those flagged `sensitive=true`) are deleted from the database
- **AND** non-sensitive keys are retained for audit purposes

### Requirement: Chat-to-Task Integration
The system SHALL allow users to submit browser agent tasks directly from the Chat view by prefixing a message with `/browse` or by the AI detecting a browseable goal and offering to launch a browser task.

#### Scenario: User submits a browse command from chat
- **WHEN** the user types `/browse find the current BTC price on CoinGecko`
- **THEN** a new task is created in the database with that goal
- **AND** the Tasks view becomes active showing the task starting
- **AND** a chat message confirms "Starting browser task: find the current BTC price on CoinGecko"

#### Scenario: AI offers to browse on user's behalf
- **WHEN** the user asks something in chat that requires live web data (e.g. "what's the AAPL stock price?")
- **THEN** Claude may respond with a "Want me to look that up?" suggestion with a one-click "Start browsing" button in the bubble
