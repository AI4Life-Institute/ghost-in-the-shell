import { describe, it, expect, beforeEach } from 'vitest';
import { DATA_FILES } from '../data/db';

describe('DATA_FILES structure', () => {
  it('has agents and skills top-level folders', () => {
    const names = DATA_FILES.map(n => n.name);
    expect(names).toContain('agents');
    expect(names).toContain('skills');
  });

  it('agents folder has sqlite databases', () => {
    const agentsFolder = DATA_FILES.find(n => n.name === 'agents');
    expect(agentsFolder?.children?.some(c => c.type === 'sqlite')).toBe(true);
  });
});

describe('renderDataTree()', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="data-tree-inner"></div>';
    // Reset DATA_FILES open state
    DATA_FILES.forEach(n => { if (n.type === 'folder') n.open = true; });
  });

  it('renders folder nodes', async () => {
    const { renderDataTree } = await import('../views/data-tree');
    renderDataTree();
    const inner = document.getElementById('data-tree-inner')!;
    expect(inner.innerHTML).toContain('agents');
    expect(inner.innerHTML).toContain('skills');
  });

  it('renders sqlite tables as children', async () => {
    const { renderDataTree } = await import('../views/data-tree');
    renderDataTree();
    expect(document.querySelector('.table-item')).not.toBeNull();
  });
});

describe('toggleDataNode()', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="data-tree-inner"></div>';
  });

  it('toggles open state of a node', async () => {
    const { toggleDataNode } = await import('../views/data-tree');
    const node = DATA_FILES.find(n => n.id === 'f-agents');
    const initialOpen = node?.open;
    toggleDataNode('f-agents');
    expect(node?.open).toBe(!initialOpen);
  });
});
