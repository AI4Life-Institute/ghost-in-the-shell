# Change: Add /thread Command + Redefine /fork as Worktree

## Why
当前 `/fork` 同时承担了"开 thread"和"子任务"两个语义，不够清晰。实际使用中有两种不同需求：
1. **轻量对话分支**：在同一个项目下开个新对话，共享目录，比如让 AI 同时处理两件事
2. **代码隔离分叉**：创建独立的工作副本（git worktree），互不干扰地改代码

这两个需求应该用不同的命令：`/thread` 负责轻量对话，`/fork` 负责代码隔离。

## What Changes

### A. `/thread` 新命令
- 在已绑定的 channel 下创建 Discord thread + 新 CLI session
- 共享父 channel 的 `work_dir` 和 `coding_cli`
- 用户输入的文字作为新 session 的初始 prompt
- 子 binding 设置 `parent_channel_id` 指向父 channel
- Discord 里直接开 thread 也自动触发同样逻辑

### B. `/fork` 改为 Worktree 模式
- **BREAKING**：`/fork` 不再接受 `subdir` 参数，改为创建 git worktree
- `git worktree add` 创建隔离副本，CLI session 在 worktree 中启动
- kill 时自动 `git worktree remove` 清理
- 非 git 目录报错（worktree 依赖 git）
- 随时可用，不限于有 thread 的场景

### C. 生命周期
- thread archived/deleted → 自动 kill 子 session
- 父 channel `/kill` → 连带清理所有子 thread session + worktree
- fork 的 worktree kill 时自动清理

## Impact
- Affected specs: discord-interactions
- Affected code: `src/gits/adapters/discord/bot.py` (`/thread` command, thread event listener), `src/gits/core/engine.py` (`handle_thread`, 重写 `handle_fork`)
