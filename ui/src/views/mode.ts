import { AGENTS } from '../data/agents';
import { state } from '../state';

export function setMode(mode: string): void {
  state.curMode = mode;
  document.querySelectorAll('.mode').forEach(m => m.classList.remove('on'));
  const modeEl = document.querySelector(`.mode[data-mode="${mode}"]`);
  if (modeEl) modeEl.classList.add('on');
  document.querySelectorAll('.view').forEach(v => v.classList.remove('on'));
  const viewEl = document.getElementById('view-' + mode);
  if (viewEl) viewEl.classList.add('on');
  // update badge visibility
  updateAgentBadge();
}

export function updateAgentBadge(): void {
  const badge = document.getElementById('agents-badge');
  if (!badge) return;
  const running = countRunningAgents();
  badge.textContent = running > 0 ? `[${running}]` : '';
}

export function countRunningAgents(): number {
  let n = 0;
  AGENTS.browser.profiles.forEach(p => p.agents.forEach(a => { if (a.status === 'running') n++; }));
  AGENTS.loop.forEach(a => { if (a.status === 'running') n++; });
  AGENTS.reactive.forEach(a => { if (a.status === 'listening' || a.status === 'running') n++; });
  return n;
}

export function countWaitingAgents(): number {
  let n = 0;
  AGENTS.browser.profiles.forEach(p => p.agents.forEach(a => { if (a.status === 'waiting') n++; }));
  AGENTS.loop.forEach(a => { if (a.status === 'waiting') n++; });
  AGENTS.reactive.forEach(a => { if (a.status === 'waiting') n++; });
  return n;
}

export function updateAgentsWarnBadge(): void {
  const warnEl = document.querySelector('.mode[data-mode="agents"] .mode-badge.warn');
  if (warnEl) (warnEl as HTMLElement).style.display = countWaitingAgents() > 0 ? '' : 'none';
}
