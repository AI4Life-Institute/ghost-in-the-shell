import { state } from '../state';
import { esc } from '../utils';
import { AGENTS } from '../data/agents';
import { DB } from '../data/db';
import { loadDashboard, saveDashboard } from '../dashboard-store';
import { linkToLibrary } from './library';
import type {
  Widget, WidgetType, WidgetSize, AgentDashboard,
  ConversationConfig, ChartConfig, ComputeConfig, FilesConfig, FileEntry,
} from '../types';

// ── Widget Registry interface ───────────────────────────────────────────────

interface WidgetRenderer {
  defaultSize: WidgetSize;
  render(w: Widget): HTMLElement;
}

// ── Widget card wrapper ─────────────────────────────────────────────────────

function _widgetCard(w: Widget, body: HTMLElement): HTMLElement {
  const card = document.createElement('div');
  card.className = `dash-widget dash-widget-${w.size} dash-widget-state-${w.state}`;
  card.dataset.widgetId = w.id;

  const hdr = document.createElement('div');
  hdr.className = 'dw-hdr';
  hdr.innerHTML = `
    <span class="dw-title">${esc(w.title || w.type)}</span>
    <span class="dw-state-badge dw-state-${w.state}">${_stateBadge(w.state)}</span>`;
  card.appendChild(hdr);
  card.appendChild(body);
  return card;
}

function _stateBadge(st: string): string {
  const m: Record<string, string> = { running: '▶', review: '⚠ Review', done: '✓', idle: '' };
  return m[st] ?? st;
}

// ── Conversation widget ─────────────────────────────────────────────────────

const conversationRenderer: WidgetRenderer = {
  defaultSize: '2x1',
  render(w: Widget): HTMLElement {
    const cfg = w.config as ConversationConfig;
    const el = document.createElement('div');
    el.className = 'dw-conversation';

    if (cfg.mode === 'tail') {
      el.innerHTML = `
        <div class="dw-tail-wrap">
          <pre class="dw-tail-pre" id="dw-tail-${w.id}"><span class="dw-tail-cursor">▌</span></pre>
        </div>
        <div class="dw-conv-toolbar">
          <button class="dw-conv-mode-btn" onclick="dashboardWidgetToggleTail('${w.id}')">💬 Chat</button>
        </div>`;
    } else {
      // Check for pending HITL from agent detail
      const agent = _allAgents().find(a => a.id === w.agentId);
      const hitlHtml = agent?.detail?.hitl?.pending
        ? `<div class="dw-conv-msg agent hitl">
            <span class="dw-msg-ico">⚠️</span>
            <div class="dw-msg-body dw-hitl-body">
              <div class="dw-hitl-msg">${esc(agent.detail.hitl.msg)}</div>
              <div class="dw-hitl-btns">
                <button class="dw-hitl-btn confirm"
                  onclick="dashboardHitlRespond('${w.id}','confirm')">✓ Confirm</button>
                <button class="dw-hitl-btn skip"
                  onclick="dashboardHitlRespond('${w.id}','skip')">✕ Skip</button>
              </div>
            </div>
          </div>` : '';
      el.innerHTML = `
        <div class="dw-conv-msgs" id="dw-conv-msgs-${w.id}">
          <div class="dw-conv-msg agent">
            <span class="dw-msg-ico">🤖</span>
            <div class="dw-msg-body">Ready. What would you like to know?</div>
          </div>
          ${hitlHtml}
        </div>
        <div class="dw-conv-input-row">
          <input class="dw-conv-input" id="dw-conv-input-${w.id}"
            placeholder="Message agent…"
            onkeydown="dashboardConvSend(event,'${w.id}')">
          <button class="dw-conv-send" onclick="dashboardConvSendBtn('${w.id}')">↑</button>
          <button class="dw-conv-mode-btn" onclick="dashboardWidgetToggleTail('${w.id}')">≡ Log</button>
        </div>`;
    }
    return el;
  },
};

// ── Chart widget ────────────────────────────────────────────────────────────

