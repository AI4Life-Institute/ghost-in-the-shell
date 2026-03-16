import { state } from '../state';
import { esc } from '../utils';
import { AGENTS } from '../data/agents';
import { showToast } from '../ui/toast';
import { setMode } from './mode';
import type { Agent } from '../types';

export function _flatAgent(id: string): Agent | null {
  for (const p of AGENTS.browser.profiles) {
    for (const a of p.agents) { if (a.id === id) return a; }
  }
  for (const a of AGENTS.loop) { if (a.id === id) return a; }
  for (const a of AGENTS.reactive) { if (a.id === id) return a; }
  return null;
}

export function _fleetCardHTML(a: Agent): string {
  const badgeMap: Record<string, [string, string]> = {
    running:  ['fleet-badge-running',  '▶ Running'],
    done:     ['fleet-badge-done',     '✓ Done'],
    idle:     ['fleet-badge-idle',     '⏸ Idle'],
    listening:['fleet-badge-listening','● Listening'],
    waiting:  ['fleet-badge-waiting',  '⚠ Waiting'],
  };
  const [badgeCls, badgeTxt] = badgeMap[a.status] || ['fleet-badge-idle', a.status];
  const repairBadge = a.autoRepaired
    ? `<div class="fleet-repaired-badge">🤖 Auto-repaired by Build Agent</div>`
    : '';
  return `<div class="fleet-card ${a.status}${state.curAgentId===a.id?' on':''}" onclick="openFleetDrawer(this,'${a.id}')">
    <div class="fleet-card-top">
      <div class="fleet-card-name">${esc(a.name)}</div>
      <div class="fleet-card-badge ${badgeCls}">${badgeTxt}</div>
    </div>
    <div class="fleet-card-sub">${esc(a.sub)}</div>
    <div class="fleet-card-type">${esc(a.type)}</div>
    ${repairBadge}
  </div>`;
}

export function renderFleet(): void {
  const totalAgents = AGENTS.browser.profiles.reduce((n,p)=>n+p.agents.length,0)
    + AGENTS.loop.length + AGENTS.reactive.length;
  const emptyEl = document.getElementById('agents-empty');
  const scrollEl = document.getElementById('fleet-scroll');
  if (emptyEl) emptyEl.style.display = totalAgents === 0 ? 'flex' : 'none';
  if (scrollEl) scrollEl.style.display = totalAgents === 0 ? 'none' : '';

  const profileRow = document.getElementById('profile-row');
  if (profileRow) {
    let html = '';
    AGENTS.browser.profiles.forEach(p => {
      const running = p.agents.filter(a => a.status === 'running').length;
      const count = p.agents.length;
      const isOn = p.id === state.curProfileId;
      const statusCls = running > 0 ? 'running' : 'idle';
      const statusTxt = running > 0 ? `▶ ${running} running` : count > 0 ? '⏸ Idle' : '● No agents';
      html += `<div class="profile-card${isOn?' on':''}" onclick="selProfile(this,'${p.id}')">
        <div class="profile-card-ico">🌐</div>
        <div class="profile-card-name">${esc(p.label)}</div>
        <div class="profile-card-count">${count} agent${count!==1?'s':''}</div>
        <div class="profile-card-status ${statusCls}">${statusTxt}</div>
      </div>`;
    });
    html += `<div class="profile-card add" onclick="showToast('Add Chrome profile — coming soon')">＋<br>Add Profile</div>`;
    profileRow.innerHTML = html;
  }
  renderProfileAgents(state.curProfileId);

  renderTriggerGrid();
}

export function renderProfileAgents(profileId: string): void {
  const profile = AGENTS.browser.profiles.find(p => p.id === profileId);
  const row = document.getElementById('profile-agents-row');
  if (!row || !profile) return;
  if (profile.agents.length === 0) {
    row.innerHTML = `<div style="padding:6px 10px;font-size:12px;color:rgba(0,0,0,.35);font-style:italic">No agents in this profile. Ask the Build agent to create one.</div>`;
    return;
  }
  row.innerHTML = profile.agents.map(_fleetCardHTML).join('');
}

export function selProfile(el: HTMLElement | null, profileId: string): void {
  state.curProfileId = profileId;
  document.querySelectorAll('.profile-card').forEach(c => c.classList.remove('on'));
  if (el) el.classList.add('on');
  renderProfileAgents(profileId);
}

