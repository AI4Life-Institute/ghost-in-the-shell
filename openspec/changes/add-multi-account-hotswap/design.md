## Context

用户场景：单台机器拥有多个独立 Claude Max 订阅（不同邮箱、不同 org），需要：
1. 每个账户登录一次（OAuth 浏览器交互），凭据由 ghost 持久化保管
2. 不同 tmux session 可同时挂在不同账户下，并行运行不冲突
3. 撞限额时仅通知用户，由用户决定切换某个 binding 到其它账户
4. 跨账户使用同一历史会话——通过显式 import 拷贝实现，**不**通过共享 JSONL 文件实现

约束（用户明确）：
- 用 claude CLI 官方支持的 `CLAUDE_CONFIG_DIR` 环境变量做隔离
- 配置目录命名 `claude-{account}`（如 `claude-personal`、`claude-work`）
- **严格隔离**：每账户的 `projects/`、`todos/`、`settings.json` 等子项是真实物理目录/文件，不与其它账户共享；跨账户使用同一 session 走显式 import 拷贝
- 配额检测**主动调** `https://api.anthropic.com/api/oauth/usage`（OAuth Bearer 认证），不做被动模式匹配
- 不持久化 `rateLimitedUntil` 等"猜测的"重置时间（API 是权威源，按需查询）
- 不动 `~/.claude/` 原目录（`claude_account=None` binding 与外部直接调用 `claude` 继续用此身份）

**为什么放弃 symlink 共享方案**：曾设计 `~/.claude-shared/projects/` 让所有账户共用一份 session 目录。该方案有根本风险：
- 两个不同账户的 binding 若 `--resume` 同一 `cli_session_id` → 两个 claude CLI 进程并发 append 同一 JSONL → 对话历史互相串入对方上下文
- ghost 没有 `cli_session_id` 全局唯一性约束，binding 可重复引用
- claude CLI `--resume` 时按文件读 JSONL 重建 context，并发写入会破坏 context 重建
- 即使 `projects/` 改不共享但 `todos/` / `statsig/` 共享，仍有低频但真实的串扰

V1 改为严格隔离：每账户一个完全独立的目录，跨账户 session 复用走显式拷贝。

## Goals / Non-Goals

### Goals
- 每个账户登录一次，独立 config 目录持久化保管
- 不同 binding 可同时使用不同账户；切换是 per-binding 操作，不波及其它 binding
- 切换原语：kill binding 内 claude → 修改 binding 的 `claude_account` 字段 → respawn with `CLAUDE_CONFIG_DIR=...`
- 跨账户使用同一历史会话通过 `gits account import` 显式拷贝；之后两个账户独立演进，写入不串
- 用量数据 API 实时查询；CLI/Discord list 输出含权威用量百分比
- 5 条 CLI 命令、2 条 Discord 命令完成日常操作
- 向后兼容：未启用账户功能的用户行为不变

### Non-Goals
- 不做自动切换（detect → notify 即止）
- 不做"按 binding 选择账户"的智能策略（手动选）
- 不做 reset-time 持久存储（API 现查现用）
- 不做跨账户文件共享（曾是 V1 设计，已被风险否决——见上文）
- 不做 import 后自动同步双向 sync（import 是一次性快照拷贝；之后两个账户的同 id session 各自演进）
- 不做被动输出模式匹配兜底（V1 范围；V2 可考虑作 fallback）
- 不做手动凭据导入（必须走 `claude auth login` 原生流程）
- 不做跨机器同步凭据
- 不修改 keychain 行为或介入 claude CLI 内部凭据机制
- 不为 codex/copilot/opencode 做类似账户隔离

## Decisions

### D1: 用 CLAUDE_CONFIG_DIR 做账户隔离

每账户一个独立 config 目录 `~/.claude-{name}/`，**完全独立**（无 symlink 跨账户共享任何子项）：

```
~/.claude-{name}/
├── .credentials.json           # 真实文件，OAuth 凭据写入此处
├── .gits-managed               # ghost 拥有权 marker（mode 0644，空内容）
├── projects/                   # 真实目录，session JSONL 写入此处
├── settings.json               # 真实文件，含 ghost hooks（add 时复制）
├── todos/                      # 真实目录
├── statsig/                    # 真实目录
├── shell-snapshots/            # 真实目录
├── ide/                        # 真实目录
├── plugins/                    # 真实目录
└── ...                         # 其它子项均为真实文件/目录
```

claude 启动命令前置：

```bash
CLAUDE_CONFIG_DIR=$HOME/.claude-{name} claude --resume <id>
```

claude CLI 把 `CLAUDE_CONFIG_DIR` 视为 config 根目录，所有读写都基于此路径。每账户的所有数据物理隔离；OAuth refresh 写凭据文件、claude 进程写 JSONL、todos、shell-snapshots 等都只在本账户目录内。

**为什么放弃旧方案的 swap-file / env-source-file**：旧路径需要应对 claude 凭据 4 元组（env > keychain > file > IDE inherit）的若干不可靠角落（参见本文末 §Reference §B）；`CLAUDE_CONFIG_DIR` 是官方支持的隔离单位，由 CLI 自身保证不串值。

### D2: 严格隔离，不共享任何子项

每账户目录内的所有子项（除 OAuth 凭据外的也包括 projects、settings.json、todos、statsig、shell-snapshots、ide、plugins）都是**真实物理文件/目录**——账户间无 symlink、无 bind mount、无 hardlink。

**为什么不共享**：见 §Context 末段。两个不同账户的 binding 若引用同一 `cli_session_id` 并 `--resume`，会让两个 claude CLI 进程并发写同一 JSONL；ghost 无机制阻止；context 重建错乱。即使 `projects/` 改不共享，`todos/` / `statsig/` 等共享仍有低频串扰。

