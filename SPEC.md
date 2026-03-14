# Ghost in the Shell — Open Spec v2

## 项目愿景

Ghost in the Shell (GITS) 是一个 **社交平台 ↔ tmux 桥接工具**，让用户可以通过 Discord（后续扩展其他平台）远程操控服务器上 tmux session 中运行的 coding CLI（Claude Code 等），并支持对当前终端画面进行手动截屏。

核心理念：**tmux 是唯一真实源（source of truth）**，社交平台是远程遥控器和显示器。

---

## 调研背景

深入克隆并阅读了两个参考项目的全部源代码。

### 参考项目对比

| 维度 | [ccbot](https://github.com/six-ddc/ccbot) | [claude-on-discord](https://github.com/spanishflu-est1918/claude-on-discord) |
|------|---------------------|----------------------------------------------|
| 语言/运行时 | **Python 3.12+** (hatchling 构建) | **TypeScript / Bun** |
| 聊天平台 | 仅 Telegram (`python-telegram-bot>=21.0`) | 仅 Discord (`discord.js v14.25.1`) |
| AI 集成方式 | **通过 tmux 间接操控** Claude Code CLI | **通过 `@anthropic-ai/claude-agent-sdk` 直接调用** |
| tmux 控制 | **`libtmux>=0.37.0`** — Python tmux 绑定 | ❌ 无 tmux 支持（路线图中） |
| 终端截屏 | **Pillow** 自行渲染 ANSI→PNG（3层字体：JetBrainsMono + NotoSansCJK + Symbola） | **`agent-browser` CLI** 仅网页截屏（`http://localhost:3000`） |
| 输出监控 | **双通道**：JSONL 文件字节偏移轮询 + `tmux capture-pane -e` | Claude Agent SDK 流式回调（`onTextDelta`/`onThinkingDelta`） |
| 交互式 UI | **终端解析器** (`terminal_parser.py`) 正则匹配权限提示→Telegram 按钮 | Discord 按钮 + 23个斜杠命令 |
| 多会话管理 | Telegram 话题 ↔ tmux 窗口 (`session.py` JSON 持久化) | Discord 频道/线程 ↔ Claude session (SQLite) |
| 进程管理 | 直接运行 | **Guardian 监督器** — 心跳检测、自动重启、HTTP 控制 API |
| 消息处理 | `telegramify-markdown` 转 MarkdownV2 + 消息队列 | `chunker.ts` 分块处理 2000 字符限制 + 限速队列 |
| 架构耦合度 | **低**（不依赖 SDK，任何 CLI 都可控制） | **高**（深度绑定 Claude Agent SDK） |
| 安全性 | 环境变量清洗（从 tmux 移除敏感 env），用户白名单 | 多层权限模式，RunawayToolGuard 防工具死循环 |
| 代码量 | ~3000 行 Python | ~10400 行 TypeScript |

### ccbot 源码关键实现细节

**截屏渲染** (`screenshot.py`, 336行)：
- 使用 `tmux capture-pane -e -p -t {window_id}` 捕获带 ANSI 颜色的 pane 内容
- 正则解析 ANSI 转义序列：`\x1b\[([0-9;]*)m`
- 支持 16色、256色（6×6×6 RGB 立方体 + 24级灰度）、RGB 真彩色
- 三级字体级联（按字符 Unicode 范围分配字体层级）
- `asyncio.to_thread()` 将 CPU 密集的 Pillow 渲染放入线程池
- 深色背景 RGB(30,30,30)，默认前景 RGB(212,212,212)

**tmux 交互** (`tmux_manager.py`, 448行)：
- 基于 `libtmux` 库，所有阻塞调用用 `asyncio.to_thread()` 包装
- 发送文本时特殊处理：`pane.send_keys(text, literal=True, enter=False)` + 500ms 延迟 + Enter
- 感叹号命令 (`!command`)：先发 `!`，等 1 秒，再发剩余部分（适配 Claude Code 的 bash 模式）
- 环境变量隔离：创建 session 时移除 `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY` 等

**终端 UI 检测** (`terminal_parser.py`)：
- 正则匹配 Claude Code 的多种交互模式：
  - `AskUserQuestion` — 多选问题
  - `PermissionPrompt` — 工具权限请求 (Allow/Deny)
  - `BashApproval` — Bash 命令批准
  - `ExitPlanMode` — 计划模式确认
  - `RestoreCheckpoint` — 检查点恢复
- 状态行解析（旋转器动画 + 工作状态文本检测）

**Claude Code Hook** (`hook.py`)：
- 通过 `~/.claude/settings.json` 注册 `SessionStart` hook
- Hook 触发时记录 `tmux_window_id → claude_session_id` 映射到 `~/.ccbot/session_map.json`

### claude-on-discord 源码关键实现细节

**Claude 调用** (`runner.ts` + `channel-worker.ts`)：
- 使用 `query()` 函数（来自 `@anthropic-ai/claude-agent-sdk`）
- 每个 Discord 频道一个 `ChannelWorker`，内部 `AsyncInputQueue` 串行处理
- 5 级降级重试策略：默认 → 禁用 MCP → 禁用 Session 恢复 → 两者都禁 → 安全模式
- 流式消费：`for await (const message of this.query)` 异步迭代

**Discord 消息处理** (`user-message-handler.ts`)：
- 消息去重（5 分钟窗口，缓存 5000 条消息 ID）
- 多用户频道智能 @mention 检测
- `!command` 快捷语法直接执行 bash
- `StreamingStatusController`：300ms 防抖的流式消息编辑
- `DiscordDispatchQueue`：按频道隔离的限速消息队列（指数退避 + 429 处理）

**Thread 分支**：
- Discord Thread 自动继承父频道完整上下文
- 可选 `AUTO_THREAD_WORKTREE=true` 自动创建 Git worktree

### 关键洞察

1. **ccbot 的 tmux 架构是正确的方向** — 不受 SDK 变更影响，保留完整终端能力，任何 coding CLI 都可控制
2. **claude-on-discord 的 Discord 集成体验更成熟** — 消息分块、限速队列、流式更新、Guardian 自愈
3. **ccbot 的截屏方案可直接复用** — Pillow + ANSI 解析 + 三级字体，成熟且已验证
4. **ccbot 的 JSONL 监控 + pane 轮询双通道是最佳实践** — 结构化数据 + 实时终端状态互补
5. **两者都缺乏多平台支持** — 需要一个平台无关的抽象层

---

## 架构设计

### 总体架构

```
┌─────────────────────────────────────────────────────────┐
│                     Ghost in the Shell                   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Platform Adapters (适配层)            │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │   │
│  │  │ Discord  │  │ Telegram │  │  Slack (扩展) │   │   │
│  │  │ Adapter  │  │ Adapter  │  │   Adapter     │   │   │
│  │  └────┬─────┘  └────┬─────┘  └──────┬───────┘   │   │
│  └───────┼──────────────┼───────────────┼───────────┘   │
│          │              │               │                │
│  ┌───────▼──────────────▼───────────────▼───────────┐   │
│  │            Core Engine (核心引擎)                  │   │
│  │                                                    │   │
│  │  ┌─────────────┐  ┌──────────────┐               │   │
│  │  │  Session     │  │  Message      │               │   │
│  │  │  Manager     │  │  Router       │               │   │
│  │  └──────┬──────┘  └──────┬───────┘               │   │
│  │         │                │                         │   │
│  │  ┌──────▼──────┐  ┌─────▼────────┐               │   │
│  │  │  tmux       │  │  Screenshot   │               │   │
│  │  │  Controller │  │  Engine       │               │   │
│  │  └──────┬──────┘  └──────┬───────┘               │   │
│  │         │                │                         │   │
│  │  ┌──────▼──────┐  ┌─────▼────────┐               │   │
│  │  │  Output     │  │  Interactive  │               │   │
│  │  │  Monitor    │  │  UI Bridge    │               │   │
│  │  └─────────────┘  └──────────────┘               │   │
│  └──────────────────────────────────────────────────┘   │
│                          │                               │
│  ┌───────────────────────▼──────────────────────────┐   │
│  │              tmux Session Layer                    │   │
│  │  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐         │   │
│  │  │Win 1 │  │Win 2 │  │Win 3 │  │Win N │         │   │
│  │  │Claude│  │Cursor │  │Codex │  │ ...  │         │   │
│  │  │Code  │  │      │  │CLI   │  │      │         │   │
│  │  └──────┘  └──────┘  └──────┘  └──────┘         │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 核心设计原则

1. **tmux-first** — tmux session 是唯一真实源，所有操作最终映射为 tmux 命令
2. **平台无关的核心** — Core Engine 不感知具体聊天平台，通过 Adapter 接口解耦
3. **Coding CLI 无关** — 不绑定 Claude Code SDK，通过 tmux 间接操控任何 coding CLI
4. **渐进式** — 先做 Discord + tmux + 手动截屏，功能逐步增加
5. **可恢复性** — tmux 挂掉不是终点，bot 能从持久化状态自动重建所有会话

---

## 技术栈选型

| 层面 | 选择 | 理由 |
|------|------|------|
| 语言 | **Python 3.12+** | ccbot 已验证 Python + libtmux + Pillow 方案成熟可靠 |
| tmux 控制 | **libtmux >=0.37.0** | ccbot 验证过的成熟 Python tmux 绑定 |
| Discord | **discord.py >=2.4** | Python 生态最成熟的 Discord 库 |
| 截屏渲染 | **Pillow >=10.0** | ccbot 的 ANSI→PNG 方案可直接复用 |
| 配置管理 | **Pydantic Settings** | 类型安全的环境变量解析 |
| 数据持久化 | **JSON 文件**（MVP），后续可升级 SQLite | ccbot 用 JSON，简单可靠；复杂场景再引入 SQLite |
| Markdown 处理 | `telegramify-markdown` (Telegram), Discord 原生 Markdown | 跨平台格式化 |
| 包管理 | **uv** | 快速现代的 Python 包管理 |
| 代码质量 | **ruff** | linting + formatting 一体化 |
| 异步框架 | **asyncio** | libtmux 阻塞调用通过 `asyncio.to_thread()` 包装 |

---

## 模块详细设计

### 1. Platform Adapter 接口

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class IncomingMessage:
    """平台无关的入站消息"""
    platform: str            # "discord" | "telegram" | ...
    channel_id: str          # 平台侧的频道/话题 ID
    user_id: str
    text: str | None
    image_paths: list[str]   # 附件图片（已下载到本地的路径）
    reply_to: str | None
    raw: object              # 原始平台消息对象

@dataclass
class OutgoingMessage:
    """平台无关的出站消息"""
    text: str | None = None
    image: bytes | None = None       # PNG 图片数据
    buttons: list[list[Button]] | None = None
    edit_message_id: str | None = None

@dataclass
class Button:
    label: str
    callback_data: str

class PlatformAdapter(ABC):
    """聊天平台适配器接口"""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_message(self, channel_id: str, msg: OutgoingMessage) -> str:
        """发送消息，返回消息 ID"""
        ...

    @abstractmethod
    async def edit_message(self, channel_id: str, message_id: str, msg: OutgoingMessage) -> None: ...

    @abstractmethod
    async def delete_message(self, channel_id: str, message_id: str) -> None: ...

    @abstractmethod
    def on_message(self, callback) -> None:
        """注册消息回调"""
        ...

    @abstractmethod
    def on_button_click(self, callback) -> None:
        """注册按钮点击回调"""
        ...
```

### 2. tmux Controller

基于 ccbot `tmux_manager.py` 的架构，使用 libtmux 库（非直接调用 tmux 命令）：

```python
import libtmux
import asyncio

class TmuxController:
    """tmux 会话控制器 — 基于 libtmux"""

    def __init__(self, session_name: str = "gits"):
        self.session_name = session_name
        self.server = libtmux.Server()

    # --- 会话管理 ---
    async def get_or_create_session(self) -> libtmux.Session:
        """获取或创建 tmux session（参考 ccbot 的实现）"""
        return await asyncio.to_thread(self._get_or_create_session_sync)

    async def list_windows(self) -> list[WindowInfo]:
        return await asyncio.to_thread(self._list_windows_sync)

    async def create_window(self, name: str, cwd: str, command: str | None = None) -> WindowInfo:
        """创建新窗口并可选执行命令（如启动 claude）"""
        return await asyncio.to_thread(self._create_window_sync, name, cwd, command)

    async def kill_window(self, window_id: str) -> None:
        return await asyncio.to_thread(self._kill_window_sync, window_id)

    # --- 输入（参考 ccbot 的特殊处理逻辑）---
    async def send_text(self, window_id: str, text: str, enter: bool = True) -> None:
        """发送文本到 tmux pane
        - 普通文本: pane.send_keys(text, literal=True, enter=False) + 延迟 + Enter
        - 感叹号命令 (!command): 先发 !，等 1s，再发剩余（适配 Claude Code bash 模式）
        - 特殊键: pane.send_keys(text, literal=False)
        """
        ...

    async def send_keys(self, window_id: str, keys: str) -> None:
        """发送特殊按键：Escape, C-c, Up, Down, Enter 等"""
        ...

    # --- 输出捕获 ---
    async def capture_pane_text(self, window_id: str) -> str:
        """捕获 pane 纯文本内容"""
        ...

    async def capture_pane_ansi(self, window_id: str) -> str:
        """捕获 pane 带 ANSI 颜色的内容
        实现: subprocess 调用 tmux capture-pane -e -p -t {window_id}
        （ccbot 对此有特殊处理，libtmux 的 capture_pane 不支持 -e 参数）
        """
        ...

    # --- 安全（参考 ccbot）---
    def scrub_env(self, session: libtmux.Session) -> None:
        """清除 DISCORD_BOT_TOKEN, OPENAI_API_KEY 等敏感环境变量"""
        ...
```

### 3. Screenshot Engine

直接复用 ccbot `screenshot.py` 的核心逻辑：

```python
from PIL import Image, ImageDraw, ImageFont

class ScreenshotEngine:
    """终端 ANSI 内容 → PNG 渲染引擎

    核心实现复用自 ccbot screenshot.py (336行)：
    - 解析 ANSI 转义序列（正则：\\x1b\\[([0-9;]*)m）
    - 支持 16色/256色(6×6×6 RGB + 24级灰度)/RGB 真彩
    - 3 级字体级联：JetBrainsMono → NotoSansCJK → Symbola
    - CJK 全角字符正确处理（占两个字符宽度）
    - 深色背景 RGB(30,30,30)，默认前景 RGB(212,212,212)
    - CPU 密集渲染通过 asyncio.to_thread() 在线程池执行
    """

    def __init__(self, font_size: int = 28):
        self.font_size = font_size
        self.fonts = self._load_font_stack()

    async def capture(self, tmux_ctrl: TmuxController, window_id: str) -> bytes:
        """手动截屏：捕获 tmux pane 带 ANSI 颜色的内容并渲染为 PNG"""
        ansi_text = await tmux_ctrl.capture_pane_ansi(window_id)
        return await asyncio.to_thread(self._render_sync, ansi_text)

    def _render_sync(self, ansi_text: str) -> bytes:
        """同步渲染（在线程池中执行）
        1. _parse_ansi_line() 解析每行为 StyledSegment 列表
        2. 按字符分配字体层级 (_font_tier())
        3. Pillow Image + ImageDraw 逐字符绘制
        4. 返回 PNG 字节
        """
        ...
```

### 4. Output Monitor

参考 ccbot 的双通道监控架构（JSONL + pane 轮询）：

```python
class OutputMonitor:
    """Coding CLI 输出监控器 — 双通道"""

    # --- 通道一：JSONL 文件监控（结构化数据）---
    # 参考 ccbot session_monitor.py + transcript_parser.py
    #
    # 工作方式：
    # - 轮询 ~/.claude/projects/<hash>/*.jsonl 文件
    # - 记录字节偏移量，增量读取新内容（ccbot monitor_state.py）
    # - mtime 缓存跳过未变文件
    # - 解析结构化数据：助手回复、工具调用、思考内容
    # - 配对 tool_use / tool_result
    async def watch_jsonl(self, project_dir: str, callback) -> None: ...

    # --- 通道二：tmux pane 轮询（实时终端状态）---
    # 参考 ccbot handlers/status_polling.py + terminal_parser.py
    #
    # 工作方式：
    # - 定期 capture_pane 获取终端内容
    # - terminal_parser 检测交互式 UI（权限提示、用户问题等）
    # - 检测状态行变化（旋转器动画、工作状态）
    async def poll_pane(self, window_id: str, interval: float = 2.0) -> None: ...
```

### 5. Session Manager

管理聊天频道与 tmux 窗口之间的映射关系。

MVP 阶段使用 JSON 文件持久化（与 ccbot 一致），后续可升级 SQLite：

```python
@dataclass
class SessionBinding:
    """一个绑定 = 一个聊天频道 ↔ 一个 tmux 窗口"""
    platform: str           # "discord" | "telegram"
    channel_id: str
    window_id: str
    window_name: str
    work_dir: str
    coding_cli: str         # "claude" | "cursor" | "codex" | "custom"
    claude_session_id: str | None  # 由 Hook 自动填充
    created_at: str

class SessionManager:
    """会话绑定管理器 — JSON 文件持久化"""

    def __init__(self, state_dir: str = "~/.gits"):
        self.state_file = Path(state_dir) / "state.json"

    async def bind(self, channel_id: str, window_id: str, **kwargs) -> SessionBinding: ...
    async def unbind(self, channel_id: str) -> None: ...
    async def get_binding(self, channel_id: str) -> SessionBinding | None: ...
    async def list_bindings(self) -> list[SessionBinding]: ...
```

### 6. Interactive UI Bridge

参考 ccbot `terminal_parser.py` + `handlers/interactive_ui.py` 的实现：

```python
class InteractiveUIBridge:
    """终端交互式 UI → 聊天平台按钮 的双向桥接

    检测模式（正则匹配，参考 ccbot terminal_parser.py）：
    - PermissionPrompt: "Allow" / "Deny" 权限确认
    - AskUserQuestion: 多选项用户问题
    - BashApproval: Bash 命令批准
    - ExitPlanMode: 计划模式确认
    - RestoreCheckpoint: 检查点恢复
    """

    async def detect_ui(self, pane_text: str) -> InteractivePrompt | None: ...

    def to_buttons(self, prompt: InteractivePrompt) -> list[list[Button]]:
        """将交互式提示转换为平台按钮布局"""
        ...

    async def handle_button_click(self, callback_data: str, tmux_ctrl: TmuxController,
                                   window_id: str) -> None:
        """处理按钮点击 → 发送对应按键到 tmux pane"""
        ...
```

### 7. Coding CLI Hook

参考 ccbot 的 Hook 机制，用于自动关联 session ID：

```python
class CodingCLIHook:
    """Coding CLI Session Hook

    安装方式（以 Claude Code 为例）：
    在 ~/.claude/settings.json 中注册 SessionStart hook:
    {
      "hooks": {
        "SessionStart": [{
          "hooks": [{
            "type": "command",
            "command": "gits hook",
            "timeout": 5
          }]
        }]
      }
    }

    触发时：
    - 读取 TMUX_PANE 环境变量获取当前 tmux window ID
    - 读取 Claude 传入的 session_id
    - 写入 ~/.gits/session_map.json 建立映射
    - 通知 bot 进程（通过 IPC / 文件监控）→ bot 将 session ID 更新到 state.json
      并发送到 Discord 频道展示给用户
    """
    ...
```

---

## Discord 交互设计

以 **claude-on-discord 的命令集为蓝本**，适配 tmux 架构，并新增终端截屏功能。

### Channel + Thread 映射架构

两个参考项目都有 thread 概念，但设计完全不同：

| | **ccbot** (Telegram) | **claude-on-discord** (Discord) |
|---|---|---|
| 组织方式 | **平面**：每个 topic 独立，无层级 | **层级**：Channel=项目，Thread=子任务 |
| 映射关系 | 1 topic = 1 tmux window | Channel=1 session，Thread 继承 Channel |
| 创建方式 | 用户手动创建 topic → bot 引导选目录 → 自动创建 window | `/project` 绑定 Channel，`/fork` 创建 Thread |
| 上下文继承 | 无，每个 topic 完全独立 | Thread 继承父 Channel 的 workingDir |

**我们的方案**：结合两者——用 Discord 的 **Channel = 项目** 概念（来自 claude-on-discord），同时支持 ccbot 风格的 **Thread = 独立任务**，Thread 自动继承父 Channel 的工作目录。

```
Discord                              tmux session "gits"
─────────                            ───────────────────
#my-app (channel)                    window "my-app"
├─ topic: "project=my-app              └─ claude (coding CLI)
│         dir=/data/projects/my-app"     └─ cwd: /data/projects/my-app
│
├─ Thread: "fix-auth-bug"           window "fix-auth-bug"
│   └─ /fork fix-auth-bug              └─ claude (独立会话)
│      (默认继承根目录)                   └─ cwd: /data/projects/my-app
│
└─ Thread: "frontend-work"          window "frontend-work"
    └─ /fork frontend-work -d frontend  └─ claude (独立会话)
       (可选指定子目录)                    └─ cwd: /data/projects/my-app/frontend
```

**Channel 行为（`/bind`）— 三步交互式流程：**

参考 ccbot `directory_browser.py` 的交互式菜单设计，`/bind` 是一个多步按钮交互流程：

**Step 1: Window Picker — 检查已有 tmux 窗口**

如果有未绑定的 tmux 窗口，先展示列表让用户直接绑定：
```
🖥 Bind to Existing Window

These windows are running but not bound to any channel:
• my-app — ~/projects/my-app
• backend — ~/projects/backend

┌───────────────────────────────────┐
│ [🖥 my-app]  [🖥 backend]        │
│ [➕ New Session]  [Cancel]         │
└───────────────────────────────────┘
```
- 选择已有窗口 → 直接绑定，跳到 Step 3
- 选择「➕ New Session」→ 进入 Step 2
- 无未绑定窗口 → 自动跳到 Step 2

**Step 2: Directory Browser — 选择工作目录**

如果 `/bind` 没有带路径参数，启动交互式目录浏览器：
```
📁 Select Working Directory

Current: ~/projects/my-app

Tap a folder to enter, or select current directory

┌───────────────────────────────────┐
│ [📁 frontend]  [📁 backend]      │
│ [📁 docs]      [📁 scripts]      │
│ [📁 tests]     [📁 config]       │
│         [◀] [1/3] [▶]            │
│ [..] [✅ Select]  [Cancel]        │
└───────────────────────────────────┘
```
- 点击 📁 → 进入子目录
- 点击 `..` → 上一级
- `◀ ▶` → 翻页（每页 6 个目录，参考 ccbot `DIRS_PER_PAGE = 6`）
- `✅ Select` → 确认当前目录，进入 Step 3

**Step 3: Session Picker — 选择恢复或新建**

选定目录后，扫描该目录下已有的 coding CLI session：
```
▶ Resume Session?

Existing sessions found in this directory:

1. Fix authentication bug — 42 msgs (2h ago)
2. Add unit tests — 18 msgs (1d ago)

┌───────────────────────────────────┐
│ [▶ Fix auth...]  [▶ Add unit...] │
│ [➕ New Session]  [Cancel]         │
└───────────────────────────────────┘
```
- 选择已有 session → `claude --resume <id>` 恢复
- 选择「➕ New Session」→ 全新启动
- 无历史 session → 自动全新启动

**绑定完成后：**
1. Hook 捕获 CLI session ID → 记录到 `state.json`
2. 设置 Discord 频道 topic：`project=<name> dir=<path>`
3. 回复确认消息：
   ```
   ✅ 已绑定 #my-app → /data/projects/my-app
   🖥 tmux: window "my-app" | 🤖 claude | 📋 session: abc12345
   🔄 已恢复上次会话 (2h ago)
   ```
4. 后续该频道的所有消息转发到此 tmux 窗口

**快捷方式**：`/bind /path/to/project` 直接指定路径时跳过 Step 2，直接进入 Step 1（窗口检查）和 Step 3（session 选择）。

---

**Thread 行为（`/fork`）：**
1. 在 Discord 中创建 Thread
2. 创建新的 tmux 窗口，以 thread 名命名
3. cd 到工作目录：默认 = 父频道项目根目录；可选 `-d <subdir>` 指定子目录
4. 启动全新 coding CLI 会话（Thread 通常是新任务，不 resume）
5. Hook 捕获 session ID → 记录 → 回复确认消息（含 session ID）
6. Thread 内的消息转发到这个独立窗口

---

**Session 发现机制：**

各 CLI 的 session 扫描方式：

| Coding CLI | Session 存储 | 发现方式 |
|------------|-------------|----------|
| **Claude Code** | `~/.claude/projects/<dir-hash>/` | 扫描目录下 `*.jsonl`，按 mtime 排序，读取 session UUID + 首条消息摘要 |
| **Codex CLI** | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | 扫描 JSONL 文件，过滤 cwd 匹配目标目录的 session |
| **OpenCode** | SQLite 数据库 | 查询数据库中 cwd 匹配的最近 session |

**Thread 生命周期**（不需要手动关闭）：

Thread 的结束是自然发生的，不应该是用户刻意去做的操作。

**归档触发方式：**

| 触发事件 | 归档方式 | 具体实现 |
|----------|----------|----------|
| 用户在 Thread 中执行 `/kill` | **Bot 主动归档** | 杀掉 tmux 窗口进程 → `thread.edit({ archived: true })`（参考 claude-on-discord `kill-command.ts`） |
| tmux 窗口中的 coding CLI 正常退出 | **Bot 主动归档** | OutputMonitor 检测到退出 → 发消息通知 → `thread.edit({ archived: true })` |
| tmux 窗口被外部手动关闭 | **Bot 主动归档** | OutputMonitor 检测到窗口消失 → 发消息通知 → `thread.edit({ archived: true })` |
| tmux session 整体崩溃/服务器重启 | **不归档，尝试恢复** | HealthMonitor 检测到 → 通知 → 尝试自动恢复（见可靠性设计） |
| Thread 无人说话超时 | **Discord 自动归档** | Discord 内置机制，按 `autoArchiveDuration` 设定自动触发 |

**Discord Thread 自动归档机制**（Discord 平台内置）：

Thread 创建时设置 `autoArchiveDuration`，无人发言超过此时间后 Discord 自动归档：
- 60 分钟（1 小时）
- 1440 分钟（1 天）
- 4320 分钟（3 天）— 需要服务器 Boost
- 10080 分钟（7 天）— **GITS 默认值**，需要服务器 Boost

"活动"的定义：发送消息、取消归档、或修改归档时间。

```python
# /fork 创建 Thread 时设置自动归档时间
thread = await channel.create_thread(
    name=title,
    auto_archive_duration=10080,  # 默认 7 天无活动后归档
)
```

**归档 ≠ 删除**：
- Discord Thread 归档后仍可随时重新打开（unarchive）
- 重新打开时，bot 监听 `threadUpdate` 事件检测 `archived: true → false`
- 如果对应的 tmux 窗口还在 → 自动恢复绑定
- 如果 tmux 窗口已不在 → 提示用户可 `/fork` 重建
- 参考 claude-on-discord：`client.on("threadUpdate")` 监听归档/取消归档事件

---

### 设计逻辑

claude-on-discord 有 20 个 slash commands，我们按以下原则筛选：

- ✅ **保留** — 通过 tmux 转发即可实现的命令（大部分会话管理类）
- ✅ **新增** — tmux 绑定管理（`/bind`, `/unbind`）和终端截屏（`/screenshot`）
- ❌ **不做** — 依赖 Claude Agent SDK 的功能（merge 线程合并）、git 高级工作流（worktree/branches/pr/diff）、SDK 权限管理（mode）

| claude-on-discord 命令 | GITS 处理 | 说明 |
|------------------------|-----------|------|
| `/project <path>` | → **`/bind <path>`** | 我们需要 tmux 绑定，功能合并 |
| `/new` | ✅ **保留** | 重置会话 |
| `/compact` | ✅ **保留** | 压缩上下文 |
| `/bash <cmd>` | ✅ **保留** | 执行 shell 命令 |
| `/screenshot` | ✅ **改造** | 改为终端 ANSI→PNG 截屏（原项目是网页截屏） |
| `/model <name>` | ✅ **保留** | 切换模型 |
| `/status` | ✅ **保留** | 查看会话信息 |
| `/cost` | ✅ **保留** | 查看花费 |
| `/stop` | ✅ **保留** | 中断执行 |
| `/kill` | ✅ **保留** | 终止会话 |
| `/fork` | ✅ **保留** | 创建 Thread + 独立 tmux 窗口（继承父频道工作目录） |
| `/systemprompt` | → **`/cc memory`** | 通过 CLI 转发实现 |
| `/persona` | ❌ 不做 | SDK 专属 |
| `/mentions` | ❌ 不做 | 多用户频道策略，MVP 不需要 |
| `/mode` | ❌ 不做 | SDK 权限模式 |
| `/merge` | ❌ 不做 | SDK 线程合并（依赖会话摘要） |
| `/branches` | ❌ 不做 | git 工作流 |
| `/diff` | ❌ 不做 | git 工作流 |
| `/pr` | ❌ 不做 | git 工作流 |
| `/worktree` | ❌ 不做 | git 工作流 |
| (无) | ✅ **新增 `/bind`** | tmux 绑定管理 |
| (无) | ✅ **新增 `/unbind`** | tmux 绑定管理 |

---

### 命令与交互总览

| # | 方式 | 说明 |
|---|------|------|
| 1 | **Slash Commands** (13个) | 会话管理 + tmux 绑定 + 子任务分支 + 截屏 |
| 2 | **普通文本** | 直接发文字 → 转发到 tmux pane |
| 3 | **`!bash` 命令** | `!git status` → 直接执行 bash 返回输出 |
| 4 | **按钮交互** | 截屏导航键盘 + 交互式 UI 按钮 |
| 5 | **输出推送** | 后台 JSONL + pane 轮询 → 自动推送到频道 |

---

### 1. Slash Commands (13 个)

#### 项目绑定（Channel 级）

**`/bind [path]`**
- 将当前频道绑定到项目工作目录
- 两种使用方式：
  - `/bind /data/projects/my-app` — 直接指定路径
  - `/bind` — 不带参数，启动交互式目录浏览器（见下方）
- 行为：选定目录 → 检查已有窗口/session → 创建/绑定 tmux 窗口 → 启动 CLI
- 设置频道 topic：`project=<name> dir=<path>`
- 对应 claude-on-discord 的 `/project` + ccbot 的目录浏览器

**`/unbind`**
- 解除频道绑定，保留 tmux 窗口

#### 子任务分支（Thread 级）

**`/fork [title] [-d <subdir>]`**
- 在当前频道下创建 Thread（子任务）
- 行为：
  1. 创建 Discord Thread（名称 = title 或自动生成）
  2. 创建新 tmux 窗口（以 thread 名命名）
  3. cd 到工作目录：默认 = 父频道项目根目录；`-d <subdir>` 可选指定子目录
  4. 启动独立的 coding CLI 会话
- 对应 claude-on-discord 的 `/fork`，用 tmux 窗口替代 SDK session
- 示例：`/fork fix-bug`、`/fork frontend-work -d frontend`

#### 会话管理

**`/new`** — 重置会话：退出当前 coding CLI → 重新启动

**`/stop`** — 中断当前操作（向 tmux 发送 `Escape Escape`）

**`/kill`** — 终止会话：杀掉 tmux 窗口中的进程；如果在 Thread 中，同时归档 Thread

**`/compact`** — 向 tmux 转发 `/compact` + Enter

**`/status`** — 显示当前绑定信息：窗口名、工作目录、CLI 类型、session ID、运行状态

**`/cost`** — 向 tmux 转发 `/cost` + Enter

**`/model <name>`** — 向 tmux 转发 `/model <name>` + Enter

#### 工具命令

**`/bash <command>`** — 在工作目录下直接执行 shell 命令（subprocess）

**`/screenshot`** — 终端截屏：ANSI→PNG + 导航键盘按钮

#### CLI 命令转发

**`/cc <command>`** — 向 tmux 转发 `/<command>` + Enter，兜底覆盖所有 CLI 命令

---

### 2. 普通文本

用户在绑定频道发送文本 → `TmuxController.send_text()` 转发到 tmux pane。

```
用户: "帮我修复这个 bug"
  → pane.send_keys("帮我修复这个 bug", literal=True)
  → pane.send_keys("Enter")
  → OutputMonitor 轮询输出变化 → 推送回频道
```

与 claude-on-discord 的普通文本消息处理一致，区别在于我们走 tmux 而非 SDK。

### 3. !bash 命令

与 claude-on-discord 的 `!command` 快捷语法一致：以 `!` 开头的消息作为 bash 命令直接执行。

```
用户: !git status
  → subprocess("git status", cwd=work_dir)
  → 回复:
    $ git status
    On branch main
    nothing to commit, working tree clean
    (exit 0)
```

### 4. 按钮交互

#### 4.1 截屏导航键盘（新增）

`/screenshot` 截图下方附带导航按钮，可在 Discord 中操控终端：

```
┌─────────────────────────────────────┐
│ [⎋ Esc]  [↑]  [⏎ Enter]           │
│ [←]      [↓]  [→]                  │
│ [^C]     [␣]  [⇥ Tab]              │
│ [🔄 Refresh]                        │
└─────────────────────────────────────┘
```

点击按钮 → 发送按键到 tmux → 等 500ms → 重新截屏更新图片。

`🔄 Refresh` 仅刷新截图不发送任何按键。

#### 4.2 中断/终止按钮（对应 claude-on-discord）

claude-on-discord 的 Interrupt/Abort 按钮映射为：
- **Interrupt** → 向 tmux 发送 `Escape`（软中断）
- **Abort** → 向 tmux 发送 `C-c`（硬中断）

#### 4.3 交互式 UI 检测（来自 ccbot）

pane 轮询检测到 Claude Code 的交互式 UI 时，自动截屏并附带导航键盘推送到频道：

| UI 类型 | 典型场景 |
|---------|----------|
| PermissionPrompt | Claude 要执行 bash/编辑文件，需要权限确认 |
| AskUserQuestion | Claude 问用户多选题 |
| BashApproval | 高风险命令需确认 |
| ExitPlanMode | 退出计划模式 |

检测到后发送截屏 + 导航键盘，用户通过方向键/Enter/Esc 操作即可。

### 5. 输出推送

后台双通道监控，将 coding CLI 输出推送到 Discord（对应 claude-on-discord 的 SDK 流式回调，我们用 ccbot 的轮询方案替代）：

**JSONL 轮询**（结构化）— 解析 `~/.claude/projects/<hash>/*.jsonl`：
- `assistant.text` → Markdown 文本消息
- `tool_use` → "🔧 Using `<tool>`..."
- `tool_result` → 编辑上条消息追加结果

**Pane 轮询**（实时）— 定期 `tmux capture-pane`：
- 状态行变化 → 更新状态消息
- 交互式 UI → 触发截屏 + 按钮推送

**消息分块**：超过 2000 字符自动分块，代码块感知（跨块自动闭合/重开围栏），与 claude-on-discord 的 `chunker.ts` 逻辑一致。

---

### 消息流向总图

```
Discord Server
│
├── #my-app (Channel = 项目)
│   ├── /bind /unbind          → tmux window "my-app"
│   ├── /fork "fix-bug"        → 创建 Thread + tmux window "fix-bug"
│   ├── /new /stop /kill       → 会话控制 (→ tmux 按键)
│   ├── /compact /cost /model  → 转发 CLI 命令 (→ tmux 文本)
│   ├── /cc <cmd>              → 转发任意 CLI 命令
│   ├── /bash <cmd>            → subprocess 执行
│   ├── /screenshot            → 截屏 + 导航键盘
│   ├── "普通文字"             → send_text 到 tmux pane
│   ├── !bash cmd              → subprocess 执行
│   └── 按钮点击               → send_keys + 刷新截屏
│
├── Thread: "fix-bug" (子任务，继承 #my-app 的 workingDir)
│   └── 所有交互同上，但指向独立的 tmux window "fix-bug"
│
│              ┌──────────────────────────────┐
│              │  tmux session "gits"          │
│              │  ┌─────────┐ ┌─────────────┐ │
└──────────────│──│my-app   │ │fix-bug      │ │
               │  │(claude) │ │(claude)     │ │
               │  └─────────┘ └─────────────┘ │
               └──────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼                         ▼
       JSONL 轮询                 Pane 轮询
       (结构化输出)               (状态+UI检测)
              │                         │
              └────────────┬────────────┘
                           ▼
              Discord Channel/Thread 推送
              (文本/截图/按钮)
```

---

## 安全设计

参考 ccbot 的安全实践：

1. **环境变量清洗** — 从 tmux session 环境中移除 `DISCORD_BOT_TOKEN`, `OPENAI_API_KEY` 等敏感变量，防止被 coding CLI 读取（ccbot `tmux_manager.py` 第82-93行）
2. **用户白名单** — 配置 `ALLOWED_USERS` 限制可操作的 Discord User ID 列表
3. **Guild 白名单** — 配置 `ALLOWED_GUILDS` 限制可操作的 Discord 服务器
4. **工作目录沙盒** — 可配置 `ALLOWED_PATHS` 限制可绑定的工作目录范围
5. **原子写入** — 状态文件使用 write-to-temp + rename 防止数据损坏（参考 ccbot `utils.py`）

---

## 可靠性设计

tmux 是整个系统的核心依赖，它本身也可能崩溃（OOM、服务器重启、进程被杀等），需要完整的容错机制。

### tmux 健康监控

```python
class HealthMonitor:
    """tmux 健康监控器 — 定期探活 + 自动恢复"""

    async def check_loop(self, interval: float = 5.0) -> None:
        """定期检查 tmux 健康状态"""
        # 1. tmux server 是否存活 — libtmux.Server() 能否连接
        # 2. gits session 是否存在 — server.sessions 中是否有目标 session
        # 3. 各绑定窗口是否存在 — 遍历 SessionManager 的 bindings 逐一验证
        ...
```

### Coding CLI Session Resume 支持

tmux 恢复时，不只是重新启动 CLI，还可以尝试恢复之前的对话上下文。不同 CLI 的 resume 机制不同：

三个目标 CLI 都支持 session resume，但命令格式各不相同：

| Coding CLI | 按 session ID 恢复 | 恢复当前目录最近会话 | Session 存储位置 |
|------------|-------------------|---------------------|-----------------|
| **Claude Code** | `claude --resume <session_id>` | `claude --continue` | `~/.claude/projects/` |
| **Codex CLI** | `codex resume <session_id>` | `codex resume --last` | `~/.codex/sessions/` |
| **OpenCode** | `opencode --session <session_id>` | `opencode --continue` | SQLite 数据库 |

**统一恢复流程：**

1. Hook / 状态文件记录 `binding → cli_session_id` 映射
2. tmux 恢复时，根据 `coding_cli` 类型选择对应的 resume 命令格式
3. 若有 session ID → 按上表格式拼接 resume 命令
4. 若无 session ID → 尝试「恢复最近会话」（`--continue` / `--last`）
5. 若 resume 失败 → 降级为全新启动

```python
class CodingCLILauncher:
    """Coding CLI 启动器 — 支持 session resume"""

    # 各 CLI 的 resume 命令模板
    RESUME_TEMPLATES = {
        "claude":   {"by_id": "claude --resume {id}",         "latest": "claude --continue"},
        "codex":    {"by_id": "codex resume {id}",            "latest": "codex resume --last"},
        "opencode": {"by_id": "opencode --session {id}",      "latest": "opencode --continue"},
    }

    async def launch(self, window_id: str, work_dir: str, cli: str = "claude") -> None:
        session_id = self.session_map.get(window_id)
        templates = self.RESUME_TEMPLATES.get(cli)

        if templates and session_id:
            command = templates["by_id"].format(id=session_id)
        elif templates:
            command = templates["latest"]  # 尝试恢复最近会话
        else:
            command = cli  # 未知 CLI，直接启动
        await self.tmux.send_text(window_id, command)
```

### 故障场景与恢复策略

| 故障场景 | 检测方式 | 恢复行为 |
|----------|----------|----------|
| **单个 tmux 窗口消失** | OutputMonitor 发现 window_id 不存在 | 通知对应 Channel/Thread → 提示用户可 `/bind` 或 `/fork` 重建 |
| **tmux session 整体消失** | HealthMonitor 定期探活失败 | 自动重建 session → 遍历所有 binding 重建窗口 → cd 到原目录 → 尝试 resume CLI session → 通知各 Channel |
| **tmux server 挂掉** | HealthMonitor 连接 libtmux.Server() 失败 | 等待 tmux server 恢复（可能是重启中）→ 重试 N 次 → 超时后走 session 重建流程 |
| **服务器重启** | bot 启动时发现无 tmux | 自动创建 session → 从 `state.json` 恢复所有 binding → 尝试 resume CLI session → 通知各 Channel |

### 状态持久化保障

恢复的前提是状态文件 (`~/.gits/state.json`) 中保存了足够的信息：

```python
@dataclass
class SessionBinding:
    # ... 原有字段 ...
    work_dir: str           # 工作目录 — 恢复时 cd 到此目录
    coding_cli: str         # CLI 命令 — 恢复时重新启动
    window_name: str        # 窗口名 — 恢复时重建同名窗口
    claude_session_id: str | None  # ⬅ 恢复时用于 --resume，由 Hook 自动填充
    # 恢复后会变化的字段：
    # window_id — tmux 重建后 ID 会变，需要更新
```

### 恢复后的用户体验

```
📢 @channel
⚠️ tmux session 已断开，正在自动恢复...
✅ 已恢复 3 个工作窗口：
  • #my-app → /data/projects/my-app (claude --resume abc123) ✅ 上下文已恢复
  • Thread:fix-auth → /data/projects/my-app (claude --resume def456) ✅ 上下文已恢复
  • Thread:frontend → /data/projects/my-app/frontend (claude) ⚠️ 无历史 session，全新启动
```

### 核心设计原则补充

5. **可恢复性** — tmux 挂掉不是终点，bot 能从 `state.json` 自动重建所有会话并尝试恢复 CLI 对话上下文，用户无需手动干预

---

## 实现路线图

### Phase 1: MVP — Discord + tmux + 手动截屏 (2-3 周)

**目标**: 能通过 Discord 消息与 tmux 中的 coding CLI 交互，并支持手动终端截屏。

- [ ] 项目脚手架（uv + ruff + pyproject.toml）
- [ ] TmuxController — 基于 libtmux 封装（从 ccbot `tmux_manager.py` 移植核心逻辑）
- [ ] ScreenshotEngine — 从 ccbot `screenshot.py` 移植 ANSI→PNG 渲染
- [ ] SessionManager — 频道 ↔ tmux 窗口绑定（JSON 持久化）
- [ ] Discord Adapter — discord.py 集成
- [ ] 基础命令：`/bind`, `/unbind`, `/screenshot`, `/windows`, `/send-keys`
- [ ] 消息转发：Discord 文本 → tmux 输入
- [ ] 输出监控：tmux pane 轮询 → Discord 消息推送
- [ ] HealthMonitor — tmux 健康探活 + 启动时自动恢复绑定
- [ ] 字体文件打包（JetBrainsMono + NotoSansCJK + Symbola）

### Phase 2: 智能输出 + 交互式 UI (1-2 周)

**目标**: 更智能的输出处理和交互式 UI 桥接。

- [ ] JSONL 文件监控（从 ccbot `session_monitor.py` + `transcript_parser.py` 移植）
- [ ] 终端 UI 检测（从 ccbot `terminal_parser.py` 移植正则匹配）
- [ ] 交互式按钮映射（权限提示、多选题 → Discord 按钮）
- [ ] Claude Code Hook 集成（自动关联 session ID）
- [ ] 流式消息更新（编辑已发送消息，避免消息轰炸）
- [ ] 长消息分块处理（参考 claude-on-discord `chunker.ts` 的 2000 字符处理）
- [ ] `/status`, `/new`, `/resume`, `/compact` 命令

### Phase 3: Telegram 支持 + 多 CLI (1-2 周)

**目标**: 扩展到 Telegram 平台，支持更多 coding CLI。

- [ ] Telegram Adapter（直接复用 ccbot 的 `python-telegram-bot` 经验）
- [ ] 多 CLI 支持配置（claude / cursor / codex / 自定义命令）
- [ ] 消息历史分页浏览
- [ ] 语音消息转文字（OpenAI Whisper）

### Phase 4: 高级功能 (持续迭代)

- [ ] Guardian 进程监督器 — 升级版：HTTP 控制 API + 多实例管理（Phase 1 已有基础 HealthMonitor）
- [ ] 多用户权限管理
- [ ] Webhook 通知（任务完成/错误告警）
- [ ] 目录浏览器 UI（参考 ccbot `directory_browser.py`）

---

## 目录结构设计

```
ghost-in-the-shell/
├── pyproject.toml
├── README.md
├── SPEC.md                        # 本文档
├── .env.example
├── src/
│   └── gits/                      # 主包名
│       ├── __init__.py
│       ├── __main__.py            # CLI 入口 (gits start / gits hook)
│       ├── config.py              # Pydantic Settings 配置
│       ├── core/
│       │   ├── __init__.py
│       │   ├── engine.py          # 核心引擎，组装所有模块
│       │   ├── session.py         # SessionManager (JSON 持久化)
│       │   ├── tmux.py            # TmuxController (libtmux)
│       │   ├── screenshot.py      # ScreenshotEngine (Pillow ANSI→PNG)
│       │   ├── monitor.py         # OutputMonitor (JSONL + pane 轮询)
│       │   ├── health.py          # HealthMonitor (tmux 探活 + 自动恢复)
│       │   ├── terminal_parser.py # 终端 UI 检测 (正则匹配)
│       │   ├── ui_bridge.py       # InteractiveUIBridge
│       │   └── hook.py            # Coding CLI Hook
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── base.py            # PlatformAdapter ABC
│       │   └── discord/
│       │       ├── __init__.py
│       │       ├── adapter.py     # DiscordAdapter
│       │       ├── commands.py    # 斜杠命令定义
│       │       ├── buttons.py     # 按钮构建
│       │       └── formatter.py   # 消息格式化 + 分块
│       ├── fonts/                 # 从 ccbot 复制的字体文件
│       │   ├── JetBrainsMono-Regular.ttf
│       │   ├── NotoSansMonoCJKsc-Regular.otf
│       │   └── Symbola.ttf
│       └── utils/
│           ├── __init__.py
│           ├── ansi.py            # ANSI 转义序列解析器
│           ├── atomic_write.py    # 原子文件写入
│           └── security.py        # 环境变量清洗
├── tests/
│   ├── test_tmux.py
│   ├── test_screenshot.py
│   ├── test_terminal_parser.py
│   ├── test_session.py
│   └── test_discord_adapter.py
└── scripts/
    └── install-hook.sh            # Claude Code Hook 安装脚本
```

---

## 配置示例 (.env)

```bash
# === 平台配置 ===
DISCORD_BOT_TOKEN=your-discord-bot-token

# === 访问控制 ===
ALLOWED_USERS=123456789,987654321      # Discord User IDs
ALLOWED_GUILDS=111111111               # Discord Guild IDs

# === tmux 配置 ===
TMUX_SESSION_NAME=gits                 # tmux session 名称
CODING_CLI_COMMAND=claude              # 默认 coding CLI 命令

# === 截屏配置 ===
SCREENSHOT_FONT_SIZE=28

# === 监控配置 ===
PANE_POLL_INTERVAL=2.0                 # tmux pane 轮询间隔（秒）
JSONL_POLL_INTERVAL=2.0               # JSONL 文件轮询间隔（秒）

# === 安全配置 ===
ALLOWED_PATHS=/home/user/projects,/data/projects

# === 可选 ===
OPENAI_API_KEY=sk-xxx                  # 语音转文字（Telegram 扩展时使用）
GITS_DIR=~/.gits                       # 状态文件目录
LOG_LEVEL=INFO
```

---

## 与现有项目的差异总结

| 特性 | ccbot | claude-on-discord | **Ghost in the Shell** |
|------|-------|-------------------|------------------------|
| 多平台支持 | ❌ 仅 Telegram | ❌ 仅 Discord | ✅ 先 Discord，可扩展 |
| tmux 原生支持 | ✅ libtmux | ❌ | ✅ libtmux (复用) |
| 终端截屏 | ✅ Pillow ANSI→PNG | ❌ 仅网页截屏 | ✅ 手动截屏 (复用 ccbot) |
| 多 Coding CLI | ❌ 仅 Claude Code | ❌ 仅 Claude SDK | ✅ claude/cursor/codex/自定义 |
| 平台抽象层 | ❌ | ❌ | ✅ PlatformAdapter 接口 |
| 交互式 UI 桥接 | ✅ | ✅ | ✅ (复用 ccbot 正则) |
| 输出监控 | JSONL + pane 轮询 | SDK 流式 | JSONL + pane 轮询 (复用) |
| 架构耦合度 | 低（tmux） | 高（SDK） | 低（tmux） |
| 语言 | Python | TypeScript | Python |

---

*Ghost in the Shell — 让终端无处不在。*
