import { state } from '../state';
import { esc } from '../utils';
import { showToast } from '../ui/toast';
import type { RunnerRun } from '../types';

export function renderRunnerCard(run: RunnerRun): string {
  const name = run.skill_name || 'Unknown Skill';

  const dotColorMap: Record<string, string> = {
    success: '#22c55e',
    done:    '#22c55e',
    failed:  '#ef4444',
    fail:    '#ef4444',
    running: '#eab308',
    guarded: '#f97316',
  };
  const dotColor = dotColorMap[run.status] || 'rgba(0,0,0,.25)';

  const badgeMap: Record<string, [string, string]> = {
    success: ['fleet-badge-done',    '✓ Success'],
    done:    ['fleet-badge-done',    '✓ Done'],
    failed:  ['fleet-badge-waiting', '✗ Failed'],
    fail:    ['fleet-badge-waiting', '✗ Failed'],
    running: ['fleet-badge-running', '▶ Running'],
    guarded: ['fleet-badge-waiting', '⚠ Guarded'],
  };
  const [badgeCls, badgeTxt] = badgeMap[run.status] || ['fleet-badge-idle', run.status || '—'];

  let lastRunTxt = '—';
  if (run.started_at) {
    try {
      const d = new Date(run.started_at);
      lastRunTxt = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    } catch(e) {
      lastRunTxt = run.started_at;
    }
  }

  let durationTxt = '';
  if (run.started_at && run.finished_at) {
    try {
      const dur = Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000);
      durationTxt = ` · ${dur}s`;
    } catch(e) {}
  }

  const sk = state.skillDefs[name];
  let triggerBadge = '';
  if (sk && sk.trigger) {
    const ttype = ((sk.trigger as any).type || sk.trigger).toString().toLowerCase();
    const trigLabel = ttype === 'loop' ? 'Loop' : ttype === 'reactive' ? 'Reactive' : String((sk.trigger as any).type || sk.trigger);
    triggerBadge = `<span class="runner-trigger-badge runner-trigger-${ttype}">${trigLabel}</span>`;
  }

  const isPaused = run._paused === true;

  return `<div class="fleet-card runner-card ${run.status || 'idle'}" onclick="openRunnerDrawer(this,'${esc(name)}')">
    <div class="fleet-card-top">
      <div class="fleet-card-name" style="display:flex;align-items:center;gap:6px">
        <span class="runner-status-dot" style="background:${dotColor};width:8px;height:8px;border-radius:50%;flex-shrink:0;display:inline-block"></span>
        ${esc(name)}
      </div>
      <div class="fleet-card-badge ${badgeCls}">${badgeTxt}</div>
    </div>
    <div class="fleet-card-sub">Last run: ${lastRunTxt}${durationTxt}</div>
    <div style="display:flex;align-items:center;gap:5px;margin-top:4px">
      <div class="fleet-card-type">Runner Agent</div>
      ${triggerBadge}
    </div>
    <div class="runner-card-actions" onclick="event.stopPropagation()">
      <button class="runner-btn" onclick="runnerRunNow('${esc(name)}')">▶ Run Now</button>
      <button class="runner-btn runner-btn-sec" onclick="runnerTogglePause('${esc(name)}',this)">
        ${isPaused ? '▶ Resume' : '⏸ Pause'}
      </button>
    </div>
  </div>`;
}

export function renderRunnerGrid(): void {
  const grid = document.getElementById('runner-grid');
  if (!grid) return;
  const runs = Object.values(state.runnerAgents);
  if (runs.length === 0) {
    grid.innerHTML = '<div style="font-size:12px;color:rgba(0,0,0,.35);padding:6px 0;font-style:italic">No runner agents yet.</div>';
    return;
  }
  grid.innerHTML = runs.map(renderRunnerCard).join('');
}

