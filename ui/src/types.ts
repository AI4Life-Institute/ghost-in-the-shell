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
