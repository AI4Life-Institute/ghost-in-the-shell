<p align="center">
  <img src="docs/logo.png" width="200" alt="Ghost in the Shell">
</p>

<h1 align="center">Ghost in the Shell</h1>

<p align="center">
  用 Discord 或微信，在手机上远程控制你电脑上的 AI 编程助手。
</p>

<p align="center">
  <a href="README.en.md">English</a>
</p>

---

在外出时用手机看 AI 写代码、查看终端输出、批准权限提示——不需要开电脑，不需要 VPN。

<p align="center">
  <img src="docs/demo.gif" width="300" alt="Ghost in the Shell demo">
</p>

---

## 主要特性

- **桌面 ↔ 手机无缝切换** — 在 Mac 上启动任务，出门后用手机接着看；AI 持续工作，你随时介入
- **随时随地远程控制** — 绑定一个项目目录到 Discord 频道或微信对话，直接发消息就能操控 CLI
- **微信支持** — 不需要 Discord 账号，用你日常的微信直接操控 Ghost（`/bind`、`/bash`、`/s` 等）
- **终端截图** — 发 `/s` 立刻把当前终端画面截图发到手机
- **权限确认变按钮** — CLI 弹出权限提示时，Discord 显示可点击的按钮，手机上一键批准或拒绝
- **多 CLI 支持** — 支持 Claude Code、Codex CLI、OpenCode，每个频道可独立切换
- **会话恢复** — 重新绑定时显示历史会话列表，可续接之前的对话
- **tmux 实体终端** — 每个项目运行在真实的 tmux 窗口里，本地开发者仍可直接操作终端
- **自动内存管理** — 空闲进程自动挂起（空闲阈值随可用内存动态调整：内存充足时 2 小时，内存紧张时最短 10 分钟），收到消息后自动恢复
- **订阅安全** — 直接驱动官方 CLI 工具，和人工操作完全一样，无需 API Key，无 ToS 风险

## 为什么选 Ghost？

| | Ghost | OpenClaw | 本地 CLI |
|---|:---:|:---:|:---:|
| Discord 远程控制 | ✅ | ✅ | ❌ |
| 微信远程控制 | ✅ | ❌ | ❌ |
| 团队协作 | ✅ | ✅ | ❌ |
| 支持 Pro/Max 订阅，无需 API Key | ✅ | ⚠️ 稳定使用需要 API Key | ✅ |
| 账号安全，无 ToS 风险 | ✅ | ⚠️ 有封号记录 | ✅ |
| 真实本地终端（tmux） | ✅ | ❌ | ✅ |
| 跨 CLI 会话导入（如 Codex → Claude） | ✅ | ❌ | ❌ |

---

## 快速上手

**1. 安装**

```bash
curl -fsSL https://raw.githubusercontent.com/AI4Life-Institute/ghost-in-the-shell/master/install.sh | bash
```

或通过 Homebrew：

```bash
brew tap ai4life-institute/tap
brew install ai4life-institute/tap/ghost
```

> 安装脚本会自动处理 uv 和 tmux 的安装。需要 Python >= 3.12。

**2. 安装至少一个编程 CLI**

| CLI | 安装命令 |
|---|---|
| Claude Code | `npm i -g @anthropic-ai/claude-code` |
| Codex | `npm i -g @openai/codex` |
| OpenCode | `curl -fsSL opencode.ai/install \| bash` |

**3. 配置平台（Discord 或微信，或两者都用）**

运行设置向导：

```bash
ghost
```

**Discord 手动配置**

在 `~/.gits/config.env` 中设置：

```bash
GITS_DISCORD_TOKEN=your-bot-token
ALLOWED_GUILDS=["your-server-id"]
```

Bot 需要开启 **Message Content Intent**，并授权发送消息和创建线程的权限。

**微信配置**

运行向导后扫描二维码登录（不需要 API Key，直接使用你的微信账号）。可选设置默认项目路径，首次发消息自动绑定：

```bash
ghost wechat --path /path/to/your/project
```

重新登录（扫码）：

```bash
ghost wechat --relogin
```

**4. 启动**

```bash
ghost start
```

Ghost 会作为后台服务通过 launchd 运行，开机自启，无需手动维护。

---

## 使用方式

### Discord 工作流

1. 创建一个 Discord 频道（如 `#my-feature`）
2. `/bind /path/to/project` — 在 tmux 窗口中启动 CLI
3. 直接发消息 — 消息转发给 CLI
4. CLI 响应自动回流到 Discord
5. 权限提示以按钮形式出现，点击批准或拒绝
6. `/screenshot` 随时查看终端截图
7. `/done` 结束任务

### 微信工作流

1. 运行 `ghost wechat --path /path/to/project` 设置默认项目（一次性）
2. 在微信向 Ghost 发任意消息 — 自动绑定到默认项目
3. 直接发文字 — 转发到 Mac 上运行的 CLI
4. 随时发 `/s` 获取终端截图
5. 发 `/bind /other/path` 切换项目
6. 发 `/help` 查看所有命令

Ghost 作为后台服务运行，不需要开着桌面，随时发消息随时响应。

### Discord 命令

| 命令 | 说明 |
|---|---|
| `/bind <路径> [mode] [cli]` | 绑定频道到项目，启动 CLI。`mode`: `default`（需确认）或 `bypassPermissions`（直接执行） |
| `/unbind` | 解除绑定，关闭窗口 |
| `/info` | 查看绑定信息、会话文件路径 |
| `/screenshot` | 发送终端截图 |
| `/esc` | 发送 Escape 键 |
| `/done` | 关闭窗口并归档线程 |
| `/new [消息]` | 重置 CLI 会话 |
| `/bash <命令>` | 在项目目录执行 shell 命令 |
| `/keys <按键>` | 发送按键（Enter、Escape、Ctrl-C、Up、Down…） |
| `/model [名称]` | 切换模型（sonnet、opus、haiku、o3、gpt-4o…） |
| `/mode <模式>` | 切换权限模式（`default`、`bypassPermissions`、`auto`、`acceptEdits`） |
| `/fork <标题>` | 创建独立 git worktree 的子线程 |
| `/cc <命令>` | 直接转发斜杠命令给 CLI |

### 微信命令

| 命令 | 说明 |
|---|---|
| `/bind <路径>` | 绑定项目目录，启动 CLI |
| `/s` | 终端截图 |
| `/i` | 查看绑定状态 |
| `/e` | 发送回车 |
| `/x` | 发送 Escape |
| `/keys <按键>` | 发送按键序列 |
| `/bash <命令>` | 执行 shell 命令 |
| `/new` | 新建会话 |
| `/done` | 结束会话 |
| `/model <名称>` | 切换模型 |
| `/help` | 查看所有命令 |
| （普通文字） | 直接转发到终端 |

---

## 文档

架构说明、内部机制和配置参考见 [docs/architecture.md](docs/architecture.md)。

---

## 许可证

AI4Life 社区许可证 — 个人及年收入低于 $100 万的组织免费使用。超出此范围的商业使用需要单独授权，请联系 admins@ai4life.com。© 2026 [AI4Life Institute](https://github.com/AI4Life-Institute)
