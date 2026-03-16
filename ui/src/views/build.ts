import { state } from '../state';
import { esc, fmt } from '../utils';
import { showToast } from '../ui/toast';
import type { Session, Pane } from '../types';

declare const Terminal: any;
declare const FitAddon: any;

declare global {
  interface Window {
    __TAURI__?: any;
    ghost?: any;
  }
}

// ── Session label helpers ──────────────────────────────────────────────────

export function _sessLabel(sess: Session): string {
  if (sess.window_name && sess.window_name !== '1' && !/^\d+$/.test(sess.window_name))
    return sess.window_name;
  return sess.work_dir ? sess.work_dir.split('/').pop()! : (sess.coding_cli || 'session');
}

export function _shortDir(work_dir: string | undefined): string {
  if (!work_dir) return '~';
  let p = work_dir.replace(/^\/Users\/[^/]+/, '~');
  p = p.replace(/^\/Volumes\/[^/]+/, '');
  if (!p) p = '/';
  return p;
}

export function _projectName(work_dir: string | undefined): string {
  if (!work_dir) return '~';
  const parts = work_dir.split('/').filter(Boolean);
  if (parts.length === 0) return '~';
  return parts.length >= 2 ? parts.slice(-2).join('/') : parts[0];
}

export function _groupDir(work_dir: string | undefined): string {
  if (!work_dir) return '~';
  const p = _shortDir(work_dir);
  const parts = p.replace(/^~\/?/, '').split('/').filter(Boolean);
  const key = parts.slice(0, 3).join('/');
  return key || p;
}

export function buildSessionTree(sessions: Session[]): { byDir: Map<string, any>; dead: Session[] } {
  const alive = sessions.filter(s => s.alive !== false);
  const dead  = sessions.filter(s => s.alive === false);

  alive.sort((a, b) => {
    if (a.platform === 'desktop' && b.platform !== 'desktop') return -1;
    if (b.platform === 'desktop' && a.platform !== 'desktop') return 1;
    return (b.created_at || '').localeCompare(a.created_at || '');
  });

  const byDir = new Map<string, { roots: Session[]; childMap: Map<string, Session[]> }>();
  const channelIds = new Set(alive.map(s => s.channel_id));

  alive.forEach(sess => {
    const dir = _groupDir(sess.work_dir);
    if (!byDir.has(dir)) byDir.set(dir, { roots: [], childMap: new Map() });
    const group = byDir.get(dir)!;

    if (sess.parent_channel_id && channelIds.has(sess.parent_channel_id)) {
      if (!group.childMap.has(sess.parent_channel_id))
        group.childMap.set(sess.parent_channel_id, []);
      group.childMap.get(sess.parent_channel_id)!.push(sess);
    } else {
      group.roots.push(sess);
    }
  });

  return { byDir, dead };
}

export function recentSessions(): Session[] {
  const openIds = new Set(state.panes.map(p => p.channelId).filter(Boolean));
  return state.allSessions
    .filter(s => s.alive !== false && !openIds.has(s.channel_id))
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
    .slice(0, 5);
}

// ── Tab bar rendering ──────────────────────────────────────────────────────