**代价**：
- 磁盘冗余：每账户全量复制 plugins/（可能数十 MB）等。可接受——多账户用户磁盘通常够。
- 跨账户使用同一历史会话需要显式 import（D13）——一次性显式动作，不是无声共享
- ghost 的 hooks 配置需要在 `account add` 时拷贝到新账户的 settings.json（D14）

### D3: 账户初始化

首次 `gits account add <name> --capture-current`：

1. 校验 `~/.claude/.credentials.json` 存在且含合法 `claudeAiOauth.accessToken`
2. 创建 `~/.claude-{name}/`（mode 0700）
3. 写 `.gits-managed` marker（mode 0644，空内容）
4. 全量复制：`rsync -a ~/.claude/ ~/.claude-{name}/`（**包含** `.credentials.json` —— 接管现有登录态）
5. 提取 metadata：JWT-decode `accessToken` 取 email/orgId/subscriptionType
6. 写 manifest 条目，`manifest.default = <name>`
7. **自动迁移现有 binding**（D15）：把所有 `claude_account == None` 的 binding 改为 `claude_account = <name>`；不重启它们（下次启动自然走新路径）
8. `~/.claude/` 保持原样不动——`claude_account=None` 的 binding 仍可读它（虽然首次 capture 后预期没有 `None` binding 了）；外部 `claude` 继续用

后续 `gits account add <name>`（无 `--capture-current`）：

1. 创建 `~/.claude-{name}/`（mode 0700）+ marker
2. 跑 `CLAUDE_CONFIG_DIR=$HOME/.claude-{name} claude auth login`（subprocess 继承 stdin/stdout/stderr）
3. login 完成后 claude CLI 写凭据到 `~/.claude-{name}/.credentials.json`，并在该目录初始化空的 `projects/` 等子目录
4. 提取 metadata 入 manifest
5. 若 `manifest.default` 为 null → 设为本次 `<name>`
6. **从 `~/.claude/settings.json` 复制 ghost hooks 段到 `~/.claude-{name}/settings.json`**（D14）；如目标已含同 hook ID 则保留，避免覆盖用户后续手动调整
7. **不**影响任何现有 binding——它们各自的 `claude_account` 不变

### D4: binding 持久化账户名

`SessionBinding` 新增字段 `claude_account: str | None`：
- `None` → 默认行为，不注入 `CLAUDE_CONFIG_DIR`，使用 `~/.claude/`
- `"sharon"` → 注入 `CLAUDE_CONFIG_DIR=$HOME/.claude-sharon`

state.json 向后兼容：现有 binding 反序列化时字段缺失默认 `None`。新建 binding 取 `manifest.default`（可能为 None）。

### D5: 切换原语（per-binding，无全局锁，可选 auto-import）

`switch_account(binding_id, target_account, *, auto_import=False)`:

```
1. 取该 binding 的 per-binding 互斥锁（asyncio.Lock，进程内）
2. 校验 target_account ∈ manifest.accounts
3. 给 binding 的 tmux pane 发 C-c，等 300ms
4. 枚举 binding 内 claude 进程 pid → SIGTERM → 5s 内未死升级 SIGKILL → 1s reap
   仍存活则 abort 并释放锁
5. （NEW）auto_import 路径——见 D16；必须在 kill 之后做，保证 source claude 已死
6. 修改 binding.claude_account = target，原子写 state.json
7. 更新 manifest.lastSwitch + accounts[target].lastUsed + manifest.default = target
8. respawn：build_launch_command 拼出 CLAUDE_CONFIG_DIR=... claude --resume <id>
9. 释放锁
10. Discord 推完成消息（如有 channel binding）
```

`auto_import=False` 是 V1 默认；CLI `gits account switch` 不传，保留"显式 import + 显式 switch"两步流程。Discord `/account-switch` 调用时传 `True`（D16）。

**没有 force=True 模式**：旧设计的 `use` 命令是为绕过 `rateLimitedUntil`；该字段已取消，`switch` 不再做配额预检——任何账户都能立即切，撞限额时由 API 查询展示，用户自己判断。

**为什么不要全局锁**：每 binding 切换只读写自己的 pane + 自己的 state 字段；凭据写入面是 `~/.claude-{account}/.credentials.json`，每账户独立；同一账户被两 binding 共用时 OAuth refresh 由 claude CLI 用 atomic write 处理。不同 binding 之间并发切换合法。

### D6: 账户增删

`gits account add <name> [--capture-current]`：见 D3。
- 关键不变：**不影响任何现有 binding**——add 仅创建账户目录，不改 binding 字段、不重启任何 binding

`gits account remove <name>`：

```
1. 校验：无 binding 仍 claude_account == name；否则拒绝并列出违规 binding
2. 删除 ~/.claude-{name}/ 目录（含其 symlink；symlink 删除不影响 ~/.claude-shared/ target）
3. 从 manifest 移除条目
4. 若 manifest.default == name → 重置为下一个最近 lastUsed 的账户，或 null（manifest 空）
5. 不触碰 ~/.claude-shared/
```

### D7: Discord 暴露面

仅暴露两条命令（与"凭据增删本机原则"一致）：

- `/accounts` — Discord embed 输出每账户 name/email/subscriptionType/usage（API 实时查询）；高亮当前 channel binding 在哪个账户
- `/account-switch <name>` — autocomplete 候选账户名；切换当前 channel 绑定的 binding 到该账户
  - channel → binding 映射沿用现有逻辑（`engine.bindings_for_channel`）
  - 若 channel 未绑定任何 binding → 错误消息
  - 若该 binding 当前已是该账户 → no-op + 消息

不暴露 add/remove：OAuth 必须在 host 交互；凭据生命周期变更需本机操作以保证 trust boundary。

### D8: 配额查询（OAuth Usage API，主动）

**已实测验证可用**（2026-04-27，本机 token + curl 实证；claude CLI v2.1.121 二进制提取确认）。

**Usage 端点**：
```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken>
anthropic-beta: oauth-2025-04-20            ← 必需，缺则 401 "OAuth authentication is currently not supported"
```

