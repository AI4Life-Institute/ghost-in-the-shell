# Tasks

按 Phase 推进。每个任务后面括注实现来源（spec requirement 或 design decision）。

> **方案重写说明**：本 change 经历多轮重写——
> 1. 旧 V1：全局凭据热切换 + 被动模式匹配（已废弃）
> 2. 中间版：CLAUDE_CONFIG_DIR + symlink 共享 `~/.claude-shared/`（已废弃，因 session 写入冲突风险）
> 3. **当前 V1**：CLAUDE_CONFIG_DIR + 严格隔离（每账户独立目录，无共享）+ session import 命令 + 账户感知 ghost 代码 + OAuth Usage API 主动查询
>
> 已弃 SubscriptionVault / SwitchPrimitive / active-env.sh / 全局锁 / vault writeback / QuotaPatternMatcher / QuotaNotifier / `rateLimitedUntil` / `~/.claude-shared/` / 跨账户 symlink / `~/.claude/<x>` symlink 替换。

---

## Phase 0：基础底座

> Phase 0 完成后用户已可手动管理多账户。

### 0.1 旧方案产物清理与 deprecation

- [x] 0.1.1 `src/gits/core/subscription.py` / `cli_subscription.py` / `quota.py` / `quota_notifier.py` 标记 deprecated；保留代码但不再加载
- [x] 0.1.2 启动时若检测到 `~/.gits/subscriptions/` / `~/.gits/active-env.sh` / `~/.claude-shared/` → log WARN（design: Migration Plan）
- [x] 0.1.3 launcher 移除 `active_env_file` 内部逻辑；保留参数签名为 None default 兼容
- [x] 0.1.4 现有 `gits subscription` 子命令树保留，每条命令头部加 deprecation 提示
- [x] 0.1.5 删除 `~/.gits/quota_patterns.yaml` 相关加载/匹配代码（旧方案产物）
- [x] 0.1.6 单测：旧路径不再触发任何 vault 加载/env-file 写入/pattern matcher 加载

### 0.2 目录布局与初始化（AccountLayout，严格隔离）

- [x] 0.2.1 实现 `AccountLayout`：`account_dir(name)`、`marker_path(name)`、`legacy_claude_dir() = ~/.claude`、`projects_dir(claude_account)`、`settings_file(claude_account)`、`credentials_file(claude_account)`、`all_active_projects_dirs() -> list[Path]`（spec: Per-Account Isolated Config Directory；design: D1/D12）
- [x] 0.2.2 `create_account_dir(name, *, capture_current=False)`：
  - 创建 `~/.claude-{name}/`（mode 0700）+ 写 `.gits-managed` marker（0644）
  - 若 `capture_current=True`：`rsync -a ~/.claude/ ~/.claude-{name}/`（**包含** .credentials.json）
  - 若 `capture_current=False`：保持空目录，调用方负责跑 `claude auth login` 填充
  - （spec: Add a fresh account / First account init from existing login；design: D3）
- [x] 0.2.3 名称校验 `validate_account_name(name)`：正则 `^[a-z0-9][a-z0-9_-]{0,31}$`；拒绝预留字 `shared`（spec: Account name validation）
- [x] 0.2.4 marker 校验 `is_ghost_managed(path) -> bool`（design: D10）
- [x] 0.2.5 启动时孤儿态检测：manifest 中存在但目录不存在 → log WARN（spec: Vault accidentally points to missing dir）
- [x] 0.2.6 启动时旧产物检测：`~/.claude-shared/` 存在 → log WARN 提示这是旧设计残留（design: Migration Plan 旧方案产物清理）
- [x] 0.2.7 capture-current 失败回滚：rsync 退出非零时删除已部分创建的 `~/.claude-{name}/`（含 marker），manifest 不写，迁移不跑（spec: Capture-current rsync failure cleans up partial directory）
- [x] 0.2.8 用户 SIGINT 中断时同 0.2.7 cleanup 逻辑；用 `try/except KeyboardInterrupt + finally` 模式覆盖 rsync subprocess 中断（spec: User interrupts capture-current mid-rsync）
- [x] 0.2.9 单测：create_account_dir 不覆盖已存在；marker 写入 0644；非法 name 抛错；conflict with non-managed dir 抛错；`capture_current=True` 全量拷贝包括 credentials；`capture_current=False` 创建空目录；rsync 失败 → 残留目录被清理；SIGINT 中断 → 残留目录被清理

