# Change: Add Phase 2 — Output Push + Interactive Prompt Bridging

## Why
MVP 已通：Discord 文本消息→tmux 转发、手动截屏、slash commands 都能用。但体验是单向的——用户看不到 Claude Code 的回复，Claude Code 问问题时用户也无法在 Discord 里回答。Phase 2 补上这两个核心闭环。

## What Changes

### A. Output Push（Claude 输出 → Discord）
- **Pane 轮询**：定期 capture-pane，diff 检测新输出行，推送到 Discord
- **JSONL 轮询**：监控 `~/.claude/projects/<hash>/*.jsonl`，解析 assistant.text / tool_use / tool_result，结构化推送
- **消息分块**：超过 2000 字符自动分块，代码块感知（跨块自动闭合/重开 fence）
- **流式编辑**：debounced edit 已有消息，避免消息轰炸

### B. Interactive Prompt Bridge（Claude 提问 → Discord 按钮）
- **Prompt 检测**：正则匹配 Claude Code 的交互式提示（权限确认、多选题、plan mode 等）
- **Discord 原生按钮**：将检测到的选项直接映射为 Discord Button（不用截屏导航键盘）
- **按钮点击处理**：用户点按钮 → 发送对应数字键/Enter/Escape 到 tmux
- ~~Screenshot 导航键盘~~：**不做** — Discord 按钮比截屏+方向键体验好得多

### C. 辅助
- **Interrupt/Abort 按钮**：输出消息上提供快捷 Escape/Ctrl-C
- **状态检测**：从 pane 状态行检测 idle/busy/waiting 状态

## Impact
- Affected code: `src/gits/core/monitor.py` (new), `src/gits/core/terminal_parser.py` (new), `src/gits/core/engine.py` (modify), `src/gits/adapters/discord/bot.py` (modify), `src/gits/adapters/discord/formatter.py` (new)
- Affected specs: output-monitoring, terminal-ui-bridge, message-formatting, discord-interactions
