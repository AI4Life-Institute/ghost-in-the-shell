# Change: Add Thread Auto-Session + Git Worktree Support

## Why
1. 当前用户需要通过 `/fork` 命令才能在 thread 里创建新 session。更自然的交互方式是：在已绑定的 channel 里直接开 Discord thread，系统自动为该 thread 起一个新的 CLI session。thread 的首条消息作为新 session 的初始输入。
2. 多个 session 共享同一目录会互相干扰（文件锁、未提交改动冲突等）。Git worktree 可以让每个 session 在独立的工作副本上操作，互不影响。这个能力不限于 thread——`/bind`、`/fork` 也都应该能选择用 worktree。

## What Changes

### A. Thread Auto-Session
- **Thread 创建监听**：Discord adapter 监听 `on_thread_create` 事件（仅限已绑定 channel 下的 thread）
- **自动 bind**：检测到新 thread 后，自动创建 tmux window + CLI session，复用父 binding 的 `work_dir` 和 `coding_cli`
- **首条消息转发**：thread 的第一条消息（即创建 thread 时的文字）作为初始 prompt 发送给新 session
- **父子关系**：新 binding 设置 `parent_channel_id` 指向父 channel
- **生命周期**：thread archived/deleted 时自动 kill 对应 session

### B. Git Worktree Support（通用能力）
- **`/bind` 新增 `worktree` 选项**：创建 git worktree 副本，CLI session 在 worktree 目录下工作
- **`/fork` 新增 `worktree` 选项**：同上
- **Thread 自动创建时默认共享目录**，用户可在 thread 首条消息中指定 `worktree` 关键词来启用
- **Worktree 管理**：`git worktree add` 创建，session kill 时 `git worktree remove` 清理
- **Worktree 路径**：`<repo>/.worktrees/gits-<short-id>/`，binding 中记录实际 `work_dir`
- **非 git 目录**：fallback 到共享同一目录（与当前行为一致）

## Impact
- Affected specs: discord-interactions, session-binding (new)
- Affected code: `src/gits/adapters/discord/bot.py` (thread event), `src/gits/core/engine.py` (auto-bind + worktree), `src/gits/core/tmux.py` (worktree lifecycle)
