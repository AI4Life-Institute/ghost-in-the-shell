export interface LogEntry { ico: string; action: string; desc: string; out: string; ts: string; done: boolean; pending?: boolean; }
export interface AgentDetail { running: boolean; steps: number; totalSteps: number; elapsed: string; log: LogEntry[]; hitl: { msg: string; pending: boolean } | null; }
export interface Agent { id: string; name: string; status: string; sub: string; type: string; profile?: string; autoRepaired?: boolean; detail: AgentDetail; }
export interface BrowserProfile { id: string; label: string; agents: Agent[]; }
export interface AgentsData { browser: { profiles: BrowserProfile[] }; loop: Agent[]; reactive: Agent[]; }
export interface SkillParam { key: string; label: string; placeholder: string; }
export interface SkillRunError { msg: string; ai: string; }
export interface SkillRun { status: string; ts: string; params: string; error: SkillRunError | null; elapsed?: string | null; }
export interface Skill { name: string; desc: string; params: SkillParam[]; runs: SkillRun[]; }
export interface DbCollection { id: string; name: string; rows: number; updated: string; icon: string; table: string; sourceAgent?: string; sourceSkill?: string; }
export interface DbCollections { fromAgents: DbCollection[]; fromSkills: DbCollection[]; manual: DbCollection[]; }
export interface TableSourceInfo { type: 'agent' | 'skill'; id: string; }
export interface DataNode { type: 'folder' | 'sqlite' | 'csv'; id: string; name: string; open?: boolean; children?: DataNode[]; tables?: { id: string; name: string; rows: number }[]; tableId?: string; rows?: number; }
export interface Pane { channelId: string | null; terminal: any | null; fitAddon: any | null; ro: ResizeObserver | null; }
export interface Session { channel_id: string; window_name?: string; work_dir?: string; coding_cli?: string; platform?: string; parent_channel_id?: string; window_id?: string; alive?: boolean; created_at?: string; _status?: string; }
export interface RunnerRun { skill_name: string; status: string; started_at?: string; finished_at?: string; run_id?: string; _paused?: boolean; }
export interface SkillDef { name: string; trigger?: { type?: string } | string; on_failure?: string; guard?: { enabled: boolean }; steps?: any[]; }

// ── Dashboard & Widget types ────────────────────────────────────────────────

export type WidgetType  = 'conversation' | 'chart' | 'compute' | 'files';
export type WidgetSize  = '2x1' | '2x2';
export type WidgetState = 'running' | 'review' | 'done' | 'idle';

export interface PipelineAction {
  label: string;
  event: string;
  payload?: Record<string, unknown>;
}

export interface ConversationConfig {
  mode: 'chat' | 'tail';
}

export interface ChartConfig {
  table: string;
  xField?: string;
  yField?: string;
  chartType?: 'line' | 'bar' | 'scatter';
  view?: 'table' | 'chart';
  range?: '1h' | '24h' | '7d' | 'all';
}

export interface ComputeConfig {
  content: string;
  streaming: boolean;
  reviewActions?: PipelineAction[];
}

export interface FileEntry {
  id: string;
  name: string;
  path: string;
  mimeType: string;
  size: number;
  createdAt: string;
  isNew?: boolean;
}

export interface FilesConfig {
  dir: string;
  viewMode?: 'gallery' | 'list';
  selectable?: boolean;
  selected?: string[];
  actions?: PipelineAction[];
  batchActions?: PipelineAction[];
  files?: FileEntry[];
}

export type WidgetConfig = ConversationConfig | ChartConfig | ComputeConfig | FilesConfig;

export interface Widget {
  id: string;
  type: WidgetType;
  size: WidgetSize;
  agentId: string;
  state: WidgetState;
  title?: string;
  config: WidgetConfig;
}

export interface PipelineStage {
  id: string;
  label: string;
  widgetId: string;
  state: WidgetState;
}

export interface AgentDashboard {
  agentId: string;
  widgets: Widget[];
  pipeline?: PipelineStage[];
}
