## ADDED Requirements

### Requirement: Git Worktree Isolation
The system SHALL support creating sessions in isolated git worktrees. This capability MUST be available for `/bind`, `/fork`, and thread auto-session. When worktree mode is used, the CLI session works on an independent copy of the repository so multiple sessions do not interfere with each other.

#### Scenario: /bind with worktree option
- **WHEN** a user runs `/bind /path/to/repo` with `worktree=True`
- **AND** the path is a git repository
- **THEN** a new git worktree is created
- **AND** the CLI session starts in the worktree directory
- **AND** the binding's `work_dir` records the worktree path

#### Scenario: /bind with worktree on non-git directory
- **WHEN** a user runs `/bind /path/to/dir` with `worktree=True`
- **AND** the path is not a git repository
- **THEN** the worktree option is ignored
- **AND** the CLI session starts in the original directory

#### Scenario: Session killed with worktree
- **WHEN** a session using a worktree is killed via `/kill`
- **THEN** the tmux window is killed
- **AND** the git worktree is removed via `git worktree remove`
- **AND** the binding is removed