access token 取自 `~/.claude-{name}/.credentials.json` 的 `claudeAiOauth.accessToken`。

**Usage 响应 schema**（实测样本）：
```json
{
  "five_hour":   { "utilization": 26.0, "resets_at": "2026-04-28T07:30:00.202122+00:00" },
  "seven_day":   { "utilization": 4.0,  "resets_at": "2026-05-05T02:00:00.202137+00:00" },
  "seven_day_oauth_apps":  null,
  "seven_day_opus":        null,
  "seven_day_sonnet":      { "utilization": 2.0, "resets_at": "..." },
  "seven_day_cowork":      null,
  "seven_day_omelette":    { "utilization": 0.0, "resets_at": null },
  "iguana_necktie":        null,
  "omelette_promotional":  null,
  "extra_usage": { "is_enabled": false, "monthly_limit": null, "used_credits": null, "utilization": null, "currency": null }
}
```

显示策略：
- 主展示 `five_hour` + `seven_day`（utilization 百分比 + 距 resets_at 时长）
- `seven_day_opus` / `seven_day_sonnet` 非 null 时附加显示
- 其它内部代号字段（`seven_day_cowork` / `iguana_necktie` / `omelette_promotional` / `seven_day_omelette` / `seven_day_oauth_apps`）忽略
- `extra_usage.is_enabled = true` 时显示付费扩展额度
- 未识别的字段静默忽略（schema drift tolerated）

**调用时机**：
- `gits account list`（CLI）/ `/accounts`（Discord）触发时按需调用
- `gits account switch <name>` 不做预检（设计 D5/D9）
- 不做后台周期轮询（V1 范围）

**缓存**：60 秒内存缓存；键 `(account_name, hash(accessToken))`，token rotation 后自然失效

**容错**：
- 网络错误 / 5xx → `usage: unavailable (network)`
- 401 → `usage: stale credentials, run claude --resume to refresh`（**不做 ghost 内部刷新**——claude CLI 自己会在下次启动时刷新；ghost 只读不刷，避免重复实现 OAuth 客户端、避免与 claude CLI 写入竞态、避免再多一个 API 端点依赖）
- 429 → `usage: api rate limited, retry later`
- 缺字段 / 未知字段 → 单字段 graceful 降级，整行不阻塞

**输出展示（list）**：
```
* personal     alice@example.com           max    5h 26%   7d 4%    resets 1h32m    bindings 2
  work         work@example.com            pro    5h 0%    7d 12%   resets 4h05m    bindings 0
  legacy       sam@example.com             max    usage: stale credentials          bindings 1
[default: personal]
```

**不做被动模式匹配**：旧方案的 `quota_patterns.yaml` + `QuotaPatternMatcher` + 200ms 反向信号 + 连续两帧 debounce 全部废除。CLI 输出 / JSONL 中的限额信息只是 UX 提示，不驱动 ghost 状态变更。

### D9: CLI 子命令族（精简）

```
gits account add <name> [--capture-current]
gits account list
gits account switch <name> --binding <id>
gits account remove <name>
```

短别名 `gits acct`（旧 `gits subscription` 子命令在本 change 实现期间保留 deprecation 提示）。

`switch` 必传 `--binding <id>`：本地 CLI 没有 channel 上下文（Discord 命令通过 channel→binding 推断）。

**自动化的隐式行为**（替代旧 `default` / `repair` / `status` / `use` 命令）：
- `manifest.default`：首次 add 自动设为该账户；每次 `switch` 后更新为新目标；`remove` 删了 default 时按最近 lastUsed 自动选下一个
- symlink 修复：`add` / `list` 调用时检测缺失 symlink → 自动补；指错（指到非 `~/.claude-shared/<item>`）→ 仅 WARN 不自动改（避免破坏用户故意的安排）
- 状态：`list` 输出含 binding 计数 + 当前用量 + symlink 健康警告，无需独立 `status` 命令
- 强制切换：取消 `use` 命令；`switch` 永不预检，立即切

### D10: account 名校验与冲突

- `<name>` 必须满足 `^[a-z0-9][a-z0-9_-]{0,31}$`（小写字母数字、连字符、下划线，长度 1–32）
- 拒绝预留前缀：`shared`（避免与 `~/.claude-shared/` 混淆）
- 拒绝 `~/.claude-{name}/` 已存在但缺 `.gits-managed` marker 的目录——避免误覆盖用户手动创建的目录
- 创建时写空 marker `~/.claude-{name}/.gits-managed`（mode 0644）

### D11: 不做 ghost 内部 OAuth Refresh

明确决定 **不实现** OAuth refresh 客户端。理由：

1. **claude CLI 已经做了**——任一 binding 启动时若 access token 即将过期，claude CLI 自己会用 refresh token 刷新并写回 `.credentials.json`（参见 §Reference §B 的旧分析）
2. **避免端点依赖膨胀**——本方案只依赖一个端点 `/api/oauth/usage`；多一个 `/v1/oauth/token` 依赖意味着多一个升级 / 下线 / 鉴权变更的故障面
3. **避免 OAuth 客户端实现负担**——OAuth 2.0 标准但实现细节多（client_id 配置、错误处理、token rotation、retry policy），ghost 不应该重复 claude CLI 已经做对的事
4. **避免并发写入竞态**——claude CLI 与 ghost 同时改 `.credentials.json` 即使是 atomic write 也是 last-write-wins，引入"为什么 token 突然变旧了"类调试噪音

**ghost 在 401 时的行为**：把账户行渲染为 `usage: stale credentials, run claude --resume to refresh`。用户跑一次 claude（任意账户的 binding 即可触发），claude CLI 把新 token 写回该账户的 `.credentials.json`，下次 `gits account list` 就读到新 token 拿到 200。