const chartRenderer: WidgetRenderer = {
  defaultSize: '2x1',
  render(w: Widget): HTMLElement {
    const cfg = w.config as ChartConfig;
    const view = cfg.view ?? 'table';
    const range = cfg.range ?? '24h';
    const el = document.createElement('div');
    el.className = 'dw-chart';

    const rangeTabs = (['1h', '24h', '7d', 'all'] as const).map(r =>
      `<div class="dw-chart-rtab${range === r ? ' on' : ''}"
            onclick="dashboardChartRange('${w.id}','${r}')">${r}</div>`
    ).join('');

    el.innerHTML = `
      <div class="dw-chart-toolbar">
        <div class="dw-chart-view-tabs">
          <div class="dw-chart-vtab${view === 'table' ? ' on' : ''}"
               onclick="dashboardChartView('${w.id}','table')">Table</div>
          <div class="dw-chart-vtab${view === 'chart' ? ' on' : ''}"
               onclick="dashboardChartView('${w.id}','chart')">Chart</div>
        </div>
        <div class="dw-chart-range-tabs">${rangeTabs}</div>
        <button class="dw-chart-cfg-btn" onclick="dashboardChartConfig('${w.id}')">···</button>
      </div>
      <div class="dw-chart-body" id="dw-chart-body-${w.id}">
        ${view === 'chart' ? _chartSvg() : _chartTablePreview(cfg)}
      </div>`;
    return el;
  },
};

function _chartTablePreview(cfg: ChartConfig): string {
  const tableId = cfg.table;
  return `<div class="dw-chart-table-ph">
    <div class="dw-chart-tname">📊 ${esc(tableId)}</div>
    <div class="dw-chart-trows">Loading…</div>
    <a class="dw-chart-viewall" onclick="linkToLibrary('data','${esc(tableId)}')">View all →</a>
  </div>`;
}

function _chartSvg(): string {
  // Minimal placeholder SVG polyline
  const pts = '10,80 40,55 70,65 100,30 130,45 160,25 190,40 220,15 250,30 280,20';
  return `<svg class="dw-chart-svg" viewBox="0 0 300 100" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="rgba(99,102,241,.8)" stroke-width="2"
      stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="${pts} 280,100 10,100" fill="rgba(99,102,241,.08)" stroke="none"/>
  </svg>`;
}

// ── Compute widget ──────────────────────────────────────────────────────────

const computeRenderer: WidgetRenderer = {
  defaultSize: '2x1',
  render(w: Widget): HTMLElement {
    const cfg = w.config as ComputeConfig;
    const el = document.createElement('div');
    el.className = 'dw-compute';

    const cursor = cfg.streaming ? '<span class="dw-compute-cursor">▌</span>' : '';
    const actions = (w.state === 'review' && cfg.reviewActions?.length)
      ? `<div class="dw-compute-actions">${cfg.reviewActions.map(a =>
          `<button class="dw-compute-act-btn"
             onclick="dashboardComputeAction('${w.id}','${esc(a.event)}')">${esc(a.label)}</button>`
        ).join('')}</div>`
      : '';

    el.innerHTML = `
      <div class="dw-compute-body" id="dw-compute-body-${w.id}">
        ${_renderMarkdown(cfg.content || '')}${cursor}
      </div>
      ${actions}`;
    return el;
  },
};

function _renderMarkdown(md: string): string {
  return md
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>[^]*?<\/li>)/g, '<ul>$1</ul>')
    .replace(/\n\n+/g, '</p><p>')
    .replace(/^(?!<[hucl])(.+)/gm, '<p>$1</p>');
}

// ── Files widget ────────────────────────────────────────────────────────────

const filesRenderer: WidgetRenderer = {
  defaultSize: '2x2',
  render(w: Widget): HTMLElement {
    const cfg = w.config as FilesConfig;
    const files = cfg.files ?? [];

    // auto-detect gallery if any image MIME
    const isGallery = cfg.viewMode === 'gallery' ||
      (cfg.viewMode == null && files.some(f => f.mimeType.startsWith('image/')));

    const el = document.createElement('div');
    el.className = 'dw-files';

    const selCount = cfg.selectable && cfg.selected?.length
      ? `<div class="dw-files-sel-count">${cfg.selected.length} selected</div>` : '';

    const toolbar = `
      <div class="dw-files-toolbar">
        <div class="dw-files-vtabs">
          <div class="dw-files-vtab${isGallery ? ' on' : ''}"
               onclick="dashboardFilesView('${w.id}','gallery')">⊞ Gallery</div>
          <div class="dw-files-vtab${!isGallery ? ' on' : ''}"
               onclick="dashboardFilesView('${w.id}','list')">≡ List</div>
        </div>
        ${selCount}
      </div>`;

    const bodyItems = isGallery
      ? files.map(f => _galleryItem(f, w, cfg)).join('')
      : files.map(f => _listItem(f, w, cfg)).join('');

    const bodyWrap = isGallery
      ? `<div class="dw-files-gallery" id="dw-files-body-${w.id}">${bodyItems}</div>`
      : `<div class="dw-files-list"   id="dw-files-body-${w.id}">${bodyItems}</div>`;

    const batchBar = cfg.selectable && cfg.selected?.length && cfg.batchActions?.length
      ? `<div class="dw-files-batch">${cfg.batchActions.map(a =>
          `<button class="dw-files-batch-btn"
             onclick="dashboardFilesAction('${w.id}','${esc(a.event)}')">${esc(a.label)}</button>`
        ).join('')}</div>` : '';

    el.innerHTML = toolbar + bodyWrap + batchBar;
    return el;
  },
};

