# Ghost · Agent Dashboard — Spec Summary

> 状态：Draft · 2026-03-16

---

## 1. 核心概念

每个 Agent 拥有独立 dashboard，将对话、数据、文件、计算结果聚合到一个屏幕，消除跨视图拼凑的认知负担。Widget Grid 是主展示单元，HITL 审批内联在 widget 内，不弹 modal。多阶段任务通过 Pipeline 模式串联，每个阶段对应一个 widget，用户确认后 pipeline 继续推进。

---

## 2. 布局

侧边栏三个入口（原来四个）：

```
💻 Code     → tmux terminal pane（完全不动）
🌐 Agents   → Agent Dashboard（新）
📦 Library  → Artifact 库（Skill + Data 合并，基本不动）
```

**Agents 视图布局：**
```
┌──────────┬──────────────────────────────────────────────┐
│  [总览]  │  <Agent 名>  · ● Running  ·  [Stop]          │
│  Agent A ├──────────────────────────────────────────────┤
│  Agent B │                                              │
│  Agent C │         Widget Grid（4列，行高 160px）        │
│  ──────  │                                              │
│  + New   │                                              │
└──────────┴──────────────────────────────────────────────┘
```

**Library 视图布局：**
```
┌──────────────────────────────────────────────────────────┐
│  [Skills]  [Data]                                        │
├──────────────────────────────────────────────────────────┤
│  现有 Skill 列表/详情 或 Data 文件树/表格（tab 切换）      │
└──────────────────────────────────────────────────────────┘
```

- Agent Dashboard：关注"现在在发生什么"（监控、审核、pipeline）
- Library：关注"我有什么"（skills 资产、已收集的 data）
- Header：Agent 名、运行状态、操作按钮（进度显示在此，不占 widget）

---

## 3. Widget 目录

### `conversation` — 对话

| 字段 | 值 |
|------|----|
| 类型 | `conversation` |
| 尺寸 | 2×1（默认）/ 2×2（更多历史） |
| 用途 | 与 agent 双向对话；HITL 审批内联在消息流中 |
| 交互 | 发送消息；右上角切换 terminal tail 模式（`tail -f` 风格原始输出） |

### `chart` — 表格 / 图表

| 字段 | 值 |
|------|----|
| 类型 | `chart` |
| 尺寸 | 2×1（表格）/ 2×2（图表） |
| 用途 | 绑定一张 DB 表，展示数据；AI 自动推断默认视图 |
| 交互 | 表格 ↔ 图表切换；`···` 菜单配置轴字段、图表类型、时间范围；有新写入自动刷新（live badge） |

### `compute` — Markdown 输出

| 字段 | 值 |
|------|----|
| 类型 | `compute` |
| 尺寸 | 2×1（摘要）/ 2×2（长报告） |
| 用途 | 展示 Claude 生成的文本，渲染为 Markdown，支持流式输出 |
| 交互 | 展开 / 复制；需确认时显示审核操作栏（编辑 / 确认继续 / 重新生成） |

### `files` — 文件预览

| 字段 | 值 |
|------|----|
| 类型 | `files` |
| 尺寸 | 2×2 |
| 用途 | 展示 agent 产出文件；图片类 → gallery 模式，文件类 → list 模式 |
| 交互 | 点击图片放大；PDF 显示缩略图；MP3 内联播放；可多选后触发批量操作 |

---

## 4. 交互模式（按钮规则）

每个产出物（图片、视频、文件）下方**常驻** 2-3 个按钮，无需右键。

- 按钮由 agent pipeline 配置，最多 3 个
- 每个按钮对应一个 IPC 事件；widget 不关心具体逻辑
- 选中多个文件后，widget 底部出现批量操作栏

Widget 三种状态：

| 状态 | 含义 | 外观 |
|------|------|------|
| `running` | Agent 正在产出 | 边框闪烁，live badge |
| `review` | 等待人工审核 | 边框高亮（amber），显示操作栏 |
| `done` | 已审核 / 已触发下一步 | 正常边框，badge 变绿 |

---

## 5. Pipeline 模式

Dashboard header 显示多阶段进度：

```
MTV Agent · 第 2 集
● 音乐 ✓  →  ● 选图 ⚠  →  ○ 生成视频  →  ○ 拼接
```

- 点击任意阶段可跳回查看该阶段产出
- 每个阶段绑定一个 widget；用户在 widget 内完成审批后 pipeline 推进
- 典型阶段类型：`compute`（音乐/脚本确认）→ `files(gallery, selectable)`（选图）→ `conversation + files`（生成进度）→ `files(video)`（选视频）→ `compute`（最终预览）

---

## 6. 数据结构（TypeScript，简化版）

```typescript
type WidgetType  = 'conversation' | 'chart' | 'compute' | 'files';
type WidgetSize  = '2x1' | '2x2';
type WidgetState = 'running' | 'review' | 'done' | 'idle';

interface Widget {
  id: string;
  type: WidgetType;
  size: WidgetSize;
  agentId: string;
  state: WidgetState;
  config: FilesConfig | ComputeConfig | Record<string, unknown>;
}

interface FilesConfig {
  dir: string;
  viewMode: 'gallery' | 'list';
  selectable: boolean;
  selected: string[];
  actions: PipelineAction[];
}

interface ComputeConfig {
  content: string;
  streaming: boolean;
  reviewActions?: PipelineAction[];
}

interface PipelineAction {
  label: string;   // 按钮文案，如 "提交到 RunningHub"
  event: string;   // IPC 事件名
  payload?: Record<string, unknown>;
}

interface AgentDashboard {
  agentId: string;
  widgets: Widget[];
  pipeline?: PipelineStage[];
}

interface PipelineStage {
  id: string;
  label: string;
  widgetId: string;
  state: WidgetState;
}
```

---

## 7. 持久化

Layout 存储路径：`~/.gits/dashboards/<agentId>.json`

保存内容：widget 列表、尺寸、位置、config（不含运行时状态）。