export function renderSessionTabs(): void {
  const bar = document.getElementById('ws-tabbar');
  if (!bar) return;

  const addBtn = bar.querySelector('.ws-tab-add');
  const rightEl = bar.querySelector('.ws-tabbar-right');
  bar.innerHTML = '';

  let lastDir: string | null = null;
  state.panes.forEach((pane, idx) => {
    const sess = pane.channelId ? state.allSessions.find(s => s.channel_id === pane.channelId) : null;
    const dir = sess ? _groupDir(sess.work_dir) : null;

    if (dir && lastDir !== null && dir !== lastDir) {
      const div = document.createElement('div');
      div.className = 'ws-tab-divider';
      bar.appendChild(div);
    }
    if (dir) lastDir = dir;

    const tab = document.createElement('div');
    tab.className = 'ws-tab' + (idx === state.activePaneIdx ? ' on' : '');
    if (sess) {
      const status = _paneStatus(pane);
      const dotCls = status === 'active' ? 'g' : status === 'busy' ? '' : 'd';
      const deadCls = sess.alive === false ? ' dead' : status === 'busy' ? ' busy' : '';
      tab.className += deadCls;
      const platIco = sess.platform === 'desktop' ? '🖥' : '💬';
      tab.innerHTML =
        `<div class="ws-tab-dot ${dotCls}"></div>` +
        `<span>${platIco} ${esc(_sessLabel(sess))}</span>`;
    } else {
      tab.innerHTML = `<span style="opacity:.35">Empty pane</span>`;
    }
    tab.onclick = () => focusPane(idx);
    bar.appendChild(tab);
  });

  if (addBtn) bar.appendChild(addBtn);
  else {
    const a = document.createElement('div');
    a.className = 'ws-tab-add';
    a.title = 'Open session in new pane';
    a.textContent = '＋';
    a.onclick = () => openSessionPicker(-1);
    bar.appendChild(a);
  }
  if (rightEl) bar.appendChild(rightEl);
  else {
    const r = document.createElement('div');
    r.className = 'ws-tabbar-right';
    r.innerHTML = `<div class="devmode-btn${state.devMode?' on':''}" id="devmode-btn" onclick="toggleDevMode()"><div class="devmode-dot"></div><span>>_ Dev</span></div>`;
    bar.appendChild(r);
  }
}

export function _paneStatus(pane: Pane): string {
  if (!pane.channelId) return 'empty';
  const s = state.allSessions.find(ss => ss.channel_id === pane.channelId);
  if (!s || s.alive === false) return 'stopped';
  return s._status || 'idle';
}

// ── Grid rendering ─────────────────────────────────────────────────────────

export function renderGrid(): void {
  const grid = document.getElementById('ws-grid');
  if (!grid) return;

  grid.style.gridTemplateColumns = state.panes.length === 1 ? '1fr' : '1fr 1fr';

  while (grid.children.length < state.panes.length) {
    const idx = grid.children.length;
    grid.appendChild(_makePanelEl(idx));
  }
  while (grid.children.length > state.panes.length) {
    grid.removeChild(grid.lastChild!);
  }

  state.panes.forEach((pane, idx) => {
    const panelEl = grid.children[idx] as HTMLElement;
    if (!panelEl) return;
    panelEl.classList.toggle('active', idx === state.activePaneIdx);
    _updatePanelHeader(panelEl, pane, idx);
    _ensurePaneTerm(panelEl, pane, idx);
  });

  renderSessionTabs();
}

export function _makePanelEl(idx: number): HTMLElement {
  const el = document.createElement('div');
  el.className = 'ws-panel' + (idx === state.activePaneIdx ? ' active' : '');
  (el as any).dataset.paneIdx = idx;
  el.innerHTML = _panelHeadHTML(null, idx) + '<div class="wsp-term-wrap"></div>';
  el.querySelector('.wsp-head')!.addEventListener('click', () => focusPane(idx));
  return el;
}

