# Change: 多 Claude 账户严格隔离与按 binding 切换

## Why

用户在同一台机器上拥有多个独立的 Claude Max 订阅账户（不同邮箱、不同 org），需要在同一台机器上同时使用多个账户——不同 tmux session 跑在不同账户下，互不干扰。当前 ghost 只能用一份 `~/.claude/` 凭据，所有 binding 共享同一个 Claude 身份，撞限额时只能整体停摆。

旧方案规划的"全局凭据热切换"（swap `~/.claude/.credentials.json`、env-source file `CLAUDE_CODE_OAUTH_TOKEN`）依赖 claude CLI 凭据机制的脆弱细节（keychain-first 读优先、SSH 上下文写失败、OAuth refresh 写文件回流），实测下不可靠且实现复杂；同时它假设"全局只有一个 active 账户"，违背用户多账户并行的诉求。

旧方案的"被动配额检测"（pattern-match tmux/JSONL 输出）准确率与维护成本不理想——OAuth Usage API 是权威源（已实测）。

**关于 session 数据共享**：曾考虑用 symlink 让多账户共用 `~/.claude-shared/projects/`。该方案有根本风险——两个不同账户的 binding 若 `--resume` 同一 `cli_session_id`，会让两个 claude CLI 进程并发 append 同一 JSONL 文件，对话历史互相串入对方上下文。ghost 无机制阻止这种情况。**故 V1 改为严格隔离**：每账户的 `projects/`（以及其它易冲突子项）是独立物理目录，不共享；跨账户使用同一 session 通过显式 `gits account import` 命令拷贝一份到目标账户。

新思路三个支柱：
1. **隔离机制**：使用 claude CLI 官方支持的 `CLAUDE_CONFIG_DIR` 环境变量，每账户一个独立 config 目录；目录间无 symlink，写入面物理分离
2. **Session 导入**：跨账户使用历史会话由显式 `gits account import <session_id> --to <account>` 完成，复制 JSONL 到目标账户的 projects 目录；之后两个账户各自演进，不共享写入
3. **配额查询**：使用账户的 OAuth access token 主动调 `https://api.anthropic.com/api/oauth/usage` 拿权威用量，不再做模式匹配。**已实测验证可用**（2026-04-27，详见 design.md §Reference §C）

## What Changes

- 新增 `multi-account` capability：per-account `CLAUDE_CONFIG_DIR` 隔离 + 严格目录隔离 + session import 原语 + 按 binding 切换 + API 主动查询用量
- 目录布局（**无 shared 目录、无 symlink**）：
  - `~/.claude/` — 不动；`claude_account=None` 的 binding 与外部直接调用 `claude` 仍用此身份与数据
  - `~/.claude-{name}/` — 每账户一份**完全独立**的 config 目录；首次 `account add --capture-current` 通过 `cp -R ~/.claude/* → ~/.claude-{name}/` 全量拷贝；后续 `account add` 创建空目录后跑 OAuth login
  - **没有** `~/.claude-shared/`、**没有** 跨账户 symlink；账户间数据完全隔离
- 新增 CLI 子命令族（5 条）：`gits account add | list | switch | remove | import`（短别名 `gits acct`）
  - `gits account import <session_id> --to <name> [--from <name>] [--force]` —— 跨账户拷贝 session JSONL
- 新增 Discord 斜杠命令：
  - `/accounts` — 列出账户、用量、当前 channel binding 的账户
  - `/account-switch <name>` — 把当前 channel 绑定的 binding 切到指定账户；**自动 import 当前 session**（在 kill 之后、respawn 之前）：target 没有该 session 文件时拷贝 source → target；target 已有则保留不覆盖（保护 target 端累积的对话历史）；用户在 embed 里看到 import status
  - 凭据 add/remove 仅本机 CLI（OAuth 必须在 host 交互）；session import 作为独立命令 `gits account import` 也仅本机暴露；Discord 的 import 是 `/account-switch` 内联的副作用（smart default），不作为独立命令
