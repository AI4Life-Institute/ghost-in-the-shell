# Ghost · Agent Dashboard — UI Design Document

> **状态：** Draft · 2026-03-16

---

## 1. 核心理念

**每个 Agent 拥有自己的 dashboard，把它所有相关的东西聚合在一个屏幕上。**

现有痛点：看运行结果要去 Runner 视图，看数据要去 Data 视图，看 skill 要去 Skill 视图——脑子里要自己拼。

---

## 2. 整体布局

```
┌─────────────────────────────────────────────────────────┐
│  Ghost  ·  ~/myproject  ·  ● 3 active            📸  ⌘K │
├──────────┬──────────────────────────────────────────────┤
│  [总览]  │  BTC Price Monitor  ·  ● Running  ·  [Stop]  │
│  Agent A ├──────────────────────────────────────────────┤
│  Agent B │                                              │
│  Agent C │         Widget Grid                         │
│  ──────  │                                              │
│  + New   │                                              │
└──────────┴──────────────────────────────────────────────┘
```

- 左侧：Agent 列表 + 总览入口
- 顶部 header：Agent 名称、状态、操作按钮（进度在这里，不单独占 widget）
- 主区域：Widget Grid（4列，行高 160px）

---

## 3. Widget 集（MVP）

基于实际运行任务推导（均为 pm2 后台长跑，不是交互式）：

| Agent | 产出物 | 需要的 widget |
|-------|--------|--------------|
| Discord Monitor | messages 表（SQLite） | conversation + chart |
| Midjourney submit | 图片文件（批量） | conversation + files(gallery) |
| Nash-AI daily | Goldman Sachs PDF | conversation + files(list) |
| aifinance digest | NotebookLM 摘要文本 → 发 Discord | conversation + compute |
| ComfyUI jm generate | 图片文件 | conversation + files(gallery) |
| Suno generate | MP3 音频文件 | conversation + files(list) |
| BTC / market data | 数值时间序列 | conversation + chart |

**结论：4 个 widget。** 多数时候是监控产出物，只在 HITL 时变成交互。

`files` widget 内部区分两种展示模式：图片类 → gallery，文件类 → list。

---

### Widget 1：`conversation` — 对话

每个 agent 必有。用户可直接在这里和 agent 说话，双向交互。HITL 审批内联在对话里，不弹 modal。

右上角切换到 **terminal tail**（`tail -f` 风格，最后 N 行原始输出，不需要完整 xterm）。

**对话模式：**
```
💬 BTC Price Monitor          [tail ↗]
───────────────────────────────────────
  Agent  已抓取最新价格 $67,432，存入 btc_prices
         14:00:01

  Agent  RSI 升至 71.4，超过阈值 70，要发送告警吗？
         14:00:03
                              [发送]  [跳过]

    You  帮我看下过去 24h 的趋势
         14:01:22

  Agent  过去 24h 上涨 2.4%，13:00 有明显
         成交量峰值，RSI 从 58 升至 71
         14:01:24
───────────────────────────────────────
  [输入消息…]                      [↑]
```

**terminal tail 模式：**
```
💬 BTC Price Monitor          [对话 ↗]
───────────────────────────────────────
[evaluate] GET /api/btc/price → $67,432.18
[db] INSERT btc_prices → ok
[signal] RSI=71.4 > threshold=70
[done] elapsed: 2s▌
```

**尺寸：** 2×1（默认）/ 2×2（更多历史）

---

### Widget 2：`chart` — 表格 / 图表

绑定一个 DB 表，**表格和图表两种视图切换**。

AI 自动推断默认视图：有 timestamp + 数值列 → 折线图；纯文本列 → 表格。

**表格视图：**
```
📊 btc_prices       [表格] [图表]    [● live]
─────────────────────────────────────────────
timestamp            price
2026-03-16 14:00     $67,432
2026-03-16 13:00     $66,891
2026-03-16 12:00     $67,100
                             [全部 128 行 →]
```