function _galleryItem(f: FileEntry, w: Widget, cfg: FilesConfig): string {
  const isNew = f.isNew ? '<span class="dw-file-new">NEW</span>' : '';
  const sel = cfg.selected?.includes(f.id) ? ' selected' : '';
  const selAttr = cfg.selectable ? `onclick="dashboardFileSelect('${w.id}','${f.id}')"` : '';
  const acts = (cfg.actions ?? []).map(a =>
    `<button class="dw-file-act"
       onclick="dashboardFilesItemAction('${w.id}','${f.id}','${esc(a.event)}')">${esc(a.label)}</button>`
  ).join('');
  return `<div class="dw-file-thumb${sel}" ${selAttr}>
    ${isNew}
    <div class="dw-file-thumb-img">${_mimeIcon(f.mimeType)}</div>
    <div class="dw-file-thumb-name">${esc(f.name)}</div>
    ${acts ? `<div class="dw-file-actions">${acts}</div>` : ''}
  </div>`;
}

function _listItem(f: FileEntry, w: Widget, cfg: FilesConfig): string {
  const isNew = f.isNew ? '<span class="dw-file-new">NEW</span>' : '';
  const sel = cfg.selected?.includes(f.id) ? ' selected' : '';
  const selAttr = cfg.selectable ? `onclick="dashboardFileSelect('${w.id}','${f.id}')"` : '';
  const acts = (cfg.actions ?? []).map(a =>
    `<button class="dw-file-act"
       onclick="dashboardFilesItemAction('${w.id}','${f.id}','${esc(a.event)}')">${esc(a.label)}</button>`
  ).join('');
  const isAudio = f.mimeType.startsWith('audio/');
  const isPdf   = f.mimeType === 'application/pdf';
  const audioEl = isAudio
    ? `<audio controls src="${esc(f.path)}" style="height:28px;width:100%;margin-top:4px"></audio>` : '';
  // PDF inline thumbnail container (filled on demand)
  const pdfPreviewEl = isPdf
    ? `<div class="dw-file-pdf-preview" id="dw-pdf-${w.id}-${f.id}"></div>` : '';
  const pdfBtn = isPdf
    ? `<button class="dw-file-act"
         onclick="dashboardFilePdfPreview('${w.id}','${f.id}','${esc(f.path)}')">Preview</button>` : '';
  return `<div class="dw-file-row${sel}" ${selAttr}>
    <span class="dw-file-row-ico">${_mimeIcon(f.mimeType)}</span>
    <div class="dw-file-row-info">
      <div class="dw-file-row-name">${esc(f.name)} ${isNew}</div>
      <div class="dw-file-row-meta">${_fmtBytes(f.size)} · ${esc(f.createdAt)}</div>
      ${audioEl}${pdfPreviewEl}
    </div>
    ${(acts || pdfBtn) ? `<div class="dw-file-actions">${pdfBtn}${acts}</div>` : ''}
  </div>`;
}

function _mimeIcon(mime: string): string {
  if (mime.startsWith('image/')) return '🖼';
  if (mime.startsWith('audio/')) return '🎵';
  if (mime === 'application/pdf') return '📄';
  if (mime.startsWith('video/')) return '🎬';
  return '📁';
}

function _fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

// ── Registry ────────────────────────────────────────────────────────────────

const REGISTRY: Record<WidgetType, WidgetRenderer> = {
  conversation: conversationRenderer,
  chart:        chartRenderer,
  compute:      computeRenderer,
  files:        filesRenderer,
};

