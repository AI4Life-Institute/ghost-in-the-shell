import { state } from '../state';
import { esc } from '../utils';
import { DB, TABLE_SOURCE_MAP } from '../data/db';
import { SKILLS } from '../data/skills';
import { _flatAgent } from './agents';
import { showToast } from '../ui/toast';
import { setMode } from './mode';
import { renderDataTree } from './data-tree';

export function selTable(el: HTMLElement | null, id: string): void {
  document.querySelectorAll('.db-tbl-item').forEach(x => x.classList.remove('on'));
  if (el) el.classList.add('on');
  state.curTableId = id;
  state.sortCol = null; state.sortDir = 1; state.filterText = '';
  const s = document.getElementById('db-search') as HTMLInputElement;
  if (s) s.value = '';
  closeDrawer();
  renderTable();
}

export function selDataTable(el: HTMLElement | null, tableId: string): void {
  state.curTableId = tableId;
  state.sortCol = null; state.sortDir = 1; state.filterText = '';
  const s = document.getElementById('db-search') as HTMLInputElement;
  if (s) s.value = '';
  closeDrawer();
  renderDataTree();
  renderTable();
}

export function renderTable(): void {
  const data = DB[state.curTableId];
  if (!data) return;
  const tname = document.getElementById('db-tname');
  if (tname) tname.textContent = state.curTableId;

  const sourceMeta = TABLE_SOURCE_MAP[state.curTableId];
  let sourceBadgeHtml = '';
  if (sourceMeta) {
    if (sourceMeta.type === 'agent') {
      const a = _flatAgent(sourceMeta.id);
      const label = a ? esc(a.name) : esc(sourceMeta.id);
      sourceBadgeHtml = `<span class="db-source-badge">Source: Agent — <a onclick="setMode('agents')">${label}</a></span>`;
    } else if (sourceMeta.type === 'skill') {
      const sk = SKILLS[sourceMeta.id];
      const label = sk ? esc(sk.name) : esc(sourceMeta.id);
      sourceBadgeHtml = `<span class="db-source-badge">Source: Skill — <a onclick="setMode('skill')">${label}</a></span>`;
    }
  }
  if (tname) tname.innerHTML = state.curTableId + sourceBadgeHtml;

  let rows = data.rows.filter(r =>
    !state.filterText || Object.values(r).some(v => v && String(v).toLowerCase().includes(state.filterText))
  );

  if (state.sortCol) {
    rows = [...rows].sort((a, b) => {
      const av = a[state.sortCol!] ?? '', bv = b[state.sortCol!] ?? '';
      return String(av).localeCompare(String(bv), undefined, {numeric:true}) * state.sortDir;
    });
  }

  const cnt = document.getElementById('db-count');
  if (cnt) cnt.textContent = `${rows.length} row${rows.length!==1?'s':''}`;

  if (rows.length === 0 && !state.filterText) {
    const thead = document.getElementById('db-thead');
    if (thead) thead.innerHTML = '';
    const tbody = document.getElementById('db-tbody');
    if (tbody) tbody.innerHTML = `<tr class="db-empty-row"><td colspan="99">No data yet. Run an Agent or Skill to start collecting.</td></tr>`;
    return;
  }

  const thead = document.getElementById('db-thead');
  if (thead) thead.innerHTML = '<tr>' + data.cols.map(c => {
    const ico = state.sortCol===c ? (state.sortDir>0?'↑':'↓') : '';
    return `<th onclick="sortBy('${c}')">${esc(c)} <span class="sort-ico">${ico}</span></th>`;
  }).join('') + '</tr>';

  const STATUS_COLOR: Record<string, string> = {done:'#16a34a',running:'#4f46e5',queued:'rgba(0,0,0,.45)',needs_review:'#b45309',failed:'#dc2626'};
  const mono = new Set(['id','task_id','ts','created_at','input','output','value','size_bytes','seq']);
  const tbody = document.getElementById('db-tbody');
  if (tbody) tbody.innerHTML = rows.map((r, ri) =>
    `<tr class="row-click" onclick="openDrawer(${ri}, ${JSON.stringify(JSON.stringify(r))})">` +
    data.cols.map(c => {
      const v = r[c];
      if (v===null||v===undefined) return `<td><span class="db-null">null</span></td>`;
      if (c==='status') return `<td class="status" style="color:${STATUS_COLOR[v]||'inherit'}">${esc(String(v))}</td>`;
      if (c==='size_kb' && v>0) return `<td class="mono">${Number(v).toFixed(1)} KB</td>`;
      if (c==='size_bytes' && v>0) return `<td class="mono">${(v/1024).toFixed(1)} KB</td>`;
      if (c==='sensitive') return `<td>${v?'🔒':''}</td>`;
      const s = String(v);
      const disp = s.length > 60 ? s.slice(0,58)+'…' : s;
      return `<td class="${mono.has(c)?'mono':''}">${esc(disp)}</td>`;
    }).join('') + '</tr>'
  ).join('');
}

export function sortBy(col: string): void {
  if (state.sortCol===col) state.sortDir *= -1; else { state.sortCol = col; state.sortDir = 1; }
  renderTable();
}

export function filterTable(): void {
  state.filterText = (document.getElementById('db-search') as HTMLInputElement).value.toLowerCase();
  renderTable();
}

export function openDrawer(ri: number, rjson: string): void {
  const r = JSON.parse(rjson);
  const title = document.getElementById('drawer-title');
  if (title) title.textContent = state.curTableId + ' · row';
  const body = document.getElementById('drawer-body');
  if (body) body.innerHTML = Object.entries(r).map(([k,v]) => {
    const isLong = v && String(v).length > 40;
    const disp = v===null||v===undefined
      ? '<span class="db-null">null</span>'
      : `<div class="drawer-val${isLong?' mono':''}">${esc(String(v))}</div>`;
    return `<div class="drawer-field"><div class="drawer-key">${esc(k)}</div>${disp}</div>`;
  }).join('<div class="drawer-sep"></div>');
  document.getElementById('db-drawer')!.classList.add('on');
}

export function closeDrawer(): void {
  const d = document.getElementById('db-drawer');
  if (d) d.classList.remove('on');
}

export function exportCSV(): void {
  const data = DB[state.curTableId];
  if (!data) return;
  const lines = [data.cols.join(','), ...data.rows.map(r => data.cols.map(c => JSON.stringify(r[c]??'')).join(','))];
  const blob = new Blob([lines.join('\n')], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = state.curTableId + '.csv'; a.click();
}

export function refreshTable(): void { renderTable(); }

export function switchDbView(tab: string): void {
  document.querySelectorAll('.db-view-tab').forEach(t => t.classList.remove('on'));
  const el = document.querySelector(`.db-view-tab[data-view="${tab}"]`);
  if (el) el.classList.add('on');
  if (tab === 'cards') showToast('Cards view coming soon');
}