export function openFleetDrawer(el: HTMLElement | null, agentId: string): void {
  state.curAgentId = agentId;
  document.querySelectorAll('.fleet-card').forEach(c => c.classList.remove('on'));
  if (el) el.classList.add('on');
  document.getElementById('fleet-drawer')!.classList.add('on');
  _renderFleetDrawer(agentId);
}

export function closeFleetDrawer(): void {
  document.getElementById('fleet-drawer')!.classList.remove('on');
  document.querySelectorAll('.fleet-card').forEach(c => c.classList.remove('on'));
  state.curAgentId = null;
}

export function _renderFleetDrawer(agentId: string): void {
  const a = _flatAgent(agentId);
  const titleEl = document.getElementById('fleet-drawer-title');
  const bodyEl = document.getElementById('fleet-drawer-body');
  if (!a || !bodyEl) return;
  if (titleEl) titleEl.textContent = a.name + ' · ' + a.type;

  const d = a.detail;
  const runProgress = d.running
    ? `▶ Running · step ${d.steps} of ~${d.totalSteps} · ${d.elapsed} elapsed`
    : a.status === 'done' ? `✓ Done · ${d.steps} steps · ${d.elapsed}`
    : a.status === 'listening' ? '● Listening for events…'
    : '⏸ Idle';

  const logRows = d.log.map(l => {
    const out = l.out ? `<div class="ag-log-out">${esc(l.out)}</div>` : '';
    const pending = l.pending ? `<div class="ag-log-pending">⏳ in progress…</div>` : '';
    return `<div class="ag-log-row">
      <div class="ag-log-ico">${l.ico}</div>
      <div class="ag-log-body">
        <div class="ag-log-action">${l.action}</div>
        <div class="ag-log-desc">${esc(l.desc)}</div>${out}${pending}
      </div>${l.ts ? `<div class="ag-log-ts">${l.ts}</div>` : ''}
    </div>`;
  }).join('');

  const liveView = a.type === 'Browser Agent' ? _mockBrowserScreen(a) : '';

  const chromeBanner = a.type === 'Browser Agent' ? `
    <div class="ag-chrome-banner">
      <div class="ag-chrome-banner-inner">
        <span>🌐</span>
        <strong>Real Chrome · ${esc(a.profile || 'Personal Chrome')}</strong>
      </div>
      <div style="font-size:11px;color:rgba(0,0,0,.45);margin-top:3px">Your sessions. Your cookies. No re-logging in.</div>
    </div>` : '';

  const colLeft = `
    <div class="drawer-col-left">
      ${chromeBanner}
      <div class="ag-run-bar"><div class="ag-run-status">${runProgress}</div></div>
      <div class="ag-actions">
        <button class="ag-btn">⏸ Pause</button>
        <button class="ag-btn">▶ Run Now</button>
        <button class="ag-btn" style="color:#dc2626;border-color:rgba(220,38,38,.25)" onclick="showToast('Agent deleted (simulated)')">🗑 Delete</button>
        <button class="ag-btn link" onclick="setMode('data')">View in Data →</button>
      </div>
    </div>`;
  const colRight = `
    <div class="drawer-col-right">
      <div class="ag-log-hd">— Execution Log</div>
      <div class="ag-log-scroll">${logRows || '<div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">No log entries yet.</div>'}</div>
    </div>`;

  if (liveView) {
    bodyEl.innerHTML = `
      <div class="drawer-live-wrap">
        ${liveView}
        <div class="drawer-bottom-row">${colLeft}${colRight}</div>
      </div>`;
  } else {
    bodyEl.innerHTML = colLeft + colRight;
  }
}