// ── Agent list panel (task 5.1-5.3) ────────────────────────────────────────

export function renderAgentList(): void {
  const rowsEl = document.getElementById('dash-agent-rows');
  if (!rowsEl) return;

  const all = _allAgents();
  const subEl = document.getElementById('dash-overview-sub');
  if (subEl) subEl.textContent = `${all.length} agents`;

  rowsEl.innerHTML = all.map(a => {
    const dotCls = { running: 'run', listening: 'live', waiting: 'warn' }[a.status] ?? 'idle';
    const on = a.id === state.curDashboardAgentId ? ' on' : '';
    return `<div class="dash-al-row${on}" onclick="selectDashboardAgent('${a.id}')">
      <span class="dash-al-dot dash-al-dot-${dotCls}"></span>
      <div>
        <div class="dal-name">${esc(a.name)}</div>
        <div class="dal-sub">${esc(a.type)}</div>
      </div>
    </div>`;
  }).join('');
}

export function selectDashboardAgent(agentId: string): void {
  state.curDashboardAgentId = agentId;
  document.querySelectorAll('.dash-al-row, .dash-al-overview').forEach(el =>
    el.classList.remove('on'));

  if (agentId === '__overview__') {
    document.getElementById('dash-overview-btn')?.classList.add('on');
    renderOverview();
  } else {
    document.querySelectorAll('.dash-al-row').forEach(el => {
      if ((el as HTMLElement).getAttribute('onclick') === `selectDashboardAgent('${agentId}')`)
        el.classList.add('on');
    });
    renderDashboard(agentId);
  }
}

// ── Overview panel (task 4.3) ───────────────────────────────────────────────

export function renderOverview(): void {
  const hdr = document.getElementById('dash-hdr');
  const pipe = document.getElementById('dash-pipe');
  if (hdr) hdr.style.display = 'none';
  if (pipe) pipe.style.display = 'none';

  const grid = document.getElementById('dash-grid');
  if (!grid) return;

  grid.innerHTML = _allAgents().map(a => {
    const dotCls = { running: 'run', listening: 'live', waiting: 'warn' }[a.status] ?? 'idle';
    const d = a.detail;
    const progress = d.running
      ? `<div class="dash-ov-prog"><div class="dash-ov-prog-fill"
           style="width:${Math.round(d.steps / Math.max(d.totalSteps, 1) * 100)}%"></div></div>`
      : '';
    return `<div class="dash-ov-card" onclick="selectDashboardAgent('${a.id}')">
      <div class="dash-ov-hdr">
        <span class="dash-al-dot dash-al-dot-${dotCls}"></span>
        <span class="dash-ov-name">${esc(a.name)}</span>
        <span class="dash-ov-type">${esc(a.type)}</span>
      </div>
      <div class="dash-ov-sub">${esc(a.sub)}</div>
      ${progress}
    </div>`;
  }).join('');
}

// ── Dashboard render (tasks 4.2, 6.1-6.2) ──────────────────────────────────