环境变量配置面（仅 usage 相关）：
- `GITS_OAUTH_USAGE_URL`（默认 `https://api.anthropic.com/api/oauth/usage`）
- `GITS_OAUTH_BETA_HEADER`（默认 `oauth-2025-04-20`）

不再涉及 `GITS_OAUTH_REFRESH_URL` / `GITS_OAUTH_CLIENT_ID`（不需要这些端点）。

### D12: ghost 代码改为账户感知路径解析

**问题**：ghost 当前代码硬编码 `~/.claude/projects`：
- `src/gits/core/jsonl_monitor.py:220` —— `_projects_path = projects_path or Path.home() / ".claude" / "projects"`
- `src/gits/core/launcher.py:194` —— `get_session_file()` 拼 `~/.claude/projects/<hash>/<id>.jsonl`
- `src/gits/core/launcher.py:427` —— `_discover_claude_sessions()` 扫描 `~/.claude/projects`
- `src/gits/__main__.py:1165` —— `_CLAUDE_SETTINGS_FILE = "~/.claude/settings.json"` hook 安装

严格隔离方案下，per-account binding 把 JSONL 写到 `~/.claude-{name}/projects/`（真实目录，与 `~/.claude/projects/` 物理分离）。ghost 必须改为账户感知。

**修改方案**：

`AccountLayout` 提供集中路径解析：
```python
class AccountLayout:
    def projects_dir(self, claude_account: str | None) -> Path:
        if claude_account is None:
            return Path.home() / ".claude" / "projects"
        return Path.home() / f".claude-{claude_account}" / "projects"

    def settings_file(self, claude_account: str | None) -> Path: ...
    def credentials_file(self, claude_account: str | None) -> Path: ...
    def all_active_projects_dirs(self) -> list[Path]:
        """所有账户的 projects 目录 + ~/.claude/projects（如有 None binding）"""
```

**`launcher` 改造**：
- `resolve_cli(cli, claude_account=None)` —— 把 `claude_account` 传入，返回的 `ResolvedCLI.session_path` / `config_dir` 由 layout 解析（同时尊重 `cli_aliases.session_path` 显式覆盖）
- `get_session_file(work_dir, cli, session_id, claude_account=None)` —— 改用 resolved.session_path
- `_discover_claude_sessions(work_dir, session_path=None)` —— 已有 `session_path` 参数，调用方传 layout 解析的路径
- `build_launch_command(..., claude_account=None)` —— 已有此参数，注入 `CLAUDE_CONFIG_DIR`

**`JsonlMonitor` 改造**：
- 保留单 `_projects_path` 兜底（默认 `~/.claude/projects`，处理 `claude_account=None` binding）
- 新增 `add_account_projects_path(account_name, path)` / `remove_account_projects_path(account_name)`
- 监听循环：每 tick 扫描 `_projects_path` + 所有注册账户的 `projects/`；offsets 字典 key 已是 `(channel_id, file_path)`，天然按 file_path 区分账户
- 启动时由 engine 根据 manifest 注入所有当前已注册账户的路径
- `gits account add` / `remove` 在事务末段调用 monitor 的注册方法

**hook 安装器（`__main__.py:1165`）改造**：
- `gits hook --install` 默认仍写 `~/.claude/settings.json`
- 新增 `--all-accounts` flag：迭代 manifest.accounts 写入每个账户的 `<dir>/settings.json`
- `gits account add` 自动 invoke hook 安装到新账户（仅对 `~/.claude/settings.json` 中 ghost 拥有的 hook 段；不复制用户的其它 settings 字段）（D14）

**为什么这样改**：
- 账户感知是 strict-isolation 的逻辑必然——每账户物理分离意味着 ghost 必须知道 binding 属于哪个账户才能找文件
- 避免 symlink 副作用（旧 D12 的代价：触碰 `~/.claude/`、Spotlight 跟随、Time Machine 行为漂移）
- 把"账户 → 路径"映射集中到 `AccountLayout`，三处硬编码改一处即可

### D13: Session Import 原语

**目标**：跨账户使用同一历史 session，但不共享 JSONL 文件——通过显式拷贝把 source 的快照搬到 target 的 projects 目录。

**接口**：
```
gits account import <session_id> --to <target> [--from <source>] [--force]
```

**流程**：
1. 校验 `<target>` ∈ manifest.accounts
2. 解析 source：
   - 若 `--from <source>` 指定：使用该账户的 `projects/`
   - 否则自动扫描所有账户 + `~/.claude/projects/`（即 `claude_account=None` 的兜底位置），找含 `<session_id>.jsonl` 的位置
   - 若多处命中且未指定 `--from` → 列出全部命中位置，要求用户用 `--from` 消歧
   - 若零处命中 → 报错退出
3. 校验 source 与 target 不同；同则提示 no-op 退出
4. 定位源文件：`<source_projects_dir>/<work_dir_hash>/<session_id>.jsonl`
5. 检查目标位置 `<target_projects_dir>/<same hash>/<session_id>.jsonl` 是否已存在：
   - 已存在且 `--force` 未给 → 报错退出
   - 已存在且 `--force` 给了 → 覆盖（先 mv .gits-bak 再 cp，成功后 rm bak）
6. 创建目标的 `<work_dir_hash>/` 子目录（如不存在）
7. `cp -p` 源文件到目标位置（保留 mode/mtime）
8. 写 manifest.lastImport 记录
9. 输出：源/目标路径、文件大小、行数、mtime

**并发安全**：
- 若源文件正在被某个 claude 进程 append（即 source 账户有 binding 在 `--resume` 这个 session 且活跃中）→ `cp` 读到的是当时一个时刻的快照；后续 source 端的新写入不进 target；可接受（用户 import 后 target 端会从快照点继续，不需要"实时同步"）
- 若 target 账户已有 binding 在 `--resume` 同 id（罕见，因为目标端可能从未见过这个 id）→ `--force` 会覆盖，但需要先 kill 那个 binding 内的 claude 进程；spec 简化：`--force` 时 ghost log WARN 让用户先停 binding，仍允许覆盖（用户责任）；V2 可加 binding-aware 安全检查