### 0.3 Account 元数据与 manifest（AccountVault）

- [x] 0.3.1 设计 `~/.gits/accounts/manifest.json` schema（**不含** `rateLimitedUntil`）：
  ```json
  { "default": "<name>|null",
    "accounts": [
      { "name", "email", "orgId", "subscriptionType", "config_dir",
        "lastUsed", "tags": [] }
    ],
    "lastSwitch": { "at", "binding_id", "from", "to", "reason" },
    "lastImport": { "at", "session_id", "from", "to" } }
  ```
  （spec: Account Vault；design: D13）
- [x] 0.3.2 实现 `AccountVault`：atomic read/write、`list()`/`get(name)`/`add()`/`remove()`/`set_default(name)`/`record_switch(...)`/`record_import(...)`
- [x] 0.3.3 metadata 提取 helper：从 `.credentials.json` JWT-decode 取 `email`/`orgId`/`subscriptionType`；失败时容错
- [x] 0.3.4 自动 default：首次 add 自动 set；switch 后自动更新；remove 删了 default 时按 lastUsed 倒序选下一个
- [x] 0.3.5 单测：vault CRUD + atomic write + 0600 + JWT decode 容错 + 自动 default 切换 + lastImport 记录

### 0.4 binding ↔ account 字段 + 自动迁移

- [x] 0.4.1 `SessionBinding` 新增字段 `claude_account: str | None = None`（spec: Per-Binding Account Field）
- [x] 0.4.2 state.json 反序列化兼容：缺失字段默认 `None`（spec: Backward compat for state schema）
- [x] 0.4.3 新建 binding 时 `manifest.default` 设置 → `claude_account = manifest.default`
- [x] 0.4.4 序列化只在 `claude_account is not None` 时写入字段
- [x] 0.4.5 `migrate_legacy_bindings(target_account)`：扫 state.json，把所有 `claude_account=None` 的 binding 设为 `target_account`，atomic write；返回迁移计数（design: D15；spec: Capture-current auto-migrates bindings）
- [x] 0.4.6 `--capture-current` 流程末段调用 0.4.5；输出 "已迁移 N 个现有 binding 到 <name>"
- [x] 0.4.7 单测：旧 state.json 加载 → 字段默认 None；新建 binding 走 default；迁移幂等（重复跑迁移计数为 0）

### 0.5 launcher 改造为账户感知

- [x] 0.5.1 `resolve_cli(cli, claude_account=None)`：根据 `claude_account` 解析 session_path 与 config_dir；尊重 alias 的显式 `session_path` / `config_dir` 覆盖（spec: Launch Command Honors Account；design: D12）
- [x] 0.5.2 `build_launch_command(cli, session_id, work_dir, *, claude_account=None)`：仅 `resolved.base_type == "claude"` 且 `claude_account is not None` 注入 `CLAUDE_CONFIG_DIR={shlex.quote(account_dir)}` 前缀（spec: Inject for claude-base CLI with account）
- [x] 0.5.3 `get_session_file(work_dir, cli, session_id, *, claude_account=None)`：解析为该账户的 `projects/` 路径再查找
- [x] 0.5.4 `_discover_claude_sessions(work_dir, *, session_path=None, claude_account=None)`：未传 session_path 时从 layout 解析；codex/copilot/opencode 的实现不受影响（它们各自有路径常量）
- [x] 0.5.5 移除 `active_env_file` 注入逻辑（保留参数签名）
- [x] 0.5.6 现有 `cli_aliases.config_dir` 与 `cli_aliases.session_path` 保持作用——若别名同时设了又有 `claude_account`，account 优先
- [x] 0.5.7 单测：build_launch_command 含/不含 CLAUDE_CONFIG_DIR 各分支；codex/copilot/opencode 不注入；alias path 与 account 共存场景；`get_session_file(claude_account="x")` 走 `~/.claude-x/projects/`