export function renderDashboard(agentId: string): void {
  const agent = _findAgent(agentId);
  if (!agent) return;

  // header (task 6.1)
  const hdr = document.getElementById('dash-hdr');
  if (hdr) {
    hdr.style.display = 'flex';
    const dot = document.getElementById('dash-hdr-dot');
    if (dot) dot.className = `dash-hdr-dot dash-hdr-dot-${agent.status}`;
    const nm = document.getElementById('dash-hdr-name');
    if (nm) nm.textContent = agent.name;
    const sub = document.getElementById('dash-hdr-sub');
    if (sub) sub.textContent = `${agent.type} · ${agent.sub}`;
    const st = document.getElementById('dash-hdr-status');
    if (st) st.textContent = _statusLabel(agent.status);
    const pause = document.getElementById('dash-hdr-pause') as HTMLButtonElement | null;
    const stop  = document.getElementById('dash-hdr-stop')  as HTMLButtonElement | null;
    const show  = agent.detail.running;
    if (pause) pause.style.display = show ? '' : 'none';
    if (stop)  stop.style.display  = show ? '' : 'none';
  }

  // load or infer dashboard config
  let db = state.agentDashboards[agentId] ?? loadDashboard(agentId);
  if (!db) {
    db = inferDefaultDashboard(agentId, agent);
    state.agentDashboards[agentId] = db;
    saveDashboard(agentId, db);
  }

  // pipeline bar (task 6.2)
  const pipe = document.getElementById('dash-pipe');
  if (pipe) {
    if (db.pipeline?.length) {
      pipe.style.display = 'flex';
      pipe.innerHTML = db.pipeline.map((s, i) => {
        const ico = { done: '✓', running: '▶', review: '⚠', idle: '○' }[s.state] ?? '○';
        return `${i > 0 ? '<span class="dash-pipe-arrow">›</span>' : ''}
          <div class="dash-pipe-stage dash-pipe-stage-${s.state}"
               onclick="dashboardPipeNav('${s.id}')">
            <span class="dash-pipe-ico">${ico}</span>
            <span class="dash-pipe-lbl">${esc(s.label)}</span>
          </div>`;
      }).join('');
    } else {
      pipe.style.display = 'none';
    }
  }

  // widget grid (task 4.2)
  const grid = document.getElementById('dash-grid');
  if (!grid) return;
  grid.innerHTML = '';
  db.widgets.forEach(w => {
    const renderer = REGISTRY[w.type];
    if (!renderer) return;
    const body = renderer.render(w);
    grid.appendChild(_widgetCard(w, body));
  });
}

// ── Default layout inference (task 11.1) ───────────────────────────────────

export function inferDefaultDashboard(agentId: string, agent: any): AgentDashboard {
  const widgets: Widget[] = [];
  let n = 0;

  // Always: conversation
  widgets.push({
    id: `${agentId}-w${++n}`, type: 'conversation', size: '2x1', agentId,
    state: agent.detail?.running ? 'running' : 'idle',
    title: 'Conversation',
    config: { mode: 'chat' } as ConversationConfig,
  });

  // Chart — auto-detect if table has timestamp column (8.2)
  const tableName = agentId.replace(/-/g, '_') + '_data';
  const tableData = DB[tableName];
  const hasTimestamp = tableData?.cols?.some(c =>
    /^(ts|time|timestamp|date|created_at|updated_at)$/i.test(c)
  ) ?? false;

  widgets.push({
    id: `${agentId}-w${++n}`, type: 'chart', size: '2x1', agentId,
    state: 'idle',
    title: 'Data',
    config: {
      table: tableName,
      view: hasTimestamp ? 'chart' : 'table',
      range: '24h',
    } as ChartConfig,
  });

  return { agentId, widgets };
}

// ── Interaction handlers (exposed as globals via main.ts) ──────────────────

export function dashboardWidgetToggleTail(widgetId: string): void {
  const db = state.agentDashboards[state.curDashboardAgentId];
  const w = db?.widgets.find(x => x.id === widgetId);
  if (!w) return;
  const cfg = w.config as ConversationConfig;
  cfg.mode = cfg.mode === 'tail' ? 'chat' : 'tail';
  renderDashboard(state.curDashboardAgentId);
}

export function dashboardConvSend(e: KeyboardEvent, widgetId: string): void {
  if (e.key === 'Enter') dashboardConvSendBtn(widgetId);
}

export function dashboardConvSendBtn(widgetId: string): void {
  const input = document.getElementById(`dw-conv-input-${widgetId}`) as HTMLInputElement | null;
  if (!input?.value.trim()) return;
  const msgs = document.getElementById(`dw-conv-msgs-${widgetId}`);
  if (msgs) {
    const d = document.createElement('div');
    d.className = 'dw-conv-msg user';
    d.innerHTML = `<div class="dw-msg-body">${esc(input.value)}</div>`;
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
  }
  (window as any).ghost?.send?.('agent_message', { widgetId, text: input.value });
  input.value = '';
}

export function dashboardChartView(widgetId: string, view: 'table' | 'chart'): void {
  const w = _curWidget(widgetId);
  if (w) { (w.config as ChartConfig).view = view; renderDashboard(state.curDashboardAgentId); }
}

export function dashboardChartRange(widgetId: string, range: '1h' | '24h' | '7d' | 'all'): void {
  const w = _curWidget(widgetId);
  if (w) { (w.config as ChartConfig).range = range; renderDashboard(state.curDashboardAgentId); }
}