**import 后用户行为**：
- 已有 binding：用户 `gits account switch <target> --binding <id>` 把某个 binding 切到 target，binding 的 `cli_session_id` 不变，下次 respawn `claude --resume <session_id>` 找到 target 目录里的 JSONL 副本
- 新 binding：用户在 Discord 发 `/start` 等创建新 binding，UI 提供 session picker 选择导入的 session id；binding 创建时 `claude_account = target`

**Discord 暴露**：V1 不暴露 `/account-import`（涉及多账户路径、消歧逻辑、在 Discord 上交互不舒服）；V2 候选

### D14: ghost Hooks 跨账户传播

**问题**：ghost 在 `~/.claude/settings.json` 安装 hook（如 `gits hook --install` 写入的 hook 字段）。严格隔离后，per-account binding 用 `~/.claude-{name}/settings.json`，不会自动获得这些 hooks。

**V1 策略**：
- `gits account add` 末段：从 `~/.claude/settings.json` 提取 ghost 拥有的 hook 段（identifiable by hook 名字前缀或 ghost-marker），merge 到 `~/.claude-{name}/settings.json`
- 如目标 settings.json 已存在同名 hook → 保留目标版本（用户可能后续手动调过）
- 如 `~/.claude/settings.json` 不存在 hook 段（用户从未 install） → 跳过（账户 settings 维持 OAuth login 时 claude CLI 创建的默认值）
- `gits hook --install` 提供新 flag `--all-accounts`：迭代每账户 settings 写入；不带 flag 时仅写 `~/.claude/settings.json`（向后兼容）
- `gits hook --uninstall --all-accounts`：对称移除

**幂等**：以 hook 名字（matcher）为去重 key；多次 add 不重复添加。

### D15: --capture-current 自动迁移现有 binding

**问题**：用户跑 `gits account add personal --capture-current` 之前，所有 binding 是 `claude_account=None`，使用 `~/.claude/projects/`。capture 后，`~/.claude/projects/` 内容被复制到 `~/.claude-personal/projects/`。如果 binding 仍是 `None`：
- 它们继续向 `~/.claude/projects/<id>.jsonl` 写
- 而 `~/.claude-personal/projects/<id>.jsonl` 是同一 session 的快照，独立演进
- 双源风险：用户后续切到 `personal` 账户又切回 None，session 历史在两个文件间分叉

**V1 解法**：`--capture-current` 自动迁移所有 `claude_account=None` 的 binding 为 `claude_account=<new_name>`：
1. capture 完成后扫描 state.json
2. 对每个 `claude_account=None` 的 binding：设 `claude_account = <new_name>`
3. **不**重启 binding（避免一次性 kill 所有 claude）
4. binding 下次自然 respawn（被切换、被 HealthMonitor 恢复、用户手动重启）即生效新路径
5. 输出："已自动迁移 N 个现有 binding 到账户 <new_name>"

向后兼容剧透：用户**不**跑 `--capture-current`（首个 add 用 OAuth login 模式）→ 现有 binding 仍是 `None`，并存使用 `~/.claude/`。这种情况下严格隔离的多账户与既有 binding 是分离世界，互不干扰。

### D16: Discord `/account-switch` 自动 import 当前 session

**动机**：strict isolation 模式下，binding 切到新账户后默认看不到原账户的会话历史。CLI 用户通过两步流程（`gits account import` → `gits account switch`）解决；Discord 用户没有便利的 import 命令（且本设计也不暴露 `/account-import`），手动操作冗长。

**Discord 上下文有 CLI 没有的便利**：channel → binding 映射已知，binding 的 `cli_session_id` 已知，source 账户（binding 当前 `claude_account`）已知，target 账户（命令参数）已知——所有 import 路径计算都不需要用户输入。

**`/account-switch <name>` 触发的自动 import 逻辑**（在 `switch_account(auto_import=True)` 锁内、kill 之后、改字段之前执行）：

```
if binding.cli_session_id is None:
    skip                                  # binding 从未跑过 claude
elif binding.claude_account == target:
    skip                                  # 当前已是目标账户，整个 switch 也是 no-op
else:
    source_path = layout.session_jsonl(
        claude_account=binding.claude_account,
        work_dir=binding.work_dir,
        session_id=binding.cli_session_id,
    )
    target_path = layout.session_jsonl(
        claude_account=target,
        work_dir=binding.work_dir,
        session_id=binding.cli_session_id,
    )
    if not source_path.exists():
        skip                              # session 文件不存在——claude 还没真正写过；切换后 --resume 会失败或新建
    elif target_path.exists():
        skip                              # 保留 target 已有版本；不覆盖（见下文"为什么不覆盖"）
    else:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)   # cp -p：保留 mode + mtime
        vault.record_import(at=now, session_id=binding.cli_session_id, from=binding.claude_account, to=target)
        log.info("auto-import session %s from %s to %s during switch", ...)
```

**为什么"target 已有就保留"而非覆盖**：

考虑这种使用流程：
```
T0: binding b1 在账户 A，session abc，对话到第 10 轮
T1: /account-switch B → 自动 import abc(A→B)；B 接着对话到第 20 轮（B 端 abc 现在 20 轮）
T2: /account-switch A → A 端 abc 还是 10 轮（B-side 那 10 轮没回流，正确）；A 端继续到第 15 轮
T3: /account-switch B → B 端 abc 还是 20 轮，binding 直接 --resume 接上 B 端 timeline
```

如果 T3 时强制覆盖 B 端的 abc，就把 B-side 的 10 轮对话（11–20 轮）抹掉了——丢数据。**保留 target 已有 = 每账户各自累积自己的 timeline**，与 strict isolation 的设计意图一致。

