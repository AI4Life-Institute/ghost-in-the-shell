import { describe, it, expect } from 'vitest';
import { validateDashboard } from '../validate.js';
import type { AgentDashboard } from '../types.js';

const VALID_DASHBOARD: AgentDashboard = {
  agentId: 'btc-monitor',
  widgets: [
    {
      id: 'btc-monitor-w1',
      type: 'conversation',
      size: '2x1',
      agentId: 'btc-monitor',
      state: 'idle',
      title: 'Conversation',
      config: { mode: 'chat' },
    },
    {
      id: 'btc-monitor-w2',
      type: 'chart',
      size: '2x1',
      agentId: 'btc-monitor',
      state: 'running',
      title: 'Prices',
      config: { table: 'btc_prices', view: 'chart', range: '24h' },
    },
  ],
};

describe('validateDashboard', () => {
  it('passes a valid config', () => {
    const r = validateDashboard(VALID_DASHBOARD);
    expect(r.ok).toBe(true);
    expect(r.checks.every(c => c.pass)).toBe(true);
  });

  it('fails when agentId is missing', () => {
    const bad = { ...VALID_DASHBOARD, agentId: '' };
    const r = validateDashboard(bad);
    expect(r.ok).toBe(false);
    const check = r.checks.find(c => c.label.includes('agentId'));
    expect(check?.pass).toBe(false);
  });

  it('fails on unknown widget type', () => {
    const bad: AgentDashboard = {
      ...VALID_DASHBOARD,
      widgets: [{ ...VALID_DASHBOARD.widgets[0], type: 'unknown' as any }],
    };
    const r = validateDashboard(bad);
    expect(r.ok).toBe(false);
    const check = r.checks.find(c => c.label.includes('types are known'));
    expect(check?.pass).toBe(false);
    expect(check?.detail).toContain('unknown');
  });

  it('fails when pipeline references missing widgetId', () => {
    const bad: AgentDashboard = {
      ...VALID_DASHBOARD,
      pipeline: [
        { id: 'stage-1', label: 'Step 1', widgetId: 'nonexistent-widget', state: 'idle' },
      ],
    };
    const r = validateDashboard(bad);
    expect(r.ok).toBe(false);
    const check = r.checks.find(c => c.label.includes('pipeline widgetIds'));
    expect(check?.pass).toBe(false);
    expect(check?.detail).toContain('nonexistent-widget');
  });

  it('passes when pipeline references existing widgetIds', () => {
    const good: AgentDashboard = {
      ...VALID_DASHBOARD,
      pipeline: [
        { id: 'stage-1', label: 'Chat', widgetId: 'btc-monitor-w1', state: 'done' },
        { id: 'stage-2', label: 'Data', widgetId: 'btc-monitor-w2', state: 'running' },
      ],
    };
    const r = validateDashboard(good);
    expect(r.ok).toBe(true);
  });

  it('fails on invalid widget size', () => {
    const bad: AgentDashboard = {
      ...VALID_DASHBOARD,
      widgets: [{ ...VALID_DASHBOARD.widgets[0], size: '3x3' as any }],
    };
    const r = validateDashboard(bad);
    expect(r.ok).toBe(false);
    const check = r.checks.find(c => c.label.includes('sizes'));
    expect(check?.pass).toBe(false);
  });

  it('fails when widgets array is empty', () => {
    const bad = { ...VALID_DASHBOARD, widgets: [] as any[] };
    const r = validateDashboard(bad);
    // r.ok must be false — some check must have failed
    expect(r.ok).toBe(false);
    // At least one check must be failing
    expect(r.checks.some(c => !c.pass)).toBe(true);
  });
});
