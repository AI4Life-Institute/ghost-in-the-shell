import { state } from './state';
import { esc, fmt } from './utils';
import { showToast } from './ui/toast';
import { renderSessions, assignSessionToPane, renderSessionTabs, _updatePanelHeader } from './views/build';
import { renderRunnerGrid, renderRunnerCard } from './views/runner';
import { renderSkillPanel, renderSkillsList } from './views/skills';
import { renderTriggerGrid } from './views/agents';
import { updateAgentBadge } from './views/mode';
import {
  appendConvMessage, appendTailLine,
  refreshDashboardChart, appendComputeChunk,
  prependDashboardFile, handleFileThumbnailResult,
} from './views/dashboard';

declare global {
  interface Window {
    __TAURI__?: any;
    ghost?: any;
    html2canvas?: any;
  }
}

export function installTauriShim(): void {
  if (window.ghost || !window.__TAURI__) return;
  const invoke = window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke;
  const tauriEvent = window.__TAURI__.event;

  function _dbg(msg: string): void {
    const ts = new Date().toISOString().slice(11, 23);
    try { invoke('debug_log', { msg: `[${ts}] ${msg}` }).catch(() => {}); } catch(e) {}
  }

  _dbg('installTauriShim: start, tauriEvent=' + typeof tauriEvent);

  const _listeners: { event: string; cb: (data: any) => void }[] = [];

  function _dispatch(event: string, data: any): void {
    _listeners.forEach(l => { if (l.event === '*' || l.event === event) l.cb(data); });
  }

  const listenPromise = tauriEvent.listen('python-event', (e: any) => {
    const data = e.payload;
    _dbg('python-event received: ' + JSON.stringify(data).slice(0, 120));
    if (data?.event) _dispatch(data.event, data);
  });
  listenPromise.then(() => {
    _dbg('tauriEvent.listen python-event: registered OK');
  }).catch((err: any) => {
    _dbg('tauriEvent.listen python-event ERROR: ' + err);
    showToast('IPC error: ' + err);
  });

  tauriEvent.listen('pty-output', (e: any) => {
    _dispatch('pty-output', e.payload);
  }).catch((err: any) => { _dbg('tauriEvent.listen pty-output ERROR: ' + err); });

  window.ghost = {
    send(cmd: string, payload: any = {}) {
      _dbg('ghost.send: cmd=' + cmd);
      return invoke('python_cmd', { cmd, payload });
    },
    on(event: string, cb: (data: any) => void) {
      const entry = { event, cb };
      _listeners.push(entry);
      return entry;
    },
    off(handle: any) {
      const idx = _listeners.indexOf(handle);
      if (idx !== -1) _listeners.splice(idx, 1);
    },
    onAny(cb: (data: any) => void) { return window.ghost.on('*', cb); },
    _dbg,
  };
  _dbg('installTauriShim: window.ghost set');
}

export function sendPane(_idx: number): void {} // compat stub

export function _addPaneMsg(idx: number, role: string, text: string): void {
  const msgs = idx === 0
    ? document.getElementById('build-msgs')
    : document.getElementById('wspc-' + idx);
  if (!msgs) return;
  const now = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.innerHTML = `<div class="mav">${role==='ai'?'🤖':'W'}</div><div><div class="bbl">${fmt(text)}</div><div class="mt">${now}</div></div>`;
  if (idx === 0) {
    msgs.insertBefore(d, document.getElementById('typing'));
  } else {
    msgs.appendChild(d);
  }
  setTimeout(() => msgs.scrollTop = msgs.scrollHeight, 10);
}

export function _initTerminalWhenVisible(_idx: number, _channelId: string): void {}  // compat stub

export function initPtyTerminal(channelId: string): void {
  assignSessionToPane(channelId, state.activePaneIdx);
}

