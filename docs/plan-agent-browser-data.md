# Ghost Agent System — Design Plan

> Ghost 是本地 AI 自动化平台。用户用 Markdown 文件定义 **Tools** 和 **Skills**，Ghost 调度 Skills 产生 **Agents** 运行，日志按 Agent 隔离，UI 实时可见。
>
> **核心原则：业务脚本原地不动，定义全部基于本地文件，无本地 ID 系统。**

---

## 一、概念模型

```
Tool          一个 CLI 命令的 Markdown 说明
                可以是系统工具（openclaw、nlm）或本地脚本（npx tsx ...）
                有没有源码无所谓，Ghost 只关心怎么调用

Skill         一个 Markdown 文件，描述 trigger + steps + guard
                trigger: Loop（cron/interval）| Reactive（polling/webhook）
                steps: 有序 Tool 调用
                guard: 出问题时哪个 Coding Agent 介入（默认 ops session）

Agent         一个正在运行（或历史）的实例，分两类：
                Coding Agent  — tmux 里跑 coding CLI，AI 驱动，自主决策（Build 面板）
                Runner Agent  — 按 Skill steps 顺序执行，默认有 Coding Agent 守护

Data          Agent 运行产出：日志文件 + artifacts + run 元数据（SQLite）
```

### 两类 Agent

| | Coding Agent | Runner Agent |
|---|---|---|
| 谁决定步骤 | coding CLI（AI 自主） | Skill md（确定性顺序） |
| AI 角色 | 主导执行 | 默认守护，失败时介入 |
| 适合场景 | 新任务、探索性工作 | 迁移现有自动化、定期任务 |
| 支持的 CLI | claude-code / codex / opencode / ... | 任意（guard session 指定）|
| 面板位置 | Build（已有） | Agents（新增）|
| trigger | 手动（未来支持调度） | Loop / Reactive |
| 日志 | `~/.gits/agents/<name>/` | `~/.gits/agents/<name>/` |

**Runner Agent 默认是 Guarded 的。** 失败时自动把错误上下文发给绑定的 Coding Agent session，由 AI 决定如何处理（修复/重试/停止/跳过）。只有显式设置 `guard: never` 才关掉守护。

---

## 二、文件目录结构

```
~/.gits/
  tools/                          ← Tool 定义（Markdown）
    discord-run.md
    aifinance-digest.md
    openclaw.md                   ← 系统工具，无源码，描述用法即可
    nlm.md
    comfyui-queue.md
    mj-run.md

  skills/                         ← Skill 定义（Markdown）
    discord-monitor.md
    aifinance-digest-pre.md
    aifinance-daily.md
    comfyui-queue.md
    mj-queue.md
    ...

  agents/                         ← 运行日志（按 Agent 隔离）
    discord-monitor/
      current.log                 ← 软链接到最新 run 日志
      2026-03-15T05:00.log
      2026-03-14T05:00.log
    aifinance-digest-pre/
      current.log
      2026-03-15T05:00.log

  gits.db                         ← 只存 run 元数据（时间、状态、日志路径）
```

---

## 三、Tool Markdown 格式

**本地项目脚本：**
```markdown
# Discord Run
Poll watched Discord channels for new messages.

## Command
npx tsx src/index.ts run --once

## Working Directory
~/projects/mcp-comfyui-service/cli/discord

## Environment
NODE_ENV=production

## Timeout
600
```

**系统工具（无源码）：**
```markdown
# OpenClaw Snapshot
Take a snapshot of the current browser page element tree.

## Command
openclaw browser snapshot --profile {profile}

## Args
- profile: browser profile name (e.g. discord, nash-ai)

## Timeout
30
```

**要点：**
- `Working Directory` 和 `Environment` 可选，系统工具无需填
- Ghost 启动时执行 `zsh -c env` 继承完整 shell 环境，解决 `.app` PATH 丢失问题
- `{placeholder}` 表示调用时传入的参数

---

## 四、Skill Markdown 格式

### Guard 配置

所有 Skill 默认开启守护：

```markdown
## Guard
on: failure    ← 默认值，可不写
               失败时将错误上下文发给 Coding Agent session
```

可选配置：

```markdown
## Guard
session: aifinance    # 指定 session 名，默认用 ops session
on: failure           # failure（默认）| always（每步汇报）| never（关闭）
```

`on: never` 关闭守护，纯确定性执行：

```markdown
## Guard
on: never
```

---

### Skill 示例

**Loop + 默认守护（最常用）：**
```markdown
# 盘前分析
每个工作日早上 5:00 PST 运行盘前财经分析，生成 NotebookLM 报告并发到 Discord。

## Trigger
loop:
  schedule: "0 5 * * 1-5"

## On Failure
retry:
  max: 2

## Steps
1. aifinance-digest --pre
```