export function openRunnerDrawer(el: HTMLElement | null, skillName: string): void {
  document.querySelectorAll('.fleet-card').forEach(c => c.classList.remove('on'));
  if (el) el.classList.add('on');

  const run = state.runnerAgents[skillName];
  const drawer = document.getElementById('fleet-drawer');
  const titleEl = document.getElementById('fleet-drawer-title');
  const bodyEl = document.getElementById('fleet-drawer-body');
  if (!drawer || !bodyEl) return;

  drawer.classList.add('on');
  if (titleEl) titleEl.textContent = skillName + ' · Runner Agent';

  const sk = state.skillDefs[skillName];
  let metaRows = '';
  if (sk) {
    const ttype = sk.trigger ? ((sk.trigger as any).type || sk.trigger) : '—';
    const onFail = sk.on_failure || '—';
    const guard = sk.guard ? (sk.guard.enabled ? '✓ Enabled' : '✗ Disabled') : '—';
    const steps = Array.isArray(sk.steps) ? sk.steps.length : '—';
    metaRows = `
      <div class="runner-meta-row">
        <span class="runner-meta-key">Trigger</span><span class="runner-meta-val">${esc(String(ttype))}</span>
        <span class="runner-meta-key">On failure</span><span class="runner-meta-val">${esc(String(onFail))}</span>
        <span class="runner-meta-key">Guard</span><span class="runner-meta-val">${esc(String(guard))}</span>
        <span class="runner-meta-key">Steps</span><span class="runner-meta-val">${esc(String(steps))}</span>
      </div>`;
  }

  const runId = run ? run.run_id : null;
  const logPanelId = 'runner-log-panel-' + skillName.replace(/[^a-z0-9]/gi, '_');

  bodyEl.innerHTML = `
    <div class="drawer-col-left">
      <div class="ag-run-bar">
        <div class="ag-run-status">${run ? esc(run.status || '—') : 'No runs yet'}</div>
      </div>
      ${metaRows}
      <div class="ag-actions">
        <button class="ag-btn" onclick="runnerRunNow('${esc(skillName)}')">▶ Run Now</button>
        <button class="ag-btn" onclick="runnerTogglePauseById('${esc(skillName)}')">⏸ Pause / ▶ Resume</button>
      </div>
    </div>
    <div class="drawer-col-right">
      <div class="ag-log-hd">— Live Log</div>
      <div class="ag-log-scroll runner-log-scroll" id="${logPanelId}">
        <div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">Fetching log…</div>
      </div>
    </div>`;

  if (window.ghost && skillName && runId) {
    window.ghost.send('agent_log', { skill_name: skillName, run_id: runId });
  } else {
    const lp = document.getElementById(logPanelId);
    if (lp) lp.innerHTML = '<div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">No log available (not connected to backend).</div>';
  }
}

export function runnerRunNow(skillName: string): void {
  if (window.ghost) {
    window.ghost.send('skill_run', { skill_name: skillName });
    showToast(`▶ Running "${skillName}"…`);
  } else {
    showToast(`▶ Run Now: ${skillName} (no backend connected)`);
  }
}

export function runnerTogglePause(skillName: string, btn: HTMLButtonElement | null): void {
  const run = state.runnerAgents[skillName];
  if (!run) return;
  const wasPaused = run._paused === true;
  run._paused = !wasPaused;
  if (btn) btn.textContent = run._paused ? '▶ Resume' : '⏸ Pause';
  if (window.ghost) {
    window.ghost.send(run._paused ? 'skill_pause' : 'skill_resume', { skill_name: skillName });
    showToast(run._paused ? `⏸ Paused "${skillName}"` : `▶ Resumed "${skillName}"`);
  } else {
    showToast((run._paused ? '⏸ Paused ' : '▶ Resumed ') + skillName + ' (no backend)');
  }
}

export function runnerTogglePauseById(skillName: string): void {
  const run = state.runnerAgents[skillName];
  if (!run) return;
  runnerTogglePause(skillName, null);
  renderRunnerGrid();
}

declare global {
  interface Window {
    ghost?: any;
  }
}