### 0.6 JsonlMonitor 改造为账户感知

- [x] 0.6.1 保留单 `_projects_path` 兜底（默认 `~/.claude/projects`，处理 `claude_account=None` binding）（design: D12）
- [x] 0.6.2 新增 `_account_paths: dict[str, Path]`：account_name → projects dir
- [x] 0.6.3 `register_account_path(account_name, path)` / `unregister_account_path(account_name)` 接口
- [x] 0.6.4 监听循环：每 tick 扫描 `_projects_path` ∪ `_account_paths.values()`；offsets 字典 key 已是 `(channel_id, file_path)`，天然区分账户
- [x] 0.6.5 启动时由 engine 根据 manifest 注入所有当前已注册账户的路径
- [x] 0.6.6 `gits account add` / `remove` 在 vault 写入末段调用 monitor 的注册/反注册接口
- [x] 0.6.7 单测：双账户分别写 JSONL，monitor 都能看到；偏移量按账户独立

### 0.7 切换原语 `switch_account(binding_id, name, *, auto_import=False)`

- [x] 0.7.1 实现 per-binding `asyncio.Lock` 缓存（key=binding_id），lock 持有期覆盖整个切换
- [x] 0.7.2 校验目标账户存在；不预检配额
- [x] 0.7.3 给 binding 的 tmux pane 发 `C-c`，等 300ms
- [x] 0.7.4 枚举 binding 内 claude 进程 pid → SIGTERM → 5s 内未死升级 SIGKILL → 1s reap；仍存活则 abort 并释放锁
- [x] 0.7.5 **（新）auto_import=True 路径**（kill 之后、改字段之前执行）：解析 source/target 文件路径（用 layout）；按 D16 规则决定 skip / cp（source 不存在 / target 已存在 / 同账户 / cli_session_id 为 None 时跳过）；cp 用 `shutil.copy2` 保留 mtime；成功 cp 后 vault.record_import；返回 import status 给调用方供 UX 反馈
- [x] 0.7.6 修改 `binding.claude_account = target`，原子写 state.json
- [x] 0.7.7 vault.record_switch + accounts[target].lastUsed + manifest.default = target
- [x] 0.7.8 respawn：调 `launcher.build_launch_command(claude_account=target)`，spawn 到该 binding 的 tmux pane
- [x] 0.7.9 respawn 失败：标记 binding `respawn_failed`，记录 manifest.lastSwitch.partial=true
- [x] 0.7.10 释放 lock；返回 `SwitchResult(success, import_status, ...)` 给 Discord/CLI 调用方组装消息
- [x] 0.7.11 单测（基础）：成功路径；kill 超时 abort；respawn 失败标记；并发同 binding 排队；并发不同 binding 并行
- [x] 0.7.12 单测（auto_import）：source 文件存在 + target 不存在 → 触发 cp + 记录 import；source 不存在 → skip + import_status="no_source"；target 已存在 → skip + import_status="target_existed"；cli_session_id=None → skip + import_status="no_session"；同账户 → 整个 switch 是 no-op
- [x] 0.7.13 单测（auto_import + force_overwrite=False 默认）：明确不会覆盖 target 已有文件；用 fixture 在 target 写"特征字符串"，switch 后特征字符串仍在
- [x] 0.7.14 单测（auto-import + 抢救语义）：auto_import 流程在 kill 之后执行——构造 fixture 让 source claude 在 cp 时还活着是不可能的（kill 已确保），但仍验证调用顺序：mock kill 返回成功 → 验证 cp 后才发生

### 0.8 OAuth Usage API 客户端（仅 GET usage，不做刷新）

> 端点已实测确认（design.md §Reference §C，2026-04-27）。
> 明确不做 ghost 内部 OAuth refresh（D11）——401 即报 stale，让用户跑 claude 让 CLI 自己刷。