需要"用 source 最新状态强制覆盖 target"的少见场景（用户明确想把当前账户的最新对话搬到另一账户）→ 走本机 CLI：
```bash
gits account import <session_id> --from A --to B --force
gits account switch B --binding b1
```

CLI 是"显式 + 可控"的入口，Discord 是"快捷 + 智能默认"的入口。

**用户可见反馈**：

切换完成后 Discord embed 内含 import 状态：
- `imported`：`✅ 已切换到 work — session abc 已从 alice 导入。对话历史保留。`
- `skipped (target had session)`：`✅ 已切换到 work — work 上已有此 session 的历史（未覆盖）。`
- `skipped (no source)`：`✅ 已切换到 work — 未找到当前 session 文件，新对话从空开始。`
- `skipped (no session_id)`：`✅ 已切换到 work — binding 尚未启动过 session。`

**并发安全**：auto-import 必须在 kill claude **之后**做。如果在 kill 前 cp，source 端 claude 可能在 cp 期间继续写入新行，导致 cp 拿到部分行（极罕见但可能）；kill 后 cp 拿到的是 source 端的最终状态。

**与 D13 的关系**：D13 的 `import_session()` primitive 是 CLI 入口；本节描述 Discord 路径直接调用更轻量的内联逻辑。两者共用同一 layout helper 解析路径；写 `manifest.lastImport` 与日志 schema 一致。可以让两者共用同一 `_do_copy(source, target, force=False)` helper，但调用点和上下文不同。

**为什么不也给 CLI `gits account switch` 加 auto-import 默认**：CLI 的设计哲学是"显式 + 可控"——用户在本机有时间看输出、读文档、明确指定。auto-import 默认会让"我只想换账户、不想动 session 文件"的高级用户惊讶；显式 `gits account import` + `switch` 两步更安全。Discord 反向：UX 即时性优先，"smart default"换 friction-free。两套语义共存。

未来如有需要，CLI 可加 `gits account switch --auto-import` flag（V2 候选）。

## Risks / Trade-offs

| # | 风险 | 缓解 |
|---|---|---|
| R1 | macOS keychain 是否按 `CLAUDE_CONFIG_DIR` 隔离 | spike V1：双账户分别 login → 比对 keychain 内容；若不隔离，文件优先策略仍可工作（claude 从 `<CLAUDE_CONFIG_DIR>/.credentials.json` 读，keychain 仅备份） |
| R2 | claude CLI 内部某些路径硬编码 `~/.claude/` 不走 `CLAUDE_CONFIG_DIR` | spike V2：长 session 跑后看 `~/.claude/` mtime 变化与 isolated dir 实际产物 |
| R3 | 多 binding 共用同一账户时 OAuth refresh 写文件竞态 | claude CLI 用 atomic write（tmpfile + rename），并发 refresh 是 last-write-wins，不会损坏文件 |
| R4 | OAuth Usage API schema 漂移（Anthropic 改字段） | 实测过的字段名见 D8；ghost 仅依赖 `five_hour` / `seven_day`（核心）+ `extra_usage`，其它内部代号字段忽略；未知字段静默跳过 |
| R5 | OAuth Usage 端点未来下线或迁移 | 端点 / beta header 通过环境变量可覆盖（D8）；Anthropic 升级 beta 版本时改 `GITS_OAUTH_BETA_HEADER` 即可 |
| R6 | beta header `oauth-2025-04-20` 失效 | 同 R5；ghost log WARN "OAuth beta header rejected" 引导用户升级 ghost / 改 env |
| R6.1 | access token 过期，ghost 不主刷 | 401 时报 `stale credentials` 引导用户跑 claude 触发 CLI 自带刷新；list 行其它字段（name/email/binding 数）仍展示，不阻塞 |
| R7.1 | 用户期望"导入后两边自动同步"，实际是一次性快照 | UX 文案明确："import 是快照拷贝，import 后两个账户的同 id session 各自演进，互不影响" |
| R7.2 | `gits account import` 时源 session 正在被 source 账户的 claude append | `cp` 是单时刻读取，目标拿到的是当时快照；后续 source 端写入不进 target——这是预期行为，不是 bug |
| R7.3 | import 时目标已有同 id session 文件且 `--force` 覆盖时该 session 在 target 账户活跃中 | 写 WARN 提示用户先停 target binding；不强制 kill（避免破坏其它 binding 的并发操作）；用户责任 |
| R7.4 | strict isolation 下 plugins/ 等被复制多份占磁盘 | 接受——多账户用户磁盘通常够；plugins 通常 20–50MB，每账户一份可承担；V2 可加 `gits account add --shared-plugins` 覆盖此默认 |
| R7.5 | hooks 跨账户复制不一致（用户手动改了某账户的 hook） | `gits account add` 时尊重目标已有 hook（不覆盖）；`gits hook --install --all-accounts` 强制全部一致；用户在二者间选择 |
| R7.6 | `--capture-current` 后 binding 自动迁移引起意外行为 | 输出明确告知"已迁移 N 个 binding"；用户可手动改回 `None`（V2 加 `gits account unlink-binding <id>` 命令；V1 用户编辑 state.json） |
| R7.7 | Discord auto-import 时 target 已有"陈旧"同 id 文件，用户期望"看到最新状态"但实际拿到旧 timeline | UX 文案明确："work 上已有此 session 的历史（未覆盖）"，并附 hint "如需用 alice 的最新状态覆盖 work，运行 `gits account import abc-123 --from alice --to work --force`" |
| R7.8 | source claude 在 auto-import 进行中仍未 reap 干净（罕见） | auto-import 在 D5 流程的 step 5——已通过 SIGTERM/SIGKILL+1s reap 确认 source claude 退出（同 step 4）；cp 期间 source 进程不可能存在 |
| R7 | 切换账户后 prompt cache miss（cache 按 org 隔离） | 仅性能影响，第一轮额外延迟；不影响功能 |
| R8 | symlink 被用户误删或指向错位置 | `list` / `add` 启动校验：缺失自动补；错指仅 WARN（不自动改） |
| R9 | 用户在 ghost 外跑 `claude auth login`（无 CLAUDE_CONFIG_DIR）写到 `~/.claude/` | 不影响——`~/.claude/` 仍是默认目录，ghost 管理的是 `~/.claude-{name}/` |
| R10 | shared `settings.json` 修改导致跨账户串扰 | 设计意图——所有账户用同一 settings；如需账户级隔离，未来扩展（V2） |
| R11 | API 频繁查询导致服务端限速 | 60s 内存缓存 + 不做后台轮询；用户主动触发 list 时调用 |
| R12 | 旧版 ghost 留下 `~/.gits/subscriptions/` / `~/.gits/active-env.sh` | 启动检测 + WARN 引导手动迁移；旧 `gits subscription` 子命令保留 deprecation 提示 |
| R13 | `gits account remove` 时 binding 用着的账户 | 显式拒绝并列出违规 binding，要求用户先 switch |

