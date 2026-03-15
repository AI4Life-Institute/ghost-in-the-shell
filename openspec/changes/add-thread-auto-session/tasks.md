## 1. Git Worktree Support（通用能力）
- [ ] 1.1 新增 `_create_worktree(repo_dir, label)` 工具函数：调用 `git worktree add`，返回 worktree 路径
- [ ] 1.2 新增 `_remove_worktree(worktree_path)` 工具函数：调用 `git worktree remove`
- [ ] 1.3 `_create_bind()` 接受 `worktree: bool` 参数，为 True 时先创建 worktree 再在其中启动 CLI
- [ ] 1.4 Binding 的 `work_dir` 记录实际工作路径（worktree 路径或原始路径）
- [ ] 1.5 `handle_kill()` 中检测 worktree 路径，kill 时自动 `git worktree remove`
- [ ] 1.6 非 git 目录时忽略 worktree 选项，fallback 到共享目录

## 2. /bind 和 /fork 集成 Worktree
- [ ] 2.1 `/bind` slash command 新增 `worktree` boolean 选项（默认 False）
- [ ] 2.2 `/fork` slash command 新增 `worktree` boolean 选项（默认 False）
- [ ] 2.3 选项传递到 Engine 的 `handle_bind()` / `handle_fork()`

## 3. Discord Thread Event Handling
- [ ] 3.1 在 `DiscordAdapter` 中监听 `on_thread_create` 事件
- [ ] 3.2 过滤：仅处理 parent channel 已绑定的 thread
- [ ] 3.3 提取 thread 首条消息文本（starter_message）

## 4. Thread Auto-Bind Logic
- [ ] 4.1 Engine 新增 `handle_thread_created(thread_id, parent_channel_id, initial_text)` 方法
- [ ] 4.2 从父 binding 继承 `work_dir`、`coding_cli` 配置
- [ ] 4.3 默认共享父目录；仅当用户明确指定 worktree 时才创建 worktree
- [ ] 4.4 创建子 binding，设置 `parent_channel_id`
- [ ] 4.5 启动 PaneMonitor + JsonlMonitor 轮询
- [ ] 4.6 bind 完成后，将 thread 首条消息作为第一个 prompt 发送到新 session
- [ ] 4.7 在 Discord thread 中发送确认消息

## 5. Lifecycle Management
- [ ] 5.1 thread archived/deleted 时自动 kill 对应 session + 清理 worktree
- [ ] 5.2 父 channel unbind 时，同时清理所有子 thread session + worktree

## 6. Testing
- [ ] 6.1 单元测试：worktree 创建/删除
- [ ] 6.2 单元测试：thread 事件过滤（已绑定 vs 未绑定 parent）
- [ ] 6.3 单元测试：子 binding 继承父配置 + worktree 路径
- [ ] 6.4 E2E：开 thread → auto-bind with worktree → 首条消息到达 CLI → 输出回到 thread
- [ ] 6.5 E2E：`/bind --worktree` → CLI 在 worktree 目录工作
