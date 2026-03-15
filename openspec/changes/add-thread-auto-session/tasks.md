## 1. /thread 命令
- [x] 1.1 Discord adapter 注册 `/thread` slash command（参数：message 文本）
- [x] 1.2 Engine 新增 `handle_thread(channel_id, message, interaction)` 方法
- [x] 1.3 从父 binding 继承 `work_dir`、`coding_cli`
- [x] 1.4 创建 Discord thread + tmux window + 子 binding（`parent_channel_id`）
- [x] 1.5 启动 PaneMonitor + JsonlMonitor 轮询
- [x] 1.6 bind 完成后将 message 作为首条 prompt 发送到新 session
- [x] 1.7 在 thread 中发送确认消息

## 2. Discord Thread 自动检测
- [x] 2.1 监听 `on_thread_create` 事件
- [x] 2.2 过滤：仅处理 parent channel 已绑定的 thread
- [x] 2.3 提取 starter_message，复用 `handle_thread` 逻辑

## 3. /fork 改为 Worktree
- [x] 3.1 新增 `_create_worktree(repo_dir, label)` 工具函数（`git worktree add`）
- [x] 3.2 新增 `_remove_worktree(worktree_path)` 工具函数（`git worktree remove`）
- [x] 3.3 重写 `handle_fork`：移除 `subdir` 参数，改为创建 worktree
- [x] 3.4 非 git 目录时报错提示
- [x] 3.5 `/fork` slash command 更新参数（移除 subdir）

## 4. Lifecycle Management
- [x] 4.1 `handle_kill` 检测 worktree：`git status --porcelain` 检查未提交改动，有则发 Discord 确认按钮，用户确认后才删除 worktree
- [x] 4.2 thread archived/deleted → 自动 kill 子 session
- [x] 4.3 父 channel `/kill` → 连带清理所有子 thread + fork session

## 5. Testing
- [x] 5.1 单元测试：`/thread` 创建子 binding + 首条消息转发
- [x] 5.2 单元测试：thread 事件过滤（已绑定 vs 未绑定 parent）
- [x] 5.3 单元测试：worktree 创建/删除
- [x] 5.4 单元测试：非 git 目录 fork 报错
- [x] 5.5 E2E：`/thread "fix the bug"` → thread 创建 → CLI 收到 "fix the bug"
- [x] 5.6 E2E：`/fork` → worktree 创建 → CLI 在隔离目录工作 → kill 后 worktree 清理
