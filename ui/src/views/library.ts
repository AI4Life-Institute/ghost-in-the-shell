import { state } from '../state';
import { setMode } from './mode';

export function initLibrary(): void {
  switchLibraryTab(state.curLibraryTab);
}

export function switchLibraryTab(tab: 'skills' | 'data'): void {
  state.curLibraryTab = tab;
  document.querySelectorAll('.lib-tab').forEach(el => {
    el.classList.toggle('on', el.getAttribute('data-tab') === tab);
  });
  const skillsPane = document.getElementById('lib-skills-pane');
  const dataPane = document.getElementById('lib-data-pane');
  if (skillsPane) { skillsPane.style.display = tab === 'skills' ? 'flex' : 'none'; skillsPane.style.flex = '1'; }
  if (dataPane)   { dataPane.style.display   = tab === 'data'   ? 'flex' : 'none'; dataPane.style.flex   = '1'; }
}

export function linkToLibrary(tab: 'skills' | 'data', tableId?: string): void {
  setMode('library');
  switchLibraryTab(tab);
  if (tab === 'data' && tableId) {
    (window as any).selDataTable?.(null, tableId);
  }
}
