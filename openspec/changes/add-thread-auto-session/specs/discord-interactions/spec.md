## ADDED Requirements

### Requirement: Thread Command
The system SHALL provide a `/thread` command that creates a Discord thread and a new CLI session under the current bound channel. The new session MUST share the parent channel's working directory and CLI type. The user's message SHALL be forwarded as the initial prompt to the new session.

#### Scenario: /thread with message
- **WHEN** a user runs `/thread "fix the login bug"` in a bound channel
- **THEN** a Discord thread is created
- **AND** a new tmux window + CLI session is started in the parent's `work_dir`
- **AND** a child binding is stored with `parent_channel_id` pointing to the parent channel
- **AND** "fix the login bug" is sent as the first input to the new session

#### Scenario: /thread in unbound channel
- **WHEN** a user runs `/thread` in a channel with no active binding
- **THEN** an error message is returned

### Requirement: Thread Auto-Detection
When a user creates a Discord thread directly (not via `/thread`) under a bound channel, the system SHALL automatically create a new CLI session for that thread using the same logic as the `/thread` command. The thread's starter message SHALL be the initial prompt.

#### Scenario: Discord thread created under bound channel
- **WHEN** a Discord thread is created under a channel that has an active binding
- **THEN** a new session is automatically created sharing the parent's `work_dir`
- **AND** the thread's starter message is sent as the first input

#### Scenario: Discord thread created under unbound channel
- **WHEN** a Discord thread is created under a channel with no active binding
- **THEN** no session is created

## MODIFIED Requirements

### Requirement: Fork Command
The `/fork` command SHALL create a git worktree for code isolation. It creates a Discord thread with a new CLI session running in the worktree directory. The worktree is automatically cleaned up when the session is killed. **BREAKING**: the `subdir` parameter is removed; `/fork` now requires a git repository.

#### Scenario: /fork in git repository
- **WHEN** a user runs `/fork "refactor auth"` in a channel bound to a git repository
- **THEN** a git worktree is created
- **AND** a Discord thread + tmux window + CLI session is started in the worktree directory
- **AND** a child binding is stored with `parent_channel_id`

#### Scenario: /fork in non-git directory
- **WHEN** a user runs `/fork` in a channel bound to a non-git directory
- **THEN** an error message is returned explaining that fork requires a git repository

#### Scenario: Kill a forked session with clean worktree
- **WHEN** a forked session is killed via `/kill`
- **AND** the worktree has no uncommitted changes
- **THEN** the tmux window is killed
- **AND** the git worktree is removed
- **AND** the binding is removed

#### Scenario: Kill a forked session with dirty worktree
- **WHEN** a forked session is killed via `/kill`
- **AND** the worktree has uncommitted changes
- **THEN** the system SHALL warn the user and list the dirty files
- **AND** ask for confirmation before removing the worktree
- **IF** the user confirms, the worktree is removed
- **IF** the user declines, only the tmux window is killed and the worktree is preserved

### Requirement: Kill Command Lifecycle
When a parent channel is killed, the system SHALL also kill all child sessions (threads and forks) and clean up their resources including git worktrees.

#### Scenario: Kill parent with children
- **WHEN** `/kill` is run on a parent channel that has child thread/fork sessions
- **THEN** all child tmux windows are killed
- **AND** for each child with a worktree, the dirty-check and confirmation logic applies
- **AND** all child bindings are removed
- **AND** all child Discord threads are archived
