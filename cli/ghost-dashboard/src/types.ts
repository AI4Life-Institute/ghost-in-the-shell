// Shared dashboard types (mirrors ui/src/types.ts)

export type WidgetType  = 'conversation' | 'chart' | 'compute' | 'files';
export type WidgetSize  = '2x1' | '2x2';
export type WidgetState = 'running' | 'review' | 'done' | 'idle';

export interface PipelineAction {
  label: string;
  event: string;
  payload?: Record<string, unknown>;
}

export interface ConversationConfig { mode: 'chat' | 'tail'; }
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
  id: string; name: string; path: string; mimeType: string;
  size: number; createdAt: string; isNew?: boolean;
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