export function _panelHeadHTML(pane: Pane | null, idx: number): string {
  const sess = pane && pane.channelId ? state.allSessions.find(s => s.channel_id === pane.channelId) : null;
  if (!sess) {
    return `<div class="wsp-head" onclick="focusPane(${idx})">` +
      `<div class="pane-status"><div class="pane-dot stopped"></div><span class="pane-status-stopped">Empty</span></div>` +
      `<div class="wsp-name" style="opacity:.35">No session</div>` +
      `<button class="wsp-status-lbl" style="background:none;border:none;cursor:pointer;color:rgba(0,0,0,.38);font-size:11px" onclick="event.stopPropagation();openSessionPicker(${idx})">Open…</button>` +
      `</div>`;
  }
  const status = _paneStatus(pane!);
  const dotCls = status === 'active' ? 'active' : status === 'busy' ? 'idle' : 'stopped';
  const lblCls = status === 'active' ? 'pane-status-active' : status === 'busy' ? 'pane-status-idle' : 'pane-status-stopped';
  const lblTxt = status === 'active' ? '▶ Active' : status === 'busy' ? '⏳ Busy' : status === 'stopped' ? '⬜ Stopped' : '⏸ Idle';
  const cli = (sess.coding_cli || 'claude').toLowerCase();
  const dir = sess.work_dir ? sess.work_dir.split('/').pop() : '';
  return `<div class="wsp-head" onclick="focusPane(${idx})">` +
    `<div class="pane-status"><div class="pane-dot ${dotCls}"></div><span class="${lblCls}">${lblTxt}</span></div>` +
    `<div class="wsp-name">${esc(_sessLabel(sess))}</div>` +
    `<div class="wsp-ai">${esc(cli)}${dir ? ' · ' + esc(dir) : ''}</div>` +
    `<button class="wsp-status-lbl" style="background:none;border:none;cursor:pointer;color:rgba(0,0,0,.38);font-size:11px" onclick="event.stopPropagation();openSessionPicker(${idx})">Change</button>` +
    `</div>`;
}

export function _updatePanelHeader(panelEl: HTMLElement, pane: Pane, idx: number): void {
  const existingHead = panelEl.querySelector('.wsp-head');
  if (existingHead) {
    const newHead = document.createElement('div');
    newHead.innerHTML = _panelHeadHTML(pane, idx);
    panelEl.replaceChild(newHead.firstChild!, existingHead);
  }
}

export function _ensurePaneTerm(panelEl: HTMLElement, pane: Pane, idx: number): void {
  let wrap = panelEl.querySelector('.wsp-term-wrap') as HTMLElement;
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.className = 'wsp-term-wrap';
    panelEl.appendChild(wrap);
  }

  if (!pane.channelId) {
    if (!wrap.querySelector('.wsp-empty')) {
      wrap.innerHTML = `<div class="wsp-empty" onclick="openSessionPicker(${idx})"><div class="wsp-empty-ico">⌗</div><span>Click to open a session</span></div>`;
    }
    return;
  }

  if (pane.terminal && pane.channelId) return;

  wrap.innerHTML = '';
  _initPaneTerm(wrap, pane, idx);
}

export function _initPaneTerm(wrap: HTMLElement, pane: Pane, idx: number): void {
  const term = new Terminal({
    fontFamily: '"SF Mono", "JetBrains Mono", "Menlo", monospace',
    fontSize: 13,
    lineHeight: 1.45,
    theme: {
      background: 'transparent',
      foreground: 'rgba(255,255,255,0.85)',
      cursor: 'rgba(255,255,255,0.75)',
      selectionBackground: 'rgba(99,102,241,0.35)',
      black: '#1a1a1a', red: '#ff6b6b', green: '#51cf66',
      yellow: '#ffd43b', blue: '#74c0fc', magenta: '#cc5de8',
      cyan: '#3bc9db', white: '#e9ecef',
      brightBlack: '#868e96', brightRed: '#ff8787',
      brightGreen: '#8ce99a', brightYellow: '#ffe066',
      brightBlue: '#91d0ff', brightMagenta: '#da77f2',
      brightCyan: '#66d9e8', brightWhite: '#f8f9fa',
    },
    allowTransparency: true,
    cursorBlink: true,
    scrollback: 5000,
  });

  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(wrap);
  fitAddon.fit();
  term.focus();

  pane.terminal = term;
  pane.fitAddon = fitAddon;

  if (idx === 0) { state.sessTerminal = term; state.activeSessId = pane.channelId; }

  term.onData((data: string) => {
    const encoded = btoa(unescape(encodeURIComponent(data)));
    (window.__TAURI__!.core?.invoke ?? window.__TAURI__!.invoke)('pty_input', {
      channel_id: pane.channelId, data: encoded,
    }).catch(() => {});
  });

  const binding = state.allSessions.find(s => s.channel_id === pane.channelId);
  if (binding) {
    (window.__TAURI__!.core?.invoke ?? window.__TAURI__!.invoke)('open_pty', {
      channel_id: pane.channelId,
      tmux_session: state.tmuxSession,
      window_id: binding.window_id,
      rows: term.rows, cols: term.cols,
    }).catch(() => {});
  }

  const ro = new ResizeObserver(() => {
    fitAddon.fit();
    if (term.rows && term.cols) {
      (window.__TAURI__!.core?.invoke ?? window.__TAURI__!.invoke)('resize_pty', {
        channel_id: pane.channelId, rows: term.rows, cols: term.cols,
      }).catch(() => {});
    }
  });
  ro.observe(wrap);
  pane.ro = ro;
}