**图表视图：**
```
📊 btc_prices       [表格] [图表]    [● live]
─────────────────────────────────────────────
$68k ┤                         ╭──
$67k ┤      ╭────╮       ╭────╯
$66k ┤ ╭────╯    ╰───────╯
     └──────────────────────────────────────
      10:00   11:00   12:00   13:00   14:00
```

配置（`···` 菜单）：X/Y 轴字段、图表类型（折线/柱/散点）、时间范围（1h/24h/7d/全部）

数据有新写入时自动刷新（live badge）

**尺寸：** 2×1（表格）/ 2×2（图表）

---

### Widget 3：`compute` — Markdown 输出

Agent 调用 Claude 生成的文本，渲染成 Markdown。支持流式输出（生成中末尾显示光标 `▌`）。

```
🤖 HN Digest                         [09:00]
───────────────────────────────────────────
## 今日 Top 10 摘要

**1. OpenAI 发布 o3 mini** — 比 o1 快 3x，
价格降 80%。社区在讨论 benchmark 可信度。

**2. Rust 2025 edition** — async traits
正式稳定...
                             [展开] [复制]
```

**尺寸：** 2×1（摘要）/ 2×2（长报告）

---

### Widget 4：`files` — 文件预览

Agent 产出文件时展示在这里。根据文件类型自动选择展示模式。

**Gallery 模式**（图片类 — Midjourney、ComfyUI 输出）：
```
🖼 midjourney / jeni            [gallery] [list]   [48 files]
───────────────────────────────────────────────────────────
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│ img  │ │ img  │ │ img  │ │ img  │
│      │ │      │ │      │ │ ● new│
└──────┘ └──────┘ └──────┘ └──────┘
┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
│ img  │ │ img  │ │ img  │ │ img  │
└──────┘ └──────┘ └──────┘ └──────┘
```
新到的图片有 `● new` 标记，点击放大预览。

**List 模式**（文件类 — Nash-AI PDF、Suno MP3）：
```
📁 nash_reports                 [gallery] [list]   [3 files]
───────────────────────────────────────────────────────────
📄 gs_q2_2024.pdf      2.3MB   今天 14:43   [预览]
📄 gs_q1_2024.pdf      1.9MB   昨天         [预览]
🎵 suno_track_01.mp3   4.1MB   今天 10:22   [▶ 播放]
```
PDF 点击显示第一页缩略图，MP3 内联播放控件。

**尺寸：** 2×2

---

### Widget 对应场景

| Agent 类型 | 必有 | 按需添加 |
|-----------|------|---------|
| Loop（定时存数据）| conversation + chart | compute |
| Browser（爬取 + 下载文件）| conversation + files | chart |
| Reactive（事件触发）| conversation | chart / compute |
| 有 Claude 调用 | + compute | — |

---

## 4. 交互模式：Review Pipeline

### 核心模式

多阶段 pipeline 中，每个 widget 有三种状态：

| 状态 | 含义 | 外观 |
|------|------|------|
| `running` | Agent 正在产出 | 边框闪烁，live badge |
| `review` | 等待人工审核 | 边框高亮（amber），出现操作栏 |
| `done` | 已审核/已触发下一步 | 正常边框，badge 变绿 |

### 每个产出物直接显示 2-3 个按钮

不用右键，按钮常驻显示在图片/文件下方。**按钮由 agent pipeline 配置，最多 3 个。**

**图片：**
```
┌──────────────────┐
│                  │
│     [图片]       │
│                  │
└──────────────────┘
[✓ 用这张] [变体] [✕]
```

**视频：**
```
┌──────────────────┐
│  [视频缩略图]    │
│  ▶ 00:12         │
└──────────────────┘
[✓ 加入剪辑] [重生成] [✕]
```

**文件列表里：**
```
📄 gs_q2_2024.pdf   2.3MB   14:43   [预览] [存档] [✕]
🎵 track_01.mp3     4.1MB   10:22   [▶ 播放] [使用] [✕]
```