## Migration Plan

### 旧方案产物清理
- `gits subscription *` 子命令保留，输出 deprecation 提示，引导改用 `gits account *`
- 启动时检测 `~/.gits/subscriptions/` / `~/.gits/active-env.sh` → log WARN
- V1 完成后下线旧路径

### 用户首次启用
```
1. gits account add personal --capture-current
   → mkdir ~/.claude-personal/ (0700) + 写 .gits-managed marker
   → rsync -a ~/.claude/ ~/.claude-personal/  （全量拷贝包括 .credentials.json、projects、settings、todos、plugins...）
   → 提取 metadata（email/orgId/...）写 manifest
   → manifest.default = "personal"
   → 自动迁移所有 claude_account=None 的现有 binding → claude_account="personal"
2. gits account add work
   → mkdir ~/.claude-work/ + marker
   → CLAUDE_CONFIG_DIR=$HOME/.claude-work claude auth login   （子进程跑 OAuth）
   → 提取 metadata 入 manifest
   → 把 ~/.claude/settings.json 的 ghost hooks 段 merge 到 ~/.claude-work/settings.json
3. gits account list
   → 对 personal/work 分别调 /api/oauth/usage 展示用量
4. gits account switch work --binding <id>   或   /account-switch work
   → kill binding 的 claude → 改字段 → respawn with CLAUDE_CONFIG_DIR=$HOME/.claude-work
   → manifest.default = "work"
5. gits account import <session_id> --to work
   → 自动定位 session_id 在哪个账户的 projects/ 下（多处命中需 --from 消歧）
   → cp 该 JSONL 到 ~/.claude-work/projects/<same hash>/<id>.jsonl
   → 之后 work 账户的 binding 可 --resume <session_id> 从该快照点继续
```

### 回滚
```
rm -rf ~/.claude-*/  ~/.gits/accounts/
```
- binding `claude_account` 字段保留但失效（fall back to `~/.claude/`）
- `~/.claude/` 自始至终未动（capture 是 cp 不是 mv）；用户登录态、JSONL、hooks 全部原地保留
- 立即恢复改前行为

### 向后兼容守门
- `~/.gits/accounts/manifest.json` 不存在 → 不加载 AccountVault；launcher 不注入 `CLAUDE_CONFIG_DIR`
- `SessionBinding.claude_account` 字段缺失 → 默认 `None`
- 删除 `~/.gits/accounts/` 后下次启动行为与改前一致

## Verification spikes

Phase 0 编码完成后跑：

**V1：keychain 隔离行为**
- `CLAUDE_CONFIG_DIR=/tmp/test-A claude auth login`（账户 A）
- `CLAUDE_CONFIG_DIR=/tmp/test-B claude auth login`（账户 B）
- `security find-generic-password -s 'Claude Code-credentials' -w` 比对内容
- 同时跑 `CLAUDE_CONFIG_DIR=/tmp/test-A claude -p "ping"` & `CLAUDE_CONFIG_DIR=/tmp/test-B claude -p "ping"`，验证不串身份
- 失败回退：keychain last-write-wins 但文件 canonical → 文档说明

**V2：CLAUDE_CONFIG_DIR 覆盖范围**
- isolated dir 跑长 session，监听 `~/.claude/` mtime
- 列出 isolated dir 实际产物
- 输出：哪些子项需要在 `~/.claude-shared/` 提前 stub

**V3：跨账户 resume 活体测试**
- 注册 A、B 两账户
- A binding 创建 session "你是谁，我现在和谁对话"
- 切换 binding 到 B，`--resume <id>` 追问
- 验证 B endpoint 接受 A 创建的历史

**V4：OAuth Usage API 实测**（已完成 2026-04-27，见 §Reference §C）
- ✅ Usage 端点 `https://api.anthropic.com/api/oauth/usage`，需要 `anthropic-beta: oauth-2025-04-20` header
- ✅ 响应 schema 抓到（见 D8）
- 残留任务：故意打满限额 → 再调用 usage 看 utilization 是否封顶 100% 或溢出；记录限额状态下的响应形态（V4.1，可选）

> 不再验证 refresh 端点——ghost 不实现内部刷新（D11）。

## Open Questions

- P1：keychain 是否按 CLAUDE_CONFIG_DIR 隔离 → V1 spike 决定（不隔离也可工作）
- ~~P2：API 响应 schema~~ → ✅ 实测确认（D8）
- ~~P3：OAuth refresh 端点路径与请求格式~~ → 不再相关（D11 决定不实现 ghost 内部 refresh）
- P4：`/accounts` 在 Discord 输出多账户 usage 时是否会触发多次 API 调用导致限速 → 实践中观察；必要时加更长缓存或合并展示
- P5：`~/.claude/.credentials.json` 是否应该 `--capture-current` 后被删除？V1 不删

## Resolved Decisions