export function _mockBrowserScreen(a: Agent): string {
  const isRunning = a.detail.running;
  const currentUrl = isRunning ? 'nash-ai.cn/reports/list' : 'nash-ai.cn/reports';
  const liveLbl = isRunning
    ? `<span class="live-dot"></span> Live · 2s ago`
    : `<span style="color:rgba(0,0,0,.35)">Last frame · 18s ago</span>`;

  const pageContent = isRunning ? `
    <div class="mock-site-header">
      <div class="mock-site-logo">Nash<span>AI</span></div>
      <div class="mock-site-nav">Reports &nbsp;·&nbsp; Portfolio &nbsp;·&nbsp; Settings</div>
    </div>
    <div class="mock-site-body">
      <div class="mock-site-title">Research Reports</div>
      <div class="mock-site-row sel">
        <div class="mock-site-row-ico">📄</div>
        <div class="mock-site-row-name">Goldman Sachs Q2 2024 Analysis</div>
        <div class="mock-site-row-meta">2.4 MB · PDF</div>
        <div class="mock-download-bar"><div class="mock-download-fill"></div></div>
      </div>
      <div class="mock-site-row dim"><div class="mock-site-row-ico">📄</div><div class="mock-site-row-name">Morgan Stanley Q2 2024</div><div class="mock-site-row-meta">1.8 MB</div></div>
      <div class="mock-site-row dim"><div class="mock-site-row-ico">📄</div><div class="mock-site-row-name">JP Morgan Macro Outlook</div><div class="mock-site-row-meta">3.1 MB</div></div>
    </div>` : `
    <div class="mock-site-header">
      <div class="mock-site-logo">Nash<span>AI</span></div>
    </div>
    <div class="mock-site-body" style="opacity:.7">
      <div class="mock-site-title">Research Reports · 47 items</div>
      <div class="mock-site-row dim"><div class="mock-site-row-ico">📄</div><div class="mock-site-row-name">Goldman Sachs Q2 2024 Analysis</div><div class="mock-site-row-meta">✓ Downloaded</div></div>
    </div>`;

  return `<div class="live-browser">
    <div class="live-browser-bar">
      <div class="live-browser-dots"><span></span><span></span><span></span></div>
      <div class="live-browser-url">🔒 ${currentUrl}</div>
      <div class="live-browser-badge">${liveLbl}</div>
    </div>
    <div class="live-browser-screen">${pageContent}</div>
  </div>`;
}

// ── Trigger grid (shared between agents and runner views) ──────────────────

export function renderTriggerGrid(): void {
  const grid = document.getElementById('trigger-grid');
  if (!grid) return;
  const addBtn = `<div class="fleet-card-add" onclick="showToast('Type /agent in Build to create a Trigger Agent')">＋ New Trigger Agent</div>`;
  if (Object.keys(state.skillDefs).length > 0) {
    const skills = Object.values(state.skillDefs);
    grid.innerHTML = skills.map(sk => {
      const run = state.runnerAgents[sk.name] || { skill_name: sk.name, status: (sk as any).paused ? 'idle' : '—' };
      // Import renderRunnerCard lazily to avoid circular dep — use dynamic import is complex, so just inline a minimal version
      return _runnerCardHTML(run);
    }).join('') + addBtn;
  } else {
    const all = [...(AGENTS.loop || []), ...(AGENTS.reactive || [])];
    grid.innerHTML = all.map(_fleetCardHTML).join('') + addBtn;
  }
}

// Minimal runner card HTML (to avoid circular import with runner.ts)
function _runnerCardHTML(run: any): string {
  const name = run.skill_name || 'Unknown Skill';
  const dotColorMap: Record<string, string> = {
    success: '#22c55e', done: '#22c55e', failed: '#ef4444',
    fail: '#ef4444', running: '#eab308', guarded: '#f97316',
  };
  const dotColor = dotColorMap[run.status] || 'rgba(0,0,0,.25)';
  const badgeMap: Record<string, [string, string]> = {
    success: ['fleet-badge-done', '✓ Success'], done: ['fleet-badge-done', '✓ Done'],
    failed: ['fleet-badge-waiting', '✗ Failed'], fail: ['fleet-badge-waiting', '✗ Failed'],
    running: ['fleet-badge-running', '▶ Running'], guarded: ['fleet-badge-waiting', '⚠ Guarded'],
  };
  const [badgeCls, badgeTxt] = badgeMap[run.status] || ['fleet-badge-idle', run.status || '—'];
  let lastRunTxt = '—';
  if (run.started_at) {
    try { lastRunTxt = new Date(run.started_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); } catch(e) { lastRunTxt = run.started_at; }
  }
  let durationTxt = '';
  if (run.started_at && run.finished_at) {
    try { durationTxt = ` · ${Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000)}s`; } catch(e) {}
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