export function dashboardChartConfig(widgetId: string): void {
  // Toggle config dropdown inline below the ··· button
  const existingMenu = document.getElementById(`dw-chart-menu-${widgetId}`);
  if (existingMenu) { existingMenu.remove(); return; }

  const w = _curWidget(widgetId);
  if (!w) return;
  const cfg = w.config as ChartConfig;

  // Get columns for the table
  const tableData = DB[cfg.table];
  const cols: string[] = tableData?.cols ?? [];

  const xOpts = cols.map(c =>
    `<option value="${esc(c)}"${cfg.xField === c ? ' selected' : ''}>${esc(c)}</option>`
  ).join('');
  const yOpts = cols.map(c =>
    `<option value="${esc(c)}"${cfg.yField === c ? ' selected' : ''}>${esc(c)}</option>`
  ).join('');

  const menu = document.createElement('div');
  menu.id = `dw-chart-menu-${widgetId}`;
  menu.className = 'dw-chart-menu';
  menu.innerHTML = `
    <div class="dw-chart-menu-row">
      <label class="dw-chart-menu-lbl">X axis</label>
      <select class="dw-chart-menu-sel" onchange="dashboardChartSetField('${widgetId}','x',this.value)">
        <option value="">— auto —</option>${xOpts}
      </select>
    </div>
    <div class="dw-chart-menu-row">
      <label class="dw-chart-menu-lbl">Y axis</label>
      <select class="dw-chart-menu-sel" onchange="dashboardChartSetField('${widgetId}','y',this.value)">
        <option value="">— auto —</option>${yOpts}
      </select>
    </div>
    <div class="dw-chart-menu-row">
      <label class="dw-chart-menu-lbl">Type</label>
      <select class="dw-chart-menu-sel" onchange="dashboardChartSetType('${widgetId}',this.value)">
        <option value="line"${(cfg.chartType ?? 'line') === 'line' ? ' selected' : ''}>Line</option>
        <option value="bar"${cfg.chartType === 'bar' ? ' selected' : ''}>Bar</option>
        <option value="scatter"${cfg.chartType === 'scatter' ? ' selected' : ''}>Scatter</option>
      </select>
    </div>`;

  // Insert after toolbar
  const toolbar = document.querySelector(`#dw-chart-body-${widgetId}`)?.previousElementSibling;
  toolbar?.insertAdjacentElement('afterend', menu);

  // Close on outside click
  const closeHandler = (e: MouseEvent) => {
    if (!menu.contains(e.target as Node)) {
      menu.remove();
      document.removeEventListener('click', closeHandler);
    }
  };
  setTimeout(() => document.addEventListener('click', closeHandler), 0);
}

export function dashboardChartSetField(widgetId: string, axis: 'x' | 'y', value: string): void {
  const w = _curWidget(widgetId);
  if (!w) return;
  const cfg = w.config as ChartConfig;
  if (axis === 'x') cfg.xField = value || undefined;
  else cfg.yField = value || undefined;
  // Refresh chart body if in chart view
  if (cfg.view === 'chart') {
    const bodyEl = document.getElementById(`dw-chart-body-${widgetId}`);
    if (bodyEl) bodyEl.innerHTML = _chartSvg();
  }
}

export function dashboardChartSetType(widgetId: string, chartType: 'line' | 'bar' | 'scatter'): void {
  const w = _curWidget(widgetId);
  if (!w) return;
  (w.config as ChartConfig).chartType = chartType;
  if ((w.config as ChartConfig).view === 'chart') {
    const bodyEl = document.getElementById(`dw-chart-body-${widgetId}`);
    if (bodyEl) bodyEl.innerHTML = _chartSvg();
  }
}

export function dashboardComputeAction(widgetId: string, event: string): void {
  (window as any).ghost?.send?.('pipeline_action', { widgetId, event });
}

export function dashboardFilesView(widgetId: string, viewMode: 'gallery' | 'list'): void {
  const w = _curWidget(widgetId);
  if (w) { (w.config as FilesConfig).viewMode = viewMode; renderDashboard(state.curDashboardAgentId); }
}

export function dashboardFileSelect(widgetId: string, fileId: string): void {
  const w = _curWidget(widgetId);
  if (!w) return;
  const cfg = w.config as FilesConfig;
  const sel = cfg.selected ?? [];
  const idx = sel.indexOf(fileId);
  if (idx >= 0) sel.splice(idx, 1); else sel.push(fileId);
  cfg.selected = sel;
  renderDashboard(state.curDashboardAgentId);
}