**Reactive adaptive polling：**
```markdown
# Discord Monitor
持续监控 Discord 频道，市场时间每 1 分钟、非市场时间每 30 分钟轮询一次。

## Trigger
reactive:
  polling:
    peak_seconds: 60
    off_seconds: 1800
    timezone: America/New_York
    peak_start: "09:30"
    peak_end: "16:00"
    weekdays_only: true

## On Failure
continue

## Steps
1. discord-run
```

**持续运行 daemon：**
```markdown
# MJ Daemon
持续运行 Midjourney 队列处理，崩溃后自动重启。

## Trigger
reactive:
  always_on: true

## On Failure
restart

## Steps
1. npx tsx src/index.ts run
   working_dir: ~/projects/mcp-comfyui-service/cli/midjourney
```

**指定 session 全程监督（AI 每步可见）：**
```markdown
# GS 报告下载
每天下载高盛报告，登录逻辑可能变化，需要 AI 全程监督。

## Trigger
loop:
  schedule: "0 7 * * 1-5"

## Guard
session: aifinance
on: always

## Steps
1. aifinance-nashai daily
```

---

### On Failure 语义

| 值 | 含义 |
|---|---|
| `retry: max N` | 失败后重试 N 次，超过则停止并触发 Guard |
| `continue` | 跳过本轮，下次 trigger 继续（Reactive 适用）|
| `restart` | 进程崩溃后自动重启（daemon 适用）|
| `stop` | 失败即停 |
| `notify` | 失败通知（Discord / 系统通知），不重试 |

---

## 五、Runner Agent 执行流程

```
Skill trigger 到时间
  → 创建 run 记录，生成日志路径
  → 在 tmux 新建（或复用）session
  → 按 steps 顺序执行 Tool 命令
      每条命令实时写入 ~/.gits/agents/<skill>/current.log
      同时 emit agent_log IPC 事件 → UI 实时显示
  → 某步失败
      on_failure = retry  → 重试，超过次数后走 Guard
      on_failure = continue → 记录，进入下一轮
      on_failure = restart → 重启进程
      其他 → 直接走 Guard
  → Guard 介入（on: failure 或 on: always）
      将 [skill 定义 + 失败步骤 + 错误输出 + 相关 Tool md] 发给 Coding Agent session
      Coding Agent 决策并执行（修复命令 / 重试 / 跳过 / 停止）
      Guard 结果写回 run 记录
  → 全部完成 → 更新 run 状态，emit agent_run_done
```

**Guard 的上下文包含：**
- Skill 的 md 文件内容（目标描述）
- 失败的 step 和 Tool 定义
- 错误输出（log tail）
- 建议的处理方式（retry / skip / fix and retry / abort）

---

## 六、日志结构

```
~/.gits/agents/<skill-name>/
  current.log                     ← 最新 run（实时写入）
  2026-03-15T05:00:00.log         ← 历史 run，文件名 = 启动时间 ISO8601
  2026-03-14T05:00:00.log
```

- 实时写入，`tail -f current.log` 随时可用
- 不经过 SQLite，长时间运行不丢日志
- 保留最近 30 个 run 或 7 天，自动 rotate

**SQLite 只存元数据：**
```sql
CREATE TABLE runs (
  id           TEXT PRIMARY KEY,   -- 启动时间 ISO8601，也是日志文件名前缀
  skill_name   TEXT NOT NULL,
  agent_type   TEXT NOT NULL,      -- runner | coding
  started_at   TEXT NOT NULL,
  finished_at  TEXT,
  exit_code    INT,
  status       TEXT NOT NULL,      -- running | success | failed | guarded
  log_path     TEXT NOT NULL,
  guard_log    TEXT                -- Guard 介入时的决策记录（JSON）
);

CREATE TABLE artifacts (
  id           TEXT PRIMARY KEY,
  run_id       TEXT NOT NULL,
  type         TEXT NOT NULL,
  path         TEXT NOT NULL,
  label        TEXT,
  metadata     TEXT,
  created_at   TEXT NOT NULL
);
```

---

## 七、现状映射（pm2 → Ghost Runner Agent）

| pm2 进程 | Skill 文件 | Trigger | On Failure | Guard |
|---|---|---|---|---|
| `discord-poll` | `discord-monitor.md` | Reactive adaptive | continue | 默认（failure）|
| `aifinance-digest-pre` | `aifinance-digest-pre.md` | Loop `0 5 * * 1-5` | retry 2 | 默认 |
| `aifinance-digest-mid` | `aifinance-digest-mid.md` | Loop `0 11 * * 1-5` | retry 2 | 默认 |
| `aifinance-digest-post` | `aifinance-digest-post.md` | Loop `0 14 * * 1-5` | retry 2 | 默认 |
| `aifinance-daily` | `aifinance-daily.md` | Loop `0 7 * * 1-5` | retry 2 | `on: always`（登录变化）|
| `comfyui-queue-all` | `comfyui-queue.md` | Loop `0 * * * *` | stop | 默认 |
| `comfyui-moment` | `comfyui-moment.md` | Loop `30 * * * *` | stop | 默认 |
| `mj-run` | `mj-run.md` | Reactive always_on | restart | 默认 |
| `mj-queue-all` | `mj-queue.md` | Loop `*/10 * * * *` | stop | 默认 |