- [x] 0.8.1 实现 `oauth_usage.query(account_name) -> Usage | UsageError`：从 `~/.claude-{name}/.credentials.json` 读 access token，GET `https://api.anthropic.com/api/oauth/usage` 带 `Authorization: Bearer ...` + `anthropic-beta: oauth-2025-04-20`
- [x] 0.8.2 解析响应核心字段：`five_hour.{utilization, resets_at}` / `seven_day.{utilization, resets_at}` / `seven_day_opus` / `seven_day_sonnet` / `extra_usage`；其它内部代号字段忽略；未知字段静默跳过
- [x] 0.8.3 60s 内存缓存：键 `(account_name, hash(access_token))`；token rotation 后自然失效
- [x] 0.8.4 错误处理：网络 / 5xx → `unavailable`；401 → `stale credentials, run claude --resume to refresh`；429 → `rate-limited`；其它 → 通用错误展示
- [x] 0.8.5 配置覆盖：`GITS_OAUTH_USAGE_URL` / `GITS_OAUTH_BETA_HEADER`
- [x] 0.8.6 显式不实现：模块**不**含任何 POST 路径、不读 refreshToken、不写 `.credentials.json`；单测静态守门
- [x] 0.8.7 单测：query 成功路径；网络错误 graceful；401 渲染为 `stale credentials` 不刷；缓存命中跳过 HTTP；schema 漂移不报错

### 0.9 Session Import 原语

- [x] 0.9.1 实现 `import_session(session_id, *, to, from_=None, force=False) -> ImportResult`（design: D13）
- [x] 0.9.2 source 自动定位：扫 `~/.claude/projects/` + 所有 `~/.claude-*/projects/` 下含 `<session_id>.jsonl` 的位置
- [x] 0.9.3 多处命中且未指定 `--from` → 报错并列出所有命中路径
- [x] 0.9.4 source == target → 无操作 + 提示 message
- [x] 0.9.5 目标已存在同 id 文件且 `--force` 未给 → 报错退出；给了 `--force` → mv 备份 → cp → 成功后 rm bak
- [x] 0.9.6 创建目标的 `<work_dir_hash>/` 子目录（如不存在）
- [x] 0.9.7 `cp -p` 保留 mode/mtime
- [x] 0.9.8 写 manifest.lastImport
- [x] 0.9.9 输出：源/目标路径、文件大小、行数、mtime
- [x] 0.9.10 `--force` 时若 target 账户有 binding 仍 `claude_account==target` 且 `cli_session_id==<id>` 且 claude 进程活跃 → log WARN + 控制台警告，仍允许覆盖（V1 用户责任，V2 候选 `--strict`）（spec: Forced overwrite warns if a target binding is currently using that session）
- [x] 0.9.11 单测：单源命中；多源消歧；目标已存在 + force 行为；source == target；不存在 session_id；同 work_dir_hash 路径正确还原；force + target binding 活跃 → WARN 但允许

### 0.10 ghost Hooks 跨账户传播

- [x] 0.10.1 `gits account add` 末段：从 `~/.claude/settings.json` 提取 ghost 拥有的 hook 段，merge 到 `~/.claude-{name}/settings.json`（design: D14；spec: Ghost Hooks Propagation）
- [x] 0.10.2 ghost-owned 识别复用现有 `_HOOK_COMMAND_SUFFIX = "gits hook"`（`__main__.py:1166`）：command 等于该值或以 `/gits hook` 结尾；不引入新 marker 字段
- [x] 0.10.3 同 `(matcher, command)` 元组在目标已存在 → 跳过（幂等）
- [x] 0.10.4 同 matcher 但不同 command（用户改了 ghost hook） → 保留用户版本，log INFO
- [x] 0.10.5 settings.json 缺失或 JSON 解析失败 → log WARN + 跳过该文件 + 继续其它账户；不阻断父操作（spec: Malformed settings.json does not abort propagation）
- [x] 0.10.6 `gits hook --install --all-accounts` flag：迭代 manifest.accounts 每账户 settings 写入 + `~/.claude/settings.json`；不带 flag 时仅写 `~/.claude/settings.json`
- [x] 0.10.7 `gits hook --uninstall --all-accounts`：对称移除
- [x] 0.10.8 单测：account add 时 hook 复制；--all-accounts 幂等；目标已有同 (matcher, command) 不重复；目标 matcher 同但 command 不同 → 保留用户版；malformed JSON 不抛错只 WARN；source 缺失 → 跳过 propagation 不报错

