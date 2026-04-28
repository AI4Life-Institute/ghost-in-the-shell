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

设置向导完成后会自动启动 Ghost 后台服务（launchd），开机自启，无需手动操作。

之后如需手动重启：

```bash
ghost restart
```

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
| `/accounts` | 列出 Claude 账户与 OAuth Usage 用量，高亮当前 channel 在哪个账户 |
| `/account-switch <name>` | 把当前 channel 的 binding 切到指定账户；自动 import 当前 session |

### 多账户隔离（可选）

ghost 支持在同一台机器上保管多个独立的 Claude Max 订阅账户，并按 binding（即每个 tmux 会话）独立选用其中一个。配额耗尽通过主动调 OAuth Usage API 实时查询，由用户手动切换；不同 binding 可以同时挂在不同账户上互不干扰。

> 此机制使用 claude CLI 官方的 `CLAUDE_CONFIG_DIR` 环境变量做账户隔离——每账户一份完整独立的 config 目录在 `~/.claude-{name}/`，没有跨账户共享，不会出现两份 claude 进程并发写同一份 session 文件的风险。
>
> 旧的 `gits subscription *` 命令族仍可使用但已弃用（启动时会输出 deprecation 提示），新用户请直接使用 `gits account *`。

#### 目录布局

- `~/.claude/` — 不动；外部直接调用 `claude`、未配置账户的 binding 仍用此身份
- `~/.claude-{name}/` — 每账户一份独立目录（凭据、projects、settings、todos 等都是真实文件，不是 symlink）
- `~/.gits/accounts/manifest.json` — 账户元数据（name、email、orgId、subscriptionType、config_dir、lastUsed、default、lastSwitch、lastImport）

#### 添加账户

```bash
# 从老版本迁移：把当前 ~/.claude/ 全量拷贝为第一个账户（包括凭据），并自动迁移所有现有 binding 到该账户
gits account add personal --capture-current

# 注册第二个及之后的账户：启动 OAuth login，写入到 ~/.claude-{name}/.credentials.json
gits account add work
gits account add home
```

`--capture-current` **仅在第一次 add 时有效**（之后会拒绝）。它会触发：
1. `rsync -a ~/.claude/ ~/.claude-personal/`（含凭据）
2. 为该账户写入 manifest 条目并设为 default
3. 把所有 `claude_account=None` 的现有 binding 自动迁移为 `claude_account="personal"`，避免新旧路径"双源"风险

#### 列表 + 用量

```bash
gits account list
```

输出每账户一行，含 email/订阅档位/5h 与 7d 用量百分比/重置时间/绑定数。用量数据通过 `GET https://api.anthropic.com/api/oauth/usage` 实时查询（带必需的 `anthropic-beta: oauth-2025-04-20` header），不依赖被动模式匹配。401 时显示 `stale (run claude --resume)` 引导用户跑 claude 让 CLI 自带的 refresh 机制刷新——ghost **不**实现自己的 OAuth refresh 客户端。

如需覆盖端点（Anthropic 升级 beta 版本时）：

```bash
export GITS_OAUTH_USAGE_URL=https://api.anthropic.com/api/oauth/usage   # 默认
export GITS_OAUTH_BETA_HEADER=oauth-2025-04-20                          # 默认
```

#### 切换 binding 的账户

```bash
# 从本机 CLI 切换某个 binding（必须显式指定 binding id）
gits account switch work --binding <channel-id>

# 从 Discord：在该 channel 发 /account-switch work
# Discord 路径自动 import 当前 session（target 没有该 session 文件时拷贝；已有则保留 target 端历史不覆盖）
```

CLI `switch` **不**自动 import；如需把当前对话搬到目标账户，先：

```bash
gits account import <session-id> --to work [--from <source>] [--force]
```

import 是一次性快照拷贝——之后 source 与 target 各自演进，不会双向同步。target 已有同 session 文件时默认拒绝（保护已积累的对话历史）；用 `--force` 强制覆盖（旧文件先备份为 `.gits-bak`，成功后清理）。

#### 删除账户

```bash
gits account remove work
```

如有任何 binding 仍在使用该账户会拒绝；先 `gits account switch <other> --binding <id>` 把它们迁走。

#### 命令一览

| 命令 | 说明 |
|---|---|
| `gits account add <name> [--capture-current]` | 注册账户；首次可选 `--capture-current` 全量迁移 |
| `gits account list` | 列出账户、用量、binding 计数 |
| `gits account switch <name> --binding <id>` | 切换某 binding 到指定账户 |
| `gits account remove <name>` | 删除账户（在用则拒绝） |
| `gits account import <id> --to <name> [--from <name>] [--force]` | 跨账户拷贝 session JSONL |

短别名 `gits acct` 等价于 `gits account`。

#### Hooks 跨账户传播

`gits hook --install --all-accounts` 会把 ghost 的 SessionStart hook 安装到 `~/.claude/settings.json` **以及**每个 `~/.claude-{name}/settings.json`。`gits account add` 在末段自动尝试一次该传播（best-effort）。

#### 向后兼容

未创建任何账户时（`~/.gits/accounts/manifest.json` 不存在），ghost 行为与改前完全一致——纯加性功能。要回滚：

```bash
rm -rf ~/.claude-*/   # 删除所有账户目录（~/.claude/ 不动）
rm -rf ~/.gits/accounts/
```

`~/.claude/` 自始至终未被本功能修改（`--capture-current` 是 `cp` 不是 `mv`），凭据与现有 session 完整保留。

#### 详细设计

完整规范见 [`openspec/changes/add-multi-account-hotswap/`](openspec/changes/add-multi-account-hotswap/)。

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
