import { state } from '../state';
import { esc } from '../utils';
import { DATA_FILES } from '../data/db';
import type { DataNode } from '../types';

export function _findDataNode(nodes: DataNode[], id: string): DataNode | null {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (n.children) { const f = _findDataNode(n.children, id); if (f) return f; }
  }
  return null;
}

export function _renderTreeNodes(nodes: DataNode[], depth: number): string {
  const pad = (d: number) => `padding-left:${10 + d * 16}px`;
  let html = '';
  nodes.forEach(n => {
    if (n.type === 'folder') {
      const arrow = n.children && n.children.length
        ? `<span class="dtree-arrow${n.open?' open':''}" style="margin-left:auto">›</span>` : '';
      html += `<div class="dtree-node" style="${pad(depth)}" onclick="toggleDataNode('${n.id}')">
        <span class="dtree-ico">📁</span><span class="dtree-name">${esc(n.name)}</span>${arrow}</div>`;
      if (n.children && n.children.length) {
        html += `<div class="dtree-children${n.open?' open':''}" id="dtree-ch-${n.id}">`;
        html += _renderTreeNodes(n.children, depth + 1);
        html += '</div>';
      }
    } else if (n.type === 'sqlite') {
      const arrow = `<span class="dtree-arrow${n.open?' open':''}" style="margin-left:auto">›</span>`;
      html += `<div class="dtree-node" style="${pad(depth)}" onclick="toggleDataNode('${n.id}')">
        <span class="dtree-ico">🗄</span><span class="dtree-name">${esc(n.name)}</span>${arrow}</div>`;
      html += `<div class="dtree-children${n.open?' open':''}" id="dtree-ch-${n.id}">`;
      (n.tables || []).forEach(t => {
        const sel = state.curTableId === t.id;
        html += `<div class="dtree-node table-item${sel?' sel':''}" style="${pad(depth+1)}"
          onclick="selDataTable(this,'${t.id}')">
          <span class="dtree-ico" style="font-size:10px;color:rgba(0,0,0,.30)">↳</span>
          <span class="dtree-name">${esc(t.name)}</span>
          <span class="dtree-count">${t.rows}</span>
        </div>`;
      });
      html += '</div>';
    } else if (n.type === 'csv') {
      const sel = state.curTableId === n.tableId;
      html += `<div class="dtree-node${sel?' sel':''}" style="${pad(depth)}"
        onclick="selDataTable(this,'${n.tableId}')">
        <span class="dtree-ico">📄</span>
        <span class="dtree-name">${esc(n.name)}</span>
        <span class="dtree-count">${n.rows}</span>
      </div>`;
    }
  });
  return html;
}

export function renderDataTree(): void {
  const inner = document.getElementById('data-tree-inner');
  if (inner) inner.innerHTML = _renderTreeNodes(DATA_FILES, 0);
}

export function toggleDataNode(nodeId: string): void {
  const node = _findDataNode(DATA_FILES, nodeId);
  if (node) { node.open = !node.open; renderDataTree(); }
}