### 0.11 CLI 子命令族（5 条）

- [x] 0.11.1 `gits account add <name> [--capture-current]`：见 D3 流程
- [x] 0.11.2 `gits account add <name> --capture-current` 仅在 manifest 为空时合法
- [x] 0.11.3 `gits account list`：每账户一行，含 name/email/subscriptionType/lastUsed/usage(API)/binding 数；default 标记
- [x] 0.11.4 `gits account list` 内部：调 oauth_usage.query 并展示；失败的账户行展示 `usage: <error>` 不阻塞
- [x] 0.11.5 `gits account switch <name> --binding <id>`：调 switch_account
- [x] 0.11.6 `gits account remove <name>`：拒绝若有 binding 仍 claude_account==name；列出违规 binding
- [x] 0.11.7 `gits account import <session_id> --to <name> [--from <name>] [--force]`：调 import_session
- [x] 0.11.8 短别名 `gits acct` 等价 `gits account`
- [x] 0.11.9 argparse 注入 `__main__.py`，与现有子命令并列
- [x] 0.11.10 单测：每个子命令的 happy path + 错误路径

### 0.12 Discord 受限暴露

- [x] 0.12.1 注册 `/accounts`：调 oauth_usage.query 拉每账户用量，Discord embed 格式；高亮当前 channel binding 的账户
- [x] 0.12.2 注册 `/account-switch <name>`：autocomplete 候选账户名；channel→binding 解析；调 `switch_account(..., auto_import=True)`（**Discord 默认 auto_import**，CLI 不传）（design: D16）
- [x] 0.12.3 channel 未绑定 → 错误消息提示先 `/start`
- [x] 0.12.4 当前 binding 已是该账户 → no-op + 提示
- [x] 0.12.5 切换中 Discord 占位（`⚙️ 切换到 <name>...`）
- [x] 0.12.6 切换完成 Discord embed 含 import status：根据 `SwitchResult.import_status` 渲染对应文案（imported / target_existed / no_source / no_session）（spec: Discord auto-import status reported）
- [x] 0.12.7 沿用现有 access check 机制
- [x] 0.12.8 显式拒绝任何 Discord 路径下的 add/remove/import 触发（其中 `import` 子命令本身也仅 CLI 暴露——Discord auto-import 是切换流程内联的副作用，不是独立命令）
- [x] 0.12.9 单测：注册的 slash command 列表只含 `/accounts` + `/account-switch`
- [x] 0.12.10 单测：Discord 路径调 switch_account 时 auto_import=True；CLI 路径不传（默认 False）

### 0.13 向后兼容守门

- [x] 0.13.1 启动时检测 `~/.gits/accounts/manifest.json` 是否存在；不存在时跳过 AccountVault 初始化
- [x] 0.13.2 binding `claude_account=None` → launcher 无 CLAUDE_CONFIG_DIR 注入，行为与改前一致
- [x] 0.13.3 删除 `~/.gits/accounts/` 后下次启动回滚
- [x] 0.13.4 单测：vault 缺失时 ghost 行为与旧版完全一致

### 0.14 P0 机器验证 spike

- [ ] 0.14.1 V1 keychain 隔离行为：双账户分别 login → 比对 keychain 内容；并发跑两个进程验证不串身份
- [ ] 0.14.2 V2 CLAUDE_CONFIG_DIR 覆盖范围：长 session 跑完后看 `~/.claude/` mtime 变化与 isolated dir 实际产物
- [ ] 0.14.3 V3 跨账户 import + resume 活体测试：A 账户创建 session "你是谁"→ import to B → B binding `--resume <id>` 追问 → 验证 B endpoint 接受 A 创建的历史
- [x] 0.14.4 V4 OAuth Usage API 实测：响应字段、错误格式、beta header 必需性 — **已完成**（2026-04-27，详见 design.md §Reference §C）
- [ ] 0.14.5 V4.1 残留：故意打满限额后再调 usage，看 `utilization` 是否封顶 100% 或溢出

---

## Phase 1：端到端验证 + 文档

### 1.1 端到端