export function ghostSetup(): void {
  if (typeof window === 'undefined' || !window.ghost) return;
  const _dbg = window.ghost._dbg || (() => {});
  _dbg('ghostSetup: start');

  window.ghost.on('ready', () => {
    _dbg('ghostSetup: ready event received');
    window.ghost.send('sessions');
    window.ghost.send('agents', {});
    window.ghost.send('skills', {});
  });

  window.ghost.on('sessions', (data: any) => {
    _dbg('ghostSetup: sessions count=' + (data.sessions || []).length);
    if (data.tmux_session) state.tmuxSession = data.tmux_session;
    renderSessions(data.sessions || []);
  });

  window.ghost.on('pane_update', (data: any) => {
    const sess = state.allSessions.find(s => s.channel_id === data.channel_id);
    if (sess) {
      sess._status = data.status === 'busy' ? 'busy' : 'idle';
    }

    renderSessionTabs();
    const grid = document.getElementById('ws-grid');
    if (grid) {
      state.panes.forEach((pane, idx) => {
        if (pane.channelId !== data.channel_id) return;
        const panelEl = grid.children[idx] as HTMLElement;
        if (panelEl) _updatePanelHeader(panelEl, pane, idx);
      });
    }
  });

  window.ghost.on('pty-output', (data: any) => {
    const pane = state.panes.find(p => p.channelId === data.channel_id);
    if (!pane || !pane.terminal) return;
    if (data.closed) { pane.terminal.write('\r\n[terminal closed]\r\n'); return; }
    if (data.data) {
      try {
        const bytes = Uint8Array.from(atob(data.data), (c: string) => c.charCodeAt(0));
        pane.terminal.write(bytes);
      } catch (e) {
        pane.terminal.write(data.data);
      }
    }
  });

  window.ghost.on('agents_list', (data: any) => {
    const runs = data.runs || [];
    state.runnerAgents = {};
    runs.forEach((run: any) => {
      const key = run.skill_name;
      if (!key) return;
      const existing = state.runnerAgents[key];
      if (!existing) { state.runnerAgents[key] = run; return; }
      const existingTs = existing.started_at ? new Date(existing.started_at).getTime() : 0;
      const newTs = run.started_at ? new Date(run.started_at).getTime() : 0;
      if (newTs > existingTs) state.runnerAgents[key] = run;
    });
    renderRunnerGrid();
    renderTriggerGrid();
    updateAgentBadge();
  });

  window.ghost.on('skills_list', (data: any) => {
    const skills = data.skills || [];
    state.skillDefs = {};
    skills.forEach((sk: any) => { if (sk.name) state.skillDefs[sk.name] = sk; });
    renderRunnerGrid();
    renderTriggerGrid();
    renderSkillsList();
    renderSkillPanel(skills);
  });

  window.ghost.on('agent_log', (data: any) => {
    const line = data.line || data.text || '';
    // Runner panel (existing behaviour)
    const skillName = data.skill_name;
    if (skillName) {
      const logPanelId = 'runner-log-panel-' + skillName.replace(/[^a-z0-9]/gi, '_');
      const lp = document.getElementById(logPanelId);
      if (lp && line) {
        if (lp.querySelector('div[style]')) lp.innerHTML = '';
        const d = document.createElement('div');
        d.className = 'ag-log-row';
        d.style.cssText = 'font-size:11.5px;font-family:monospace;color:rgba(0,0,0,.7);padding:2px 0;border-bottom:1px solid rgba(0,0,0,.04)';
        d.textContent = line;
        lp.appendChild(d);
        lp.scrollTop = lp.scrollHeight;
      }
    }
    // Dashboard tail view (7.5)
    if (line && data.widget_id) {
      appendTailLine(data.widget_id, line);
    }
  });

  // 7.4: conversation message from agent
  window.ghost.on('conversation_message', (data: any) => {
    if (!data.widget_id || !data.text) return;
    const role = data.role === 'hitl' ? 'hitl' : (data.role === 'user' ? 'user' : 'agent');
    appendConvMessage(data.widget_id, role, data.text);
  });

  // 8.5: DB table updated — refresh bound chart
  window.ghost.on('db_write', (data: any) => {
    if (data.table) refreshDashboardChart(data.table);
  });

  // 9.2: streaming compute chunk
  window.ghost.on('compute_chunk', (data: any) => {
    if (!data.widget_id) return;
    appendComputeChunk(data.widget_id, data.text ?? '', data.done === true);
  });

  // 10.7: new file created by agent
  window.ghost.on('file_created', (data: any) => {
    if (!data.widget_id || !data.file) return;
    prependDashboardFile(data.widget_id, data.file);
  });

  // 10.5: PDF thumbnail result
  window.ghost.on('file_thumbnail_result', (data: any) => {
    if (data.file_id && data.data_url) {
      handleFileThumbnailResult(data.file_id, data.data_url);
    }
  });

  function _requestSessions(): void {
    _dbg('ghostSetup: sending sessions command');
    window.ghost.send('sessions')
      .then(() => { _dbg('ghostSetup: sessions send OK (invoke returned)'); })
      .catch((err: any) => {
        _dbg('ghostSetup: sessions send ERROR: ' + err);
        showToast('Sessions IPC error: ' + err);
      });
  }
  _requestSessions();
  window.ghost.send('agents', {}).catch(() => {});
  window.ghost.send('skills', {}).catch(() => {});

  const _sessRetry = setInterval(() => {
    if (state.allSessions.length > 0) { clearInterval(_sessRetry); return; }
    _requestSessions();
  }, 3000);
}