// ── Pane focus / assignment ────────────────────────────────────────────────

export function focusPane(idx: number): void {
  state.activePaneIdx = idx;
  state.activeSessId = state.panes[idx]?.channelId || null;
  const grid = document.getElementById('ws-grid');
  if (grid) {
    Array.from(grid.children).forEach((el, i) => el.classList.toggle('active', i === idx));
  }
  renderSessionTabs();
}

export function assignSessionToPane(channelId: string, paneIdx: number): void {
  const pane = state.panes[paneIdx];
  if (!pane) return;

  if (pane.channelId && window.__TAURI__) {
    (window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke)('close_pty', { channel_id: pane.channelId }).catch(() => {});
  }
  if (pane.terminal) { pane.terminal.dispose(); pane.terminal = null; }
  if (pane.ro) { pane.ro.disconnect(); pane.ro = null; }
  pane.fitAddon = null;
  pane.channelId = channelId;

  if (paneIdx === 0) { state.activeSessId = channelId; state.sessTerminal = null; }

  renderGrid();
  focusPane(paneIdx);
}

// ── Session Picker ─────────────────────────────────────────────────────────

export function openSessionPicker(paneIdx: number): void {
  state.sessPickerTargetPane = paneIdx >= 0 ? paneIdx : 0;
  renderSessPickerTree();
  document.getElementById('sess-picker-modal')!.classList.add('on');
}

export function closeSessPickerModal(event?: MouseEvent): void {
  if (event && event.target !== document.getElementById('sess-picker-modal')) return;
  document.getElementById('sess-picker-modal')!.classList.remove('on');
}

export function renderSessPickerTree(): void {
  const body = document.getElementById('sess-picker-body');
  if (!body) return;
  const { byDir, dead } = buildSessionTree(state.allSessions);

  let html = '';

  byDir.forEach((group, dir) => {
    const repSess = group.roots[0] || [...group.childMap.values()][0]?.[0];
    const projName = repSess ? _projectName(repSess.work_dir) : dir;
    html += `<div class="sp-dir-hd">📁 ${esc(projName)}</div>`;
    group.roots.forEach((sess: Session) => {
      html += _spRowHTML(sess, 0);
      const children = group.childMap.get(sess.channel_id) || [];
      children.forEach((child: Session) => { html += _spRowHTML(child, 1); });
    });
  });

  if (dead.length) {
    html += `<div class="sp-dead-section">Stopped</div>`;
    dead.forEach((sess: Session) => { html += _spRowHTML(sess, 0, true); });
  }

  if (!html) {
    html = `<div style="padding:24px;text-align:center;color:rgba(0,0,0,.3);font-size:13px">No sessions found.<br>Start a new one below.</div>`;
  }

  body.innerHTML = html;
}

