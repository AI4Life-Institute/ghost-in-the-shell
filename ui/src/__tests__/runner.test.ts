import { describe, it, expect, beforeEach } from 'vitest';
import { state } from '../state';
import type { RunnerRun } from '../types';

describe('renderRunnerCard()', () => {
  beforeEach(() => {
    state.skillDefs = {};
  });

  it('renders skill name', async () => {
    const { renderRunnerCard } = await import('../views/runner');
    const run: RunnerRun = { skill_name: 'BTC Monitor', status: 'success', started_at: '2026-03-15T14:00:00' };
    const html = renderRunnerCard(run);
    expect(html).toContain('BTC Monitor');
  });

  it('renders success badge', async () => {
    const { renderRunnerCard } = await import('../views/runner');
    const run: RunnerRun = { skill_name: 'Test', status: 'success' };
    const html = renderRunnerCard(run);
    expect(html).toContain('✓ Success');
  });

  it('renders failed badge', async () => {
    const { renderRunnerCard } = await import('../views/runner');
    const run: RunnerRun = { skill_name: 'Test', status: 'failed' };
    const html = renderRunnerCard(run);
    expect(html).toContain('✗ Failed');
  });

  it('renders running badge', async () => {
    const { renderRunnerCard } = await import('../views/runner');
    const run: RunnerRun = { skill_name: 'Test', status: 'running' };
    const html = renderRunnerCard(run);
    expect(html).toContain('▶ Running');
  });

  it('shows pause button when not paused', async () => {
    const { renderRunnerCard } = await import('../views/runner');
    const run: RunnerRun = { skill_name: 'Test', status: 'done', _paused: false };
    const html = renderRunnerCard(run);
    expect(html).toContain('⏸ Pause');
  });

  it('shows resume button when paused', async () => {
    const { renderRunnerCard } = await import('../views/runner');
    const run: RunnerRun = { skill_name: 'Test', status: 'done', _paused: true };
    const html = renderRunnerCard(run);
    expect(html).toContain('▶ Resume');
  });

  it('renders trigger badge from skillDefs', async () => {
    const { renderRunnerCard } = await import('../views/runner');
    state.skillDefs['BTCMonitor'] = { name: 'BTCMonitor', trigger: { type: 'loop' } };
    const run: RunnerRun = { skill_name: 'BTCMonitor', status: 'done' };
    const html = renderRunnerCard(run);
    expect(html).toContain('Loop');
  });

  it('formats last run time from ISO date', async () => {
    const { renderRunnerCard } = await import('../views/runner');
    const run: RunnerRun = { skill_name: 'Test', status: 'done', started_at: '2026-03-15T14:00:00' };
    const html = renderRunnerCard(run);
    expect(html).toContain('Last run:');
  });
});

describe('renderRunnerGrid()', () => {
  it('shows empty message when no agents', async () => {
    document.body.innerHTML = '<div id="runner-grid"></div>';
    state.runnerAgents = {};
    const { renderRunnerGrid } = await import('../views/runner');
    renderRunnerGrid();
    expect(document.getElementById('runner-grid')!.textContent).toContain('No runner agents');
  });

  it('renders cards for each agent', async () => {
    document.body.innerHTML = '<div id="runner-grid"></div>';
    state.runnerAgents = {
      'skill1': { skill_name: 'skill1', status: 'done' },
      'skill2': { skill_name: 'skill2', status: 'running' },
    };
    const { renderRunnerGrid } = await import('../views/runner');
    renderRunnerGrid();
    const cards = document.querySelectorAll('.fleet-card');
    expect(cards.length).toBe(2);
  });
});
