## ADDED Requirements

### Requirement: Thread Auto-Session
When a user creates a new Discord thread under a bound channel, the system SHALL automatically create a new CLI session for that thread. The new session MUST use the same working directory and CLI type as the parent channel's binding. The thread's first message SHALL be forwarded as the initial prompt to the new session. By default the new session shares the parent's directory; a git worktree is only used when the user explicitly requests it.

#### Scenario: Thread created under bound channel (default, shared directory)
- **WHEN** a Discord thread is created under a channel that has an active binding
- **THEN** a new tmux window is created sharing the parent's `work_dir`
- **AND** a child binding is stored with `parent_channel_id` pointing to the parent channel
- **AND** the thread's starter message is sent as the first input to the new session

#### Scenario: Thread created under unbound channel
- **WHEN** a Discord thread is created under a channel that has no active binding
- **THEN** no session is created and no binding is stored

#### Scenario: Thread archived or deleted
- **WHEN** a bound thread is archived or deleted in Discord
- **THEN** the corresponding tmux window is killed, worktree is removed (if any), and the binding is removed

#### Scenario: Parent channel unbound
- **WHEN** a parent channel is unbound via `/kill`
- **THEN** all child thread sessions are also killed, their worktrees removed, and bindings removed