---

## 八、Ghost 后端新增组件

| 组件 | 路径 | 职责 |
|---|---|---|
| `GitsDB` | `src/gits/storage/db.py` | SQLite run 元数据 CRUD |
| `SkillLoader` | `src/gits/core/skill_loader.py` | 解析 `~/.gits/skills/*.md` 和 `tools/*.md` |
| `SkillRunner` | `src/gits/core/skill_runner.py` | Loop/Reactive 调度，执行 Tool 命令，写日志，Guard 触发 |
| IPC 扩展 | `src/gits/__main__.py` | 新增 skills / agents / data 命令和事件 |

**SkillRunner 核心逻辑：**
```python
async def _run(self, skill):
    run_id = now_iso8601()
    log_path = f"~/.gits/agents/{skill.name}/{run_id}.log"
    db.insert_run(run_id, skill.name, 'runner', log_path)

    for step in skill.steps:
        exit_code = await self._exec_step(step, log_path)
        if exit_code != 0:
            handled = await self._on_failure(skill, step, exit_code, log_path)
            if not handled:
                break  # Guard 决定终止

    db.finish_run(run_id, status)
    emit({'event': 'agent_run_done', ...})

async def _on_failure(self, skill, step, exit_code, log_path):
    # 按 on_failure 策略处理
    # 若需要 Guard：把上下文注入 Coding Agent session
    guard_session = skill.guard.session or 'ops'
    context = build_guard_context(skill, step, log_path)
    await engine.inject_message(guard_session, context)
    # 等待 Coding Agent idle，读取结果
    ...
```

**新增 IPC 命令：**
```
skills        → 读 ~/.gits/skills/*.md → emit skills_list
agents        → db.query_runs() → emit agents_list
agent_log     → stream ~/.gits/agents/<skill>/current.log
skill_run     → SkillRunner.run_now(skill_name)
skill_pause   → 暂停 skill（内存状态）
```

**新增 IPC 事件：**
```json
{ "event": "skills_list",    "skills": [...] }
{ "event": "agents_list",    "agents": [...] }
{ "event": "agent_log",      "skill": "...", "run_id": "...", "line": "..." }
{ "event": "agent_run_done", "skill": "...", "run_id": "...", "status": "success|failed|guarded" }
```

---

## 九、迁移步骤（pm2 → Ghost）

### 前提条件
- [ ] `SkillRunner` 启动时执行 `zsh -c env` 继承完整 shell 环境
- [ ] `ecosystem.config.cjs` 保留原地，不删除

### 迁移顺序（stop-then-add，禁止并行双跑）

**Step 1：写 Tool md 文件**（零风险）
在 `~/.gits/tools/` 创建每个 CLI 的说明，Ghost UI Test run 验证

**Step 2：迁移 Loop Skills**（逐一，从影响最小开始）
```
comfyui-moment → comfyui-queue-all → mj-queue-all →
aifinance-digest-post → mid → pre → aifinance-daily

每步：写 skill md → pm2 stop → Ghost 启用 → 验证第一次触发
失败：pm2 start ecosystem.config.cjs → Ghost pause → 排查
```

**Step 3：迁移 Reactive Skills**
- `mj-run`：`always_on + restart`
- `discord-poll`：最后，adaptive polling

**Step 4：清理**
稳定 1 周后 `pm2 delete all`，保留 `ecosystem.config.cjs` 作为文档

### 回滚
```bash
pm2 start /path/to/ecosystem.config.cjs
# Ghost 里 pause 对应 Skill 避免双跑
```

---

## 十、Open 问题

1. **ops session**：Ghost 是否维护一个专用的 ops Coding Agent session 作为所有 Skill 的默认 Guard？还是 Guard 默认用 Build 面板里当前活跃的 session？建议：有专用 ops session 更干净，用户可以在 Settings 里指定。

2. **Guard 决策格式**：Coding Agent 处理 Guard 上下文后，Ghost 怎么读取它的决策？约定 Coding Agent 输出特定格式（如 `GUARD: retry` / `GUARD: skip` / `GUARD: abort`），还是 Ghost 解析自然语言输出？

3. **Skill md 解析**：用 frontmatter YAML（`---` 块）还是自由 Markdown + AI 解析？建议：frontmatter 做结构化字段（trigger/guard/on_failure），正文做自然语言描述（供 Guard 理解上下文）。

4. **data_records（v2）**：Ghost 提供 `gits record <collection> <json>` CLI 供 Tool 脚本主动写入结构化数据，v1 暂不实现。
