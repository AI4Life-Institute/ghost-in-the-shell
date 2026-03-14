## Context
MVP 已验证：Discord↔tmux 消息转发、截屏、slash commands 正常。缺失两个核心闭环：(A) Claude 的输出用户看不到，(B) Claude 的交互提示用户无法在 Discord 里回答。

## Goals / Non-Goals
- Goals:
  - Claude Code 输出自动推送到 Discord（近实时）
  - Claude Code 交互提示转换为 Discord 原生按钮
  - 消息分块处理 Discord 2000 字符限制
- Non-Goals:
  - ~~截屏导航键盘~~（Discord 按钮比截屏+方向键体验好得多）
  - Telegram adapter（Phase 3）
  - 完美实时流式（轮询 2s 间隔可接受）

## Reference: ccbot terminal_parser.py

ccbot (`six-ddc/ccbot`) 已实现了完整的 Claude Code 终端提示检测，源码位于 `ccbot/src/ccbot/terminal_parser.py`（366 行）。我们直接参考其正则 patterns。

### ccbot 的 UIPattern 架构
- 每个 UI 类型定义为 `UIPattern(name, top, bottom, min_gap)`
- `top`: 标记提示区域开始的正则列表（任一匹配即可）
- `bottom`: 标记结束的正则列表
- 从上到下扫描 pane 行，top 匹配到 bottom 匹配之间的内容即为提示区域

### ccbot 已覆盖的 UI 类型及其 top/bottom 正则

| UI 类型 | top 正则 | bottom 正则 |
|---------|---------|------------|
| **ExitPlanMode** | `Would you like to proceed?` / `Claude has written up a plan` | `ctrl-g to edit in` / `Esc to cancel` |
| **AskUserQuestion** (多 tab) | `←\s+[☐✔☒]` | (无，到最后非空行) |
| **AskUserQuestion** (单 tab) | `[☐✔☒]` | `Enter to select` |
| **PermissionPrompt** (文本) | `Do you want to proceed?` / `make this edit` / `create \S` / `delete \S` | `Esc to cancel` |
| **PermissionPrompt** (数字选项) | `❯\s*1\.\s*Yes` | (无，min_gap=2) |
| **BashApproval** | `Bash command` / `This command requires approval` | `Esc to cancel` |
| **RestoreCheckpoint** | `Restore the code` | `Enter to continue` |
| **Settings** | `Settings:.*tab to cycle` / `Select model` | `Esc to cancel` / `Enter to confirm` / `Type to filter` |

### ccbot 的状态行检测
- Spinner 字符集: `· ✻ ✽ ✶ ✳ ✢`
- 在 pane 底部找 `────` 分隔线，其上一行若以 spinner 字符开头 → 提取状态文本
- Chrome 区域（`────` 分隔线以下）包含 prompt `❯` 和状态栏，需要 strip 掉

### ccbot vs GITS 的交互方式差异

| | ccbot (Telegram) | GITS (Discord) |
|---|---|---|
| 检测 | 正则 `UIPattern` | 复用 ccbot 正则 patterns |
| 交互 | 截屏 + 导航键盘 (↑↓←→ Enter Esc) | **选项按钮**（直接发数字键选择） |
| 原因 | Telegram InlineKeyboard 不能动态标签 | Discord Button 支持自定义标签 |
| 消息内容 | 纯文本（提示区域内容） | 纯文本 + 工具上下文摘要 |
| 更新 | edit 已有消息 | edit 已有消息 |

## Decisions

### 1. Prompt 检测复用 ccbot 正则
直接移植 ccbot 的 `UIPattern` 列表和匹配逻辑。格式固定、已经过生产验证。
- 多选：`❯ N. option_text` 模式
- 权限：`Do you want to proceed?` + `Esc to cancel`
- 状态行：spinner 字符 + `────` 分隔线定位

### 2. 按钮点击 → 数字键（优于 ccbot 的导航键盘）
Claude Code 的多选提示支持数字键直接选择（按 `1` 选第一项，`2` 选第二项...）：
- 用户点 Discord 按钮 → bot 向 tmux 发送对应数字字符
- 比 ccbot 的方向键导航 + Enter 简单很多，延迟也更低
- 对于 Esc to cancel 场景，提供 Cancel 按钮 → 发 Escape

### 3. 双通道输出推送
- **Pane 轮询**：2s 间隔 capture-pane diff，适用于任何 CLI，检测交互提示
- **JSONL 轮询**：2s 间隔，仅 Claude Code，提供结构化数据（assistant text、tool calls）
- 两者互补：JSONL 有结构但检测不到交互提示，Pane 能检测提示但输出是原始文本

### 4. 流式编辑避免消息轰炸
新输出到达时 edit 已有消息，不发新消息。300ms debounce。超过 2000 字符时开新消息。

## Risks / Trade-offs
- **轮询延迟**：2s 延迟可感知但可接受，用户可 `/screenshot` 获取即时视图
- **正则脆弱性**：Claude Code 更新提示格式可能打破正则。缓解：fallback 到截屏 + Esc/Enter 按钮
- **Discord rate limit**：快速编辑可能触发 429。缓解：debounce + 指数退避

## 实现顺序
1. **Terminal Parser + Prompt Buttons**（最高优先级）— 解决交互问题
2. **Pane Polling + 输出推送**（次高）— 解决看不到输出问题
3. **JSONL Polling**（可选增强）— 更结构化的输出
4. **Message Formatting**（配合输出推送）— 分块 + 流式编辑
