import type { AgentDashboard } from './types';

const STORAGE_PREFIX = 'ghost_dashboard_';

export function loadDashboard(agentId: string): AgentDashboard | null {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + agentId);
    if (!raw) return null;
    return JSON.parse(raw) as AgentDashboard;
  } catch {
    return null;
  }
}

export function saveDashboard(agentId: string, dashboard: AgentDashboard): void {
  try {
    // persist config only, strip runtime-only fields
    const toSave: AgentDashboard = {
      agentId: dashboard.agentId,
      widgets: dashboard.widgets.map(w => ({
        id: w.id,
        type: w.type,
        size: w.size,
        agentId: w.agentId,
        state: 'idle',   // reset runtime state on save
        title: w.title,
        config: w.config,
      })),
      pipeline: dashboard.pipeline,
    };
    localStorage.setItem(STORAGE_PREFIX + agentId, JSON.stringify(toSave));
  } catch {
    // localStorage may be unavailable in Tauri — silently ignore
  }
}

export function deleteDashboard(agentId: string): void {
  localStorage.removeItem(STORAGE_PREFIX + agentId);
}