- **不做自动切换**：仅由用户主动触发 `switch`
- **per-binding 切换无全局锁**：仅 binding 级 asyncio.Lock，不同 binding 并发切换合法
- **manifest.default 自动追踪**：首次 add 即设；每次 switch 自动更新为新目标；用户无需显式 `default` 命令
- **CLI 5 条命令**：add / list / switch / remove / import
- **Discord 仅 list + switch**：add/remove/import 限本机
- **`--capture-current` 仅首次有效**：第二次直接报错
- **配额查询主动 API**：调 `/api/oauth/usage`，60s 内存缓存
- **`rateLimitedUntil` 字段取消**：API 现查现用，不持久化"猜测的"重置时间
- **switch 不做配额预检**：用户基于 list 输出自行判断；切换永远立即执行
- **不实现 ghost 内部 OAuth refresh**：401 时报 `stale credentials` 引导跑 claude；claude CLI 自带 refresh 路径已经够用（D11）
- **严格隔离 strict isolation**：每账户目录全部子项是真实文件/目录，不 symlink、不 bind mount、不共享；跨账户使用同一 session 走显式 `gits account import` 拷贝（D2/D13）
- **Discord `/account-switch` 自动 import**：Discord 上下文已知 binding/session/source/target，auto-import 是"target 没有 session 文件时拷贝；已有时保留"的 smart default；CLI 不变（仍显式两步）（D16）
- **`--capture-current` 自动迁移现有 binding**：把所有 `claude_account=None` 的 binding 改为 `claude_account=<new>`，避免新旧路径双源（D15）
- **ghost 代码改为账户感知路径解析**：`AccountLayout` 提供 `projects_dir(account)` 等映射，`launcher` 与 `JsonlMonitor` 由 binding 的 `claude_account` 决定路径（D12）
- **hooks 跨账户传播**：`gits account add` 末段从 `~/.claude/settings.json` 复制 ghost 拥有的 hook 段；`gits hook --install --all-accounts` 强制全部一致（D14）

---

## Reference Material

### §A claude CLI env vars（v2.1.121 二进制 strings）

`CLAUDE_CONFIG_DIR` — config 根目录覆盖（默认 `~/.claude/`），本方案核心。

`CLAUDE_CODE_OAUTH_TOKEN` / `CLAUDE_CODE_OAUTH_REFRESH_TOKEN` / `CLAUDE_CODE_OAUTH_SCOPES` — env 模式凭据注入；旧方案使用，本方案**不再需要**。

§A.3 OAuth refresh token rotation — 服务端会在 refresh 时返回新 RT，旧 RT 在短窗口仍可用（实测 9h 内多次 rotate）。ghost 的 refresh 客户端用新 token 写回文件即可，无需特殊处理 rotation。

### §B 为什么放弃 swap-file / env-source-file 方案

旧方案核心矛盾：claude CLI 凭据来源是 4 元组（env > keychain > file > IDE inherit），ghost 想在外部"换凭据"必须同时应对：

1. keychain-first 读优先：keychain 有 entry 时永远屏蔽文件读
2. OAuth refresh 写文件不写 keychain：refresh 后 keychain 与文件不一致
3. 服务端 rotate refresh token：vault 快照过期需同步
4. SSH 上下文 keychain 写失败但读成功：守护进程下凭据冻结
5. 必须先 kill 所有 claude 才能 swap 文件 → 全局锁 + 全 binding 同时停摆

每条都需要专门 mitigation；且基本设计假设是"全局只有一个 active 账户"，与多 binding 多账户并行的诉求冲突。

`CLAUDE_CONFIG_DIR` 把上述写入面切到 per-account 目录：每账户凭据独立、refresh 写各自目录、不同账户进程并发跑各读各的、无 swap 时序问题。"切换"退化为"改 binding 字段 + respawn"。

### §C OAuth Usage API 验证记录（2026-04-27）

**验证目标**：本方案核心依赖 `https://api.anthropic.com/api/oauth/usage`；编码前必须确认端点真实可用、鉴权方式、响应 schema。

**实测命令**：
```bash
# 用本机已登录的 access token
TOKEN=$(jq -r '.claudeAiOauth.accessToken' ~/.claude/.credentials.json)

# 1. Usage（无 beta header → 401）
curl -H "Authorization: Bearer $TOKEN" https://api.anthropic.com/api/oauth/usage
# → HTTP 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth authentication is currently not supported."...

# 2. Usage（带 beta header → 200）
curl -H "Authorization: Bearer $TOKEN" \
     -H "anthropic-beta: oauth-2025-04-20" \
     https://api.anthropic.com/api/oauth/usage
# → HTTP 200，schema 见 D8
```

**从 claude v2.1.121 二进制提取的常量**（`grep -aoE` on `/Users/<user>/.local/share/claude/versions/2.1.121`）：
- `mSH = "oauth-2025-04-20"`（beta header constant；本方案使用）
- 同时观察到 `TOKEN_URL: ${api_origin}/v1/oauth/token` + `Aq().CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"`（claude CLI 自身的 refresh 端点；ghost **不**使用——见 D11）

**结论**：Usage 端点真实可用，schema 已样本化，spec 编码可基于此进行。Anthropic 升级 beta 版本时改 `GITS_OAUTH_BETA_HEADER` env；下线端点时改 `GITS_OAUTH_USAGE_URL` env。

### §D 为什么放弃被动模式匹配

旧方案 `quota_patterns.yaml` + matcher + debouncer + notifier 是被动检测：

1. 模式串依赖 CLI 文案版本，CLI 升级即失效
2. reset 时间正则提取脆弱（消息文案多样）
3. 200ms 反向信号 + 连续两帧 debounce 调参困难
4. 误报 / 漏报需要 spike 才能调到能用
5. 即使捕获到限额也只能写入"我们猜的" `rateLimitedUntil`——这个时间戳无法验证

OAuth Usage API 是权威源：直接问账户当前有多少额度、何时重置；无需推断、无需 debounce、无版本依赖。