- `state.json` 中 `SessionBinding` 新增 `claude_account: str | None` 字段（`None` 表示沿用 `~/.claude/`）
- `--capture-current` 自动迁移：所有现有 binding 的 `claude_account = None` 自动变更为新创建账户名（避免它们继续写到 `~/.claude/projects/` 与新账户的 `~/.claude-{name}/projects/` 形成"双源"风险）
- `launcher.build_launch_command` 接受 account 名注入 `CLAUDE_CONFIG_DIR`；`launcher.get_session_file` / `_discover_claude_sessions` / `JsonlMonitor` 改为账户感知（按 binding 的 `claude_account` 解析 `<account_dir>/projects/` 或 fall back 到 `~/.claude/projects/`）
- ghost 的 hooks 在 `gits account add` 末段自动从 `~/.claude/settings.json` 复制到新账户的 `<account_dir>/settings.json`，让 per-account binding 也享受 ghost 的 hooks
- 用量查询：调 `GET https://api.anthropic.com/api/oauth/usage`，header `Authorization: Bearer <accessToken>` + `anthropic-beta: oauth-2025-04-20`；401 报 `usage: stale credentials, run claude --resume to refresh`，ghost 不实现内部刷新

不变的：
- 不调用 `claude` 子命令做事前额度检查
- 不修改 macOS keychain
- 不做跨机器同步凭据
- 不做手动凭据导入（必须走 `claude auth login` 原生流程）

被取消、不再需要：
- ~~`~/.claude-shared/` 共享目录~~：strict isolation 后无共享；session 跨账户使用走 import
- ~~`~/.claude-{name}/<item>` symlink → `~/.claude-shared/<item>`~~：每账户子项都是真实文件/目录
- ~~"Legacy Claude Path Unified Via Symlinks"（旧 D12 把 `~/.claude/` 共享子项替换为 symlink）~~：放弃路径统一思路，改为让 ghost 代码账户感知
- ~~`rateLimitedUntil` 持久字段~~：用量靠 API 实时查询
- ~~输出模式匹配（`quota_patterns.yaml` / QuotaPatternMatcher / debouncer）~~：API 是权威源
- ~~`output-monitoring` capability 扩展~~：本 change 不再扩展该 capability
- ~~`gits account use` / `default` / `repair` / `status`~~：行为内化到 add/list/switch
- ~~全局凭据互斥锁~~：每 binding 独立切换，仅需 binding 级互斥
- ~~ghost 内部 OAuth refresh 客户端~~：claude CLI 自带 refresh，ghost 只读

## Impact

- 受影响 specs：`multi-account`（新增）
- 受影响代码：
  - `src/gits/__main__.py` — 新增 `gits account` 子命令族（5 条）
  - `src/gits/core/launcher.py` — 注入 `CLAUDE_CONFIG_DIR`；`resolve_cli`/`get_session_file`/`_discover_claude_sessions` 接受 `claude_account` 参数；移除 `active_env_file` 路径
  - `src/gits/core/jsonl_monitor.py` — 接受多个 projects 路径（每账户一个 + legacy `~/.claude/projects/` 兜底）；按 binding 的 `claude_account` 解析监听路径
  - `src/gits/core/engine.py` — `SessionBinding.claude_account` 字段；`switch_account(binding_id, name)` 原语；`import_session(session_id, *, to, from_=None, force=False)` 原语
  - `src/gits/adapters/discord/bot.py` — `/accounts` + `/account-switch`
  - 新增模块：
    - `src/gits/core/account.py` — AccountVault + AccountLayout（目录创建、cp 迁移、账户感知路径解析）
    - `src/gits/core/oauth_usage.py` — Usage API 客户端（仅 GET usage；不刷 token）
  - **替换或废弃**（旧方案产物）：`src/gits/core/subscription.py` / `cli_subscription.py` / `quota.py` / `quota_notifier.py`
- 新增数据：
  - `~/.claude-{name}/` — 每账户一份完全独立的目录；`.credentials.json` + `projects/` + `settings.json` + `todos/` + `statsig/` + ... 全部真实文件/目录
- 新增配置：
  - `~/.gits/accounts/manifest.json`：
    ```
    { "default": "<name>|null",
      "accounts": [
        { "name", "email", "orgId", "subscriptionType",
          "config_dir", "lastUsed", "tags": [] }
      ],
      "lastSwitch": { "at", "binding_id", "from", "to", "reason" },
      "lastImport": { "at", "session_id", "from", "to" } }
    ```
- 不影响：
  - `~/.claude/` 原目录布局、内容（`claude_account=None` binding 与外部 `claude` 继续用）
  - 现有 binding 默认 `claude_account=None` → 行为与改前一致；除非用户跑 `--capture-current` 触发自动迁移
  - CLI 别名机制、screenshot 渲染、HealthMonitor、其它 capability
