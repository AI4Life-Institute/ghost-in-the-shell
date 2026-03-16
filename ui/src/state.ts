import type { Pane, Session, RunnerRun, SkillDef } from './types';

export const state = {
  activeSessId: null as string | null,
  sessTerminal: null as any,
  allSessions: [] as Session[],
  tmuxSession: 'ghost',
  panes: [
    { channelId: null, terminal: null, fitAddon: null, ro: null },
    { channelId: null, terminal: null, fitAddon: null, ro: null },
  ] as Pane[],
  activePaneIdx: 0,
  devMode: false,
  sessPickerTargetPane: 0,
  curMode: 'build',
  curAgentId: 'nash-reporter',
  curProfileId: 'personal-chrome',
  curSkill: 'market',
  curTableId: 'btc_prices',
  sortCol: null as string | null,
  sortDir: 1,
  filterText: '',
  slashMenuOpen: false,
  toastTimer: null as ReturnType<typeof setTimeout> | null,
  skillRunExpanded: {} as Record<string, boolean>,
  runnerAgents: {} as Record<string, RunnerRun>,
  skillDefs: {} as Record<string, SkillDef>,
};
