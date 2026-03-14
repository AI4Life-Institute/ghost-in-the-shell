## 1. Terminal Prompt Detection
- [ ] 1.1 `src/gits/core/terminal_parser.py` — 正则解析 Claude Code 交互提示
- [ ] 1.2 检测多选提示：`❯ 1. Yes  2. Yes, allow...  3. No` → 提取选项列表
- [ ] 1.3 检测工具调用上下文：Bash command / Edit file / Read file 等描述块
- [ ] 1.4 检测状态行：idle (❯ prompt) / busy (spinner/Thinking) / waiting (选项提示)
- [ ] 1.5 单元测试：每种提示类型的检测

## 2. Discord Prompt Buttons
- [ ] 2.1 `PromptBridge` — 将检测到的选项转为 Discord Button 行
- [ ] 2.2 显示工具上下文（命令/文件名）+ 选项按钮
- [ ] 2.3 按钮点击 → 发送数字键选择对应选项到 tmux
- [ ] 2.4 Interrupt 按钮（Escape）和 Abort 按钮（Ctrl-C）
- [ ] 2.5 单元测试

## 3. Pane Output Polling
- [ ] 3.1 `PanePoller` in `src/gits/core/monitor.py` — 定时 capture-pane + diff
- [ ] 3.2 过滤掉状态行/spinner 噪音，只推送有意义的新输出
- [ ] 3.3 检测到交互提示时触发 Prompt Bridge（步骤 2）
- [ ] 3.4 引擎生命周期：bind 时启动，unbind/kill 时停止
- [ ] 3.5 单元测试

## 4. JSONL Output Polling
- [ ] 4.1 `JsonlPoller` in `src/gits/core/monitor.py` — 字节偏移增量读取
- [ ] 4.2 解析 assistant.text → 推送文本；tool_use → 推送工具调用摘要
- [ ] 4.3 mtime 缓存跳过未变文件
- [ ] 4.4 单元测试

## 5. Message Formatting + Streaming
- [ ] 5.1 `MessageChunker` in `src/gits/adapters/discord/formatter.py` — 2000 字符分块
- [ ] 5.2 代码块感知：跨块自动闭合/重开 ``` fence
- [ ] 5.3 Debounced message edit — 300ms 内批量更新，编辑已有消息而非发新消息
- [ ] 5.4 Discord 429 rate limit → 指数退避
- [ ] 5.5 单元测试

## 6. Integration + E2E
- [ ] 6.1 Engine 中串联：Monitor → Parser → PromptBridge/Formatter → Discord
- [ ] 6.2 按钮回调链路：Discord button click → Engine → tmux send_keys
- [ ] 6.3 本机 E2E 测试：bind → 发消息 → Claude 回复出现在 Discord → 权限提示变按钮 → 点击按钮
