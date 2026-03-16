import { describe, it, expect, beforeEach } from 'vitest';
import { state } from '../state';

// Setup minimal DOM fixtures needed by renderTable
function setupDOM() {
  document.body.innerHTML = `
    <span id="db-tname"></span>
    <span id="db-count"></span>
    <table>
      <thead id="db-thead"></thead>
      <tbody id="db-tbody"></tbody>
    </table>
    <input id="db-search" />
    <div id="db-drawer"></div>
  `;
}

describe('renderTable()', () => {
  beforeEach(() => {
    setupDOM();
    state.curTableId = 'btc_prices';
    state.sortCol = null;
    state.sortDir = 1;
    state.filterText = '';
  });

  it('renders table name', async () => {
    const { renderTable } = await import('../views/data');
    renderTable();
    expect(document.getElementById('db-tname')!.textContent).toContain('btc_prices');
  });

  it('renders correct row count', async () => {
    const { renderTable } = await import('../views/data');
    renderTable();
    const count = document.getElementById('db-count')!.textContent;
    expect(count).toMatch(/10 rows/);
  });

  it('renders thead columns', async () => {
    const { renderTable } = await import('../views/data');
    renderTable();
    const ths = document.querySelectorAll('#db-thead th');
    expect(ths.length).toBeGreaterThan(0);
    const cols = Array.from(ths).map(th => th.textContent?.trim());
    expect(cols.some(c => c?.includes('id'))).toBe(true);
    expect(cols.some(c => c?.includes('price'))).toBe(true);
  });

  it('filters rows by text', async () => {
    const { renderTable } = await import('../views/data');
    state.filterText = '$67010.00'; // only row 1
    renderTable();
    const rows = document.querySelectorAll('#db-tbody tr');
    expect(rows.length).toBe(1);
  });

  it('shows empty state for unknown table', async () => {
    const { renderTable } = await import('../views/data');
    state.curTableId = 'nonexistent_table';
    // Should not throw
    expect(() => renderTable()).not.toThrow();
  });
});

describe('sortBy()', () => {
  beforeEach(() => {
    setupDOM();
    state.curTableId = 'btc_prices';
    state.sortCol = null;
    state.sortDir = 1;
    state.filterText = '';
  });

  it('sets sortCol and renders', async () => {
    const { sortBy } = await import('../views/data');
    sortBy('price');
    expect(state.sortCol).toBe('price');
    expect(state.sortDir).toBe(1);
  });

  it('toggles direction on second call', async () => {
    const { sortBy } = await import('../views/data');
    sortBy('price');
    sortBy('price');
    expect(state.sortDir).toBe(-1);
  });

  it('resets direction when switching columns', async () => {
    const { sortBy } = await import('../views/data');
    sortBy('price');
    sortBy('price'); // dir = -1
    sortBy('id');    // new col, dir resets to 1
    expect(state.sortCol).toBe('id');
    expect(state.sortDir).toBe(1);
  });
});