每个按钮对应一个 IPC 事件，agent 那边接着跑。按钮文案和事件由 agent 配置，widget 本身不关心具体逻辑。

**批量操作栏**（选中多个后出现在 widget 底部）：
```
已选 3 张  [提交到 RunningHub]  [提交到 ComfyUI]  [清除]
```

### `compute` widget 的审核模式

当 Claude 生成内容需要确认后才继续时：

```
🤖 MTV Script · 第 2 集              ⚠ 待确认
───────────────────────────────────────────────
## 分镜脚本

**0:00-0:15** 开场：海边日落，角色从远处走来
**0:15-0:30** 近景：角色回眸，配合音乐节拍

[✏️ 编辑]  [✓ 确认，继续生成]  [✗ 重新生成]
```

### Pipeline 整体视图

Dashboard header 显示当前 pipeline 进度：

```
MTV Agent · 第 2 集
● 音乐 ✓  →  ● 选图 ⚠  →  ○ 生成视频  →  ○ 拼接
                   ↑ 当前卡在这里
```

点击任意阶段可以跳回查看该阶段的产出。

### 典型 MTV 制作 Pipeline

```
Stage 1: generate_music
  widget: compute（播放器 + 歌词）
  review: [✓ 用这首] [↺ 重新生成]

Stage 2: select_character_images
  widget: files(gallery, selectable)
  review: 选 N 张 → [提交到 ComfyUI]

Stage 3: comfyui_generate_video
  widget: conversation（进度）+ files(gallery，视频帧预览）
  review: 自动，等完成

Stage 4: review_videos
  widget: files(gallery, selectable, video preview)
  review: 选好的 → [提交拼接]

Stage 5: splice
  widget: conversation + compute（最终视频预览）
  review: [发布] [存档]
```

---

## 5. Widget 数据结构

```typescript
type WidgetType = 'conversation' | 'chart' | 'compute' | 'files';
type WidgetSize = '2x1' | '2x2';
type WidgetState = 'running' | 'review' | 'done' | 'idle';

interface Widget {
  id: string;
  type: WidgetType;
  size: WidgetSize;
  agentId: string;
  state: WidgetState;   // 控制外观和交互模式
  config: WidgetConfig;
}

// files widget 支持选择模式
interface FilesConfig {
  dir: string;
  viewMode: 'gallery' | 'list';
  selectable: boolean;          // 是否可以多选
  selected: string[];           // 已选文件 id
  actions: PipelineAction[];    // 选完之后可以触发什么
}

// 每个 action 对应 pipeline 的下一步
interface PipelineAction {
  label: string;               // "提交到 RunningHub"
  event: string;               // IPC 事件名
  payload?: Record<string, unknown>;
}

// compute widget 支持审核模式
interface ComputeConfig {
  content: string;
  streaming: boolean;
  reviewActions?: PipelineAction[];  // 如果需要确认才能继续
}

interface AgentDashboard {
  agentId: string;
  widgets: Widget[];
  pipeline?: PipelineStage[];  // 可选：定义多阶段流程
}

interface PipelineStage {
  id: string;
  label: string;
  widgetId: string;   // 对应哪个 widget
  state: WidgetState;
}
```

Layout 持久化：`~/.gits/dashboards/<agentId>.json`

---

## 5. 总览页

所有 agent 的缩略卡，点击进入对应 dashboard。

```
┌─────────────────────────────────────────────┐
│ ● BTC Price Monitor              [Loop · ▶] │
│ 上次：14:00 · 存了 1 行 · next in 4m        │
│ [chart: btc_prices]                          │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│ ⚠ Discord Notifier               [failed]   │
│ 403 权限错误 · 需要处理                       │
└─────────────────────────────────────────────┘
```

---

## 6. 后续扩展（不在 MVP）

- 拖拽调整 widget 位置和大小
- AI 自动推荐 widget 组合
- `metric` widget — 单个 KPI 数字