- [ ] 1.1.1 双账户 + 双 binding 同时跑（A binding 用 X、B binding 用 Y）→ 互不串扰；JSONL 写入物理分离；ghost 监听双方都看到
- [ ] 1.1.2 切换单 binding 账户 → 该 binding 历史保留（依赖 V3 通过 + 用户已 import 该 session）或开启新 session
- [ ] 1.1.3 一账户被多 binding 共用 → OAuth refresh 写文件不串
- [ ] 1.1.4 `gits account add` 期间不影响任一现有 binding（除 `--capture-current` 触发的迁移）
- [ ] 1.1.5 `gits account list` 调 API 展示用量；故意弄过期 token 验证 401 渲染为 `stale credentials`
- [x] 1.1.6 删除 `~/.gits/accounts/` 后 ghost 回退到原行为
- [x] 1.1.7 `gits account import <session_id> --to <target>` 全流程：自动定位、消歧、覆盖、目录创建
- [ ] 1.1.8 import 后 target 账户的 binding `--resume` 该 session 成功；之后双方各自写各自的副本互不干扰
- [x] 1.1.9 `--capture-current` 自动迁移现有 binding：迁移前 `claude_account=None`、迁移后 `claude_account=<new>`，binding 不重启但下次自然 respawn 走新路径
- [x] 1.1.10 验证 ghost 已有代码（jsonl_monitor / launcher / hook installer）在账户感知改造后正确工作：per-account binding 写入的 JSONL 被 ghost 监听、`get_session_file` 找到正确路径、hooks 在新账户生效

### 1.2 文档

- [x] 1.2.1 README 增加多账户章节：基本用法、目录布局、CLI/Discord 命令一览、严格隔离的 UX 含义
- [x] 1.2.2 `gits account --help` 文案
- [ ] 1.2.3 安全说明：凭据存储位置、0600 权限、跨机器同步注意事项
- [ ] 1.2.4 旧方案迁移说明：从 `~/.gits/subscriptions/` / `~/.claude-shared/` 迁到 `~/.gits/accounts/` 的步骤
- [x] 1.2.5 OAuth API endpoint 配置说明：环境变量 `GITS_OAUTH_USAGE_URL` / `GITS_OAUTH_BETA_HEADER` 用法；以及"401 时为何不主刷"
- [ ] 1.2.6 Session import 教程：什么时候用 import、import 与 switch 的关系、import 不是双向同步而是一次性快照

---

## REPLACED（旧方案任务，不再执行）

> 保留作为历史参照。

- ~~Phase −1（active-env.sh / writeback path / keychain best-effort）~~
- ~~全局凭据互斥锁（fcntl.flock）~~ → 改为 per-binding asyncio.Lock
- ~~SwitchPrimitive (file swap)~~ → CLAUDE_CONFIG_DIR 注入
- ~~待处理事件队列（旧自动切换状态机）~~ → 不存在自动切换
- ~~`gits subscription` 子命令族~~ → `gits account`
- ~~`gits subscription auto-switch on|off`~~ → 自动切换功能整体取消
- ~~`gits subscription use`~~ → 取消（无 rateLimitedUntil 后无意义）
- ~~`gits account default` / `repair` / `status` / `use`~~ → 行为内化
- ~~`/subscriptions` + `/sub-switch` Discord 命令~~ → `/accounts` + `/account-switch`
- ~~Phase 1.1 QuotaPatternMatcher~~ → OAuth Usage API
- ~~Phase 1.2 监听器 quota 接入~~ → 不再做被动检测
- ~~Phase 1.3 QuotaNotifier~~ → 不存在配额通知器
- ~~`rateLimitedUntil` manifest 字段~~ → 取消
- ~~`~/.claude-shared/` 共享目录~~ → 严格隔离，每账户独立
- ~~`~/.claude-{name}/<item>` symlink → `~/.claude-shared/<item>`~~ → 真实文件/目录
- ~~`~/.claude/<shared_subitem>` symlink 替换（旧 D12 路径统一）~~ → ghost 代码改为账户感知（新 D12）
- ~~`convert_legacy_claude_to_symlinks` / `.gits-bak` 备份机制~~ → 不再触碰 `~/.claude/` 共享子项
