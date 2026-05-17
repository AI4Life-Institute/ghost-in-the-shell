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

#### Default 账户走原生 `~/.claude/` + 其余账户每日 keepalive

Per `add-default-account-native-and-refresh`：当 binding 的 `claude_account == manifest.default` 时，ghost **不**注入 `CLAUDE_CONFIG_DIR`，让 claude 走原生 `~/.claude/` 路径。非 default 账户仍走 `CLAUDE_CONFIG_DIR=~/.claude-<name>/` 隔离。

**关于 keychain（empirically verified 2026-05-17）**：claude 为每个 `CLAUDE_CONFIG_DIR` 维护**独立**的 macOS keychain 条目——服务名是 `Claude Code-credentials-<sha256(path)[:8]>`，默认 `~/.claude/` 用无后缀的 `Claude Code-credentials`。所以多账户之间**不会**互相污染，每个账户有自己隔离的 keychain entry。（早期 README 里关于 keychain 全局污染的警告是错的，已废止。）

ghost 利用这个事实做了两件事：

1. **`gits account list` 现在读 keychain**：优先读 per-CONFIG_DIR 的 keychain entry（claude 写入刷新 token 的真实位置），fallback 到 `.credentials.json`。之前文件 token 老化导致显示 `stale` / `no credentials` 的问题已解决。
2. **服务名选择**：`oauth_usage.py` 根据 `manifest.default` 选择服务名顺序——default 账户优先无后缀的 `Claude Code-credentials`，非 default 优先 sha256 后缀。

**每日 keepalive — 进程内 scheduler（推荐）**

非 default 账户需要周期性触发 `claude` 启动，让 claude 自带的 OAuth 刷新机制把 refresh token 续上。ghost daemon 内置 `TokenRefreshScheduler`，只要 daemon 在跑就**自动**每天执行——**跨机器移植无需任何外部 cron / launchd 设置**。状态持久化在 `~/.gits/token_refresh_state.json`，daemon 重启不会重复刷新。

手动 CLI 入口（用于即时排查）：

```bash
# 手动跑一次（默认跳过 default 账户）
gits account refresh

# 只刷一个账户（包括 default）
gits account refresh --account <name>
```

**可选：launchd 兜底**

如果你的笔记本经常关机、ghost daemon 不长期在线，可以另装个 launchd plist 当 backstop：

```bash
gits account refresh-install     # macOS：写 plist + bootstrap
gits account refresh-uninstall   # macOS：bootout + 删 plist
# Linux 只打印 cron snippet，由用户自己加到 crontab
```

> **迁移辅助**：升级到本版本后，如果 `~/.claude-<default>/.credentials.json` 比 `~/.claude/.credentials.json` 新（启动日志会出现一条 WARN），跑：
>
> ```bash
> gits account migrate-default-native          # dry-run，只打印计划
> gits account migrate-default-native --apply  # 实际复制（会要 y/N 确认）
> ```

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

### 工具命令 (Tooling)：`ghost butler` + `ghost discord`

除了从聊天端发的 slash 命令，Ghost 还提供两组本地 CLI，可以直接在终端里脚本化操作 Discord：

- **`ghost butler <verb>`** —— PM 语义封装层：从当前 git worktree 解析出发送者身份，自动加上 bot 识别的 butler 前缀，默认目标是这个 worktree 绑定的 home channel。适合从 agent 或 cron 任务里发消息，让 bot 知道"是谁在说话"。
- **`ghost discord <verb>`** —— 底层 Discord 传输原语：原样发消息、建/归档 thread、查 channel。需要不加任何前缀装饰、或者不要 worktree 身份解析时用这套。

| `ghost butler …` | 说明 |
|---|---|
| `whoami` | 显示 bot 身份、解析出的发送者、当前绑定的 home channel |
| `bind <channel-id>` | 把指定 channel 绑定为当前 worktree 的 home channel |
| `unbind` | 清除当前 worktree 的 home channel 绑定 |
| `home` | 查看当前 worktree 的 home channel 绑定 |
| `config-onboarding` | 写入 `~/.gits/butler-onboarding.json`（新 worktree channel 用的 guild + category） |
| `send [target] <content>` | 发一条带 butler 前缀的消息（默认目标是绑定的 home channel） |
| `dispatch <name>` | 在 home channel 里建一个 thread 并发第一条消息 |
| `read-thread <id>` | 读取 thread 或 channel 的最近消息 |

| `ghost discord …` | 说明 |
|---|---|
| `setup` | 交互式配置向导（token + guild + 重启） |
| `whoami` | 打印 bot 身份（id / username / discriminator） |
| `thread create <channel-id> <name>` | 在指定 channel 里建一个 thread |
| `thread archive <thread-id>` | 归档（关闭）一个 thread |
| `thread read <id>` | 读取 thread 或 channel 的最近消息 |
| `channel create <name>` | 在配置的 onboarding category 下建一个文字 channel |
| `channel show <channel-id>` | 按 ID 查看 channel 详情 |
| `channel list --guild <guild-id>` | 列出 guild 里的所有 channel |
| `message send <target> <content>` | 发原始消息（不加任何前缀装饰） |

> **迁移说明**：旧版本安装的独立 `butler` CLI（在 `~/.local/bin/butler` 的 symlink）仍然可用，输出和 `ghost butler` 完全一致。新脚本请用 `ghost butler <verb>` 这个写法；老的 symlink 后续会逐步下线。

### 随附的 Claude Code skills

仓库的 [`skills/`](skills/) 目录下打包了两个 Claude Code skill，描述上面这两组 CLI 在什么场景下该被调用、有哪些坑：

| Skill | 路径 | 触发场景 |
|---|---|---|
| `butler` | [`skills/butler/SKILL.md`](skills/butler/SKILL.md) | 用 `ghost butler` 发消息、读 thread、绑定 home channel，或派任务（vault-aware orchestrator：建 thread、发 `/bind` + 指针消息、原子地把 thread 元数据写回 task page 的 frontmatter） |
| `onboard-worktree` | [`skills/onboard-worktree/SKILL.md`](skills/onboard-worktree/SKILL.md) | 给新贡献者一站式开通 worktree + Discord 频道 + butler 绑定 + ghost `/bind` |

每个 skill 是 `<name>/SKILL.md` 的文件夹形式（Claude Code 约定）。安装时（`install.sh` 或首次跑 `ghost setup` 向导）会自动把这两份 skill 复制到 `~/.claude/skills/`，全局可用——这样 Claude Code 在任何项目下都能在合适的时机自动触发它们。

手动管理：

```bash
ghost install-skills        # 同步 / 更新（幂等；只覆盖 ghost 管理的文件夹，不动你自己装的 skills）
ghost uninstall-skills      # 移除（只删 manifest 里登记的，自己装的 skills 不动）
```

不想装可以跳过：传 `--no-install-skills`，或者设环境变量 `GHOST_NO_INSTALL_SKILLS=1`。manifest 在 `~/.claude/skills/.ghost-installed.json`，记录了 ghost 管理的 skill 列表 + 版本 + 安装时间。

---

## 文档

架构说明、内部机制和配置参考见 [docs/architecture.md](docs/architecture.md)。

把 ghost 当作 PM 工具来用：[role](docs/role.md) · [task schema](docs/task-schema.md) · [dispatch lifecycle](docs/dispatch-lifecycle.md)。

---

## 许可证

AI4Life 社区许可证 — 个人及年收入低于 $100 万的组织免费使用。超出此范围的商业使用需要单独授权，请联系 admins@ai4life.com。© 2026 [AI4Life Institute](https://github.com/AI4Life-Institute)