export function _spRowHTML(sess: Session, depth: number, isDead?: boolean): string {
  const name = _sessLabel(sess);
  const cli = (sess.coding_cli || 'claude').toLowerCase();
  const platIco = sess.platform === 'desktop' ? '🖥' : '💬';
  const statusCls = isDead ? 'stopped' : (sess._status === 'active' ? 'alive' : 'idle');
  const statusIco = isDead ? '⬜' : (sess._status === 'active' ? '▶' : '⏸');
  const deadCls = isDead ? ' sp-dead' : '';
  const depthCls = depth === 1 ? ' sp-thread' : depth >= 2 ? ' sp-thread2' : '';
  return `<div class="sp-row${depthCls}${deadCls}" onclick="pickSession('${esc(sess.channel_id)}')">` +
    `<div class="sp-status ${statusCls}">${statusIco}</div>` +
    `<div class="sp-name">${platIco} ${esc(name)}</div>` +
    `<div class="sp-meta">` +
      `<span class="sp-cli">${esc(cli)}</span>` +
    `</div>` +
    `</div>`;
}

export function pickSession(channelId: string): void {
  document.getElementById('sess-picker-modal')!.classList.remove('on');
  assignSessionToPane(channelId, state.sessPickerTargetPane);
}

export function newSessionFromPicker(): void {
  document.getElementById('sess-picker-modal')!.classList.remove('on');
  const name = prompt('Session name:', 'ghost');
  if (!name) return;
  const refSess = state.allSessions[0];
  const work_dir = refSess?.work_dir || '~';
  if (window.ghost) window.ghost.send('new_session', { name, work_dir, cli: 'claude' }).catch(() => {});
}

export function newSession(): void { newSessionFromPicker(); }

// ── Top-level session refresh ──────────────────────────────────────────────

export function renderSessions(sessions: Session[]): void {
  state.allSessions = sessions;

  const aliveSorted = sessions
    .filter(s => s.alive !== false)
    .sort((a, b) => {
      if (a.platform === 'desktop' && b.platform !== 'desktop') return -1;
      if (b.platform === 'desktop' && a.platform !== 'desktop') return 1;
      return (b.created_at || '').localeCompare(a.created_at || '');
    });

  let assigned = 0;
  state.panes.forEach((pane, idx) => {
    if (!pane.channelId && aliveSorted[assigned]) {
      pane.channelId = aliveSorted[assigned].channel_id;
      if (idx === 0) { state.activeSessId = pane.channelId; }
      assigned++;
    }
  });

  renderGrid();
}

export function activateSession(sess: Session): void {
  assignSessionToPane(sess.channel_id, state.activePaneIdx);
}

// ── Dev Mode toggle ────────────────────────────────────────────────────────

export function toggleDevMode(): void {
  state.devMode = !state.devMode;
  const btn = document.getElementById('devmode-btn');
  if (btn) btn.classList.toggle('on', state.devMode);
  const grid = document.getElementById('ws-grid');
  if (grid) grid.classList.toggle('devmode', state.devMode);
}

export function selectBuildTab(): void {} // compat stub
export function addWindow(): void {}      // compat stub
export function addPane(): void {}        // compat stub

// ── Chat helpers ──────────────────────────────────────────────────────────

export function ar(ta: HTMLTextAreaElement): void {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 100) + 'px';
}

export function hk(_e: KeyboardEvent): void {}  // compat stub

export function send(): void {} // compat stub

export function checkBuildEmpty(): void {}

export function addm(role: string, text: string): void {
  const now = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.innerHTML = `<div class="mav">${role==='ai'?'🤖':'W'}</div><div><div class="bbl">${fmt(text)}</div><div class="mt">${now}</div></div>`;
  const msgs = document.getElementById('build-msgs');
  if (msgs) msgs.insertBefore(d, document.getElementById('typing'));
  scrl();
}

export function scrl(): void {
  const c = document.getElementById('build-msgs');
  if (c) setTimeout(() => c.scrollTop = c.scrollHeight, 10);
}

export function cpc(btn: HTMLButtonElement): void {
  navigator.clipboard.writeText(btn.previousElementSibling!.textContent!).catch(() => {});
  btn.textContent = 'Copied!';
  setTimeout(() => btn.textContent = 'Copy', 1500);
}