export function dashboardFilesItemAction(widgetId: string, fileId: string, event: string): void {
  (window as any).ghost?.send?.('file_action', { widgetId, fileId, event });
}

export function dashboardFilesAction(widgetId: string, event: string): void {
  const w = _curWidget(widgetId);
  const sel = (w?.config as FilesConfig)?.selected ?? [];
  (window as any).ghost?.send?.('file_batch_action', { widgetId, event, fileIds: sel });
}

export function dashboardPipeNav(stageId: string): void {
  const db = state.agentDashboards[state.curDashboardAgentId];
  const stage = db?.pipeline?.find(s => s.id === stageId);
  if (!stage) return;
  document.querySelector(`[data-widget-id="${stage.widgetId}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

export function dashboardPause(): void {
  (window as any).ghost?.send?.('agent_pause', { agentId: state.curDashboardAgentId });
}

export function dashboardStop(): void {
  (window as any).ghost?.send?.('agent_stop', { agentId: state.curDashboardAgentId });
}

export function openNewAgentModal(): void {
  (window as any).showToast?.('New Agent — coming soon');
}

// ── HITL handler (7.2) ──────────────────────────────────────────────────────

export function dashboardHitlRespond(widgetId: string, response: 'confirm' | 'skip'): void {
  // Remove the HITL message from UI
  const msgs = document.getElementById(`dw-conv-msgs-${widgetId}`);
  const hitlEl = msgs?.querySelector('.hitl');
  if (hitlEl) hitlEl.remove();
  // Emit IPC
  (window as any).ghost?.send?.('hitl_response', { widgetId, response });
}

// ── IPC-driven live update functions ────────────────────────────────────────

// 7.4: append a message to the conversation widget
export function appendConvMessage(
  widgetId: string,
  role: 'agent' | 'user' | 'hitl',
  text: string,
): void {
  const msgs = document.getElementById(`dw-conv-msgs-${widgetId}`);
  if (!msgs) return;

  const d = document.createElement('div');
  if (role === 'hitl') {
    d.className = 'dw-conv-msg agent hitl';
    d.innerHTML = `
      <span class="dw-msg-ico">⚠️</span>
      <div class="dw-msg-body dw-hitl-body">
        <div class="dw-hitl-msg">${esc(text)}</div>
        <div class="dw-hitl-btns">
          <button class="dw-hitl-btn confirm"
            onclick="dashboardHitlRespond('${widgetId}','confirm')">✓ Confirm</button>
          <button class="dw-hitl-btn skip"
            onclick="dashboardHitlRespond('${widgetId}','skip')">✕ Skip</button>
        </div>
      </div>`;
  } else {
    d.className = `dw-conv-msg ${role}`;
    const ico = role === 'agent' ? '<span class="dw-msg-ico">🤖</span>' : '';
    d.innerHTML = `${ico}<div class="dw-msg-body">${esc(text)}</div>`;
  }
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
}

// 7.5: append a line to the terminal tail view
export function appendTailLine(widgetId: string, line: string): void {
  const pre = document.getElementById(`dw-tail-${widgetId}`);
  if (!pre) return;
  // Remove blinking cursor, add line, re-add cursor
  const cursor = pre.querySelector('.dw-tail-cursor');
  if (cursor) cursor.remove();
  const d = document.createElement('div');
  d.textContent = line;
  pre.appendChild(d);
  // Keep max 200 lines
  while (pre.childElementCount > 201) pre.removeChild(pre.firstElementChild!);
  const newCursor = document.createElement('span');
  newCursor.className = 'dw-tail-cursor';
  newCursor.textContent = '▌';
  pre.appendChild(newCursor);
  pre.scrollTop = pre.scrollHeight;
}

// 8.5: refresh a chart widget when its table receives new data
export function refreshDashboardChart(table: string): void {
  // Find all chart widgets bound to this table across all dashboards
  Object.values(state.agentDashboards).forEach(db => {
    db.widgets.forEach(w => {
      if (w.type !== 'chart') return;
      const cfg = w.config as ChartConfig;
      if (cfg.table !== table) return;
      // Only refresh if currently visible (agentId is curDashboardAgentId)
      if (db.agentId !== state.curDashboardAgentId) return;
      const bodyEl = document.getElementById(`dw-chart-body-${w.id}`);
      if (!bodyEl) return;
      // Re-render the chart body in current view
      const view = cfg.view ?? 'table';
      bodyEl.innerHTML = view === 'chart' ? _chartSvg() : _chartTablePreview(cfg);
    });
  });
}

// 9.2: append a streaming chunk to a compute widget
export function appendComputeChunk(widgetId: string, text: string, done: boolean): void {
  const body = document.getElementById(`dw-compute-body-${widgetId}`);
  if (!body) return;

  // Remove cursor if present
  body.querySelector('.dw-compute-cursor')?.remove();

  // Append new text as a text node
  body.insertAdjacentText('beforeend', text);

  if (!done) {
    const cursor = document.createElement('span');
    cursor.className = 'dw-compute-cursor';
    cursor.textContent = '▌';
    body.appendChild(cursor);
  } else {
    // Streaming done: update widget state and show review actions if defined
    const db = state.agentDashboards[state.curDashboardAgentId];
    const w = db?.widgets.find(x => x.id === widgetId);
    if (w) {
      (w.config as ComputeConfig).streaming = false;
      if ((w.config as ComputeConfig).reviewActions?.length) {
        w.state = 'review';
        // Re-render the full widget card to show amber border + action bar
        renderDashboard(state.curDashboardAgentId);
      }
    }
  }
  body.scrollTop = body.scrollHeight;
}

// 10.5: request and show PDF thumbnail inline
export function dashboardFilePdfPreview(widgetId: string, fileId: string, path: string): void {
  const containerId = `dw-pdf-${widgetId}-${fileId}`;
  const container = document.getElementById(containerId);
  if (!container) return;

  if (container.innerHTML) {
    // Toggle off
    container.innerHTML = '';
    return;
  }

  container.innerHTML = '<div class="dw-pdf-loading">Loading preview…</div>';
  (window as any).ghost?.send?.('file_thumbnail', { fileId, path })
    .then(() => { /* response comes via file_thumbnail_result IPC */ })
    .catch(() => { container.innerHTML = '<div class="dw-pdf-loading">Preview unavailable</div>'; });
}

export function handleFileThumbnailResult(fileId: string, dataUrl: string): void {
  // Called from ipc when file_thumbnail_result arrives
  document.querySelectorAll(`[id^="dw-pdf-"]`).forEach(el => {
    const id = el.getAttribute('id') ?? '';
    if (!id.endsWith(`-${fileId}`)) return;
    (el as HTMLElement).innerHTML =
      `<img src="${dataUrl}" style="max-width:100%;border-radius:6px;margin-top:6px" alt="PDF preview">`;
  });
}

// 10.7: prepend a new file to a files widget
export function prependDashboardFile(widgetId: string, file: FileEntry): void {
  // Find the widget config to add to files list
  const w = Object.values(state.agentDashboards)
    .flatMap(db => db.widgets)
    .find(x => x.id === widgetId);
  if (!w || w.type !== 'files') return;

  const cfg = w.config as FilesConfig;
  const newFile = { ...file, isNew: true };
  cfg.files = [newFile, ...(cfg.files ?? [])];

  // Re-render if currently visible
  if (w.agentId === state.curDashboardAgentId) {
    const bodyEl = document.getElementById(`dw-files-body-${widgetId}`);
    if (!bodyEl) return;
    const isGallery = bodyEl.classList.contains('dw-files-gallery');
    const html = isGallery
      ? _galleryItem(newFile, w, cfg)
      : _listItem(newFile, w, cfg);
    bodyEl.insertAdjacentHTML('afterbegin', html);
  }
}

// ── Private helpers ─────────────────────────────────────────────────────────

function _allAgents() {
  return [
    ...AGENTS.browser.profiles.flatMap(p => p.agents),
    ...AGENTS.loop,
    ...AGENTS.reactive,
  ];
}

function _findAgent(id: string) {
  return _allAgents().find(a => a.id === id) ?? null;
}

function _curWidget(widgetId: string): Widget | undefined {
  return state.agentDashboards[state.curDashboardAgentId]?.widgets.find(w => w.id === widgetId);
}

function _statusLabel(status: string): string {
  const m: Record<string, string> = {
    running: '▶ Running', done: '✓ Done', idle: '⏸ Idle',
    listening: '● Listening', waiting: '⚠ Waiting',
  };
  return m[status] ?? status;
}
