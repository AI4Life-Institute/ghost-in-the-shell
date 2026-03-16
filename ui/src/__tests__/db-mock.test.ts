import { describe, it, expect } from 'vitest';
import { DB, DB_COLLECTIONS, TABLE_SOURCE_MAP } from '../data/db';

describe('DB mock data', () => {
  it('has tasks table with cols and rows', () => {
    expect(DB.tasks.cols).toContain('id');
    expect(DB.tasks.cols).toContain('status');
    expect(DB.tasks.rows.length).toBeGreaterThan(0);
  });

  it('btc_prices has 10 generated rows', () => {
    expect(DB.btc_prices.rows.length).toBe(10);
  });

  it('all tables have cols and rows arrays', () => {
    Object.entries(DB).forEach(([name, table]) => {
      expect(Array.isArray(table.cols), `${name}.cols should be array`).toBe(true);
      expect(Array.isArray(table.rows), `${name}.rows should be array`).toBe(true);
    });
  });
});

describe('TABLE_SOURCE_MAP', () => {
  it('maps btc_prices to agent source', () => {
    expect(TABLE_SOURCE_MAP['btc_prices']).toEqual({ type: 'agent', id: 'btc-monitor' });
  });

  it('maps market_scans to skill source', () => {
    expect(TABLE_SOURCE_MAP['market_scans']).toEqual({ type: 'skill', id: 'market' });
  });

  it('has entries from both fromAgents and fromSkills', () => {
    const types = Object.values(TABLE_SOURCE_MAP).map(v => v.type);
    expect(types).toContain('agent');
    expect(types).toContain('skill');
  });
});

describe('DB_COLLECTIONS', () => {
  it('has fromAgents, fromSkills, manual arrays', () => {
    expect(Array.isArray(DB_COLLECTIONS.fromAgents)).toBe(true);
    expect(Array.isArray(DB_COLLECTIONS.fromSkills)).toBe(true);
    expect(Array.isArray(DB_COLLECTIONS.manual)).toBe(true);
  });

  it('each collection has required fields', () => {
    [...DB_COLLECTIONS.fromAgents, ...DB_COLLECTIONS.fromSkills, ...DB_COLLECTIONS.manual].forEach(c => {
      expect(c.id).toBeTruthy();
      expect(c.name).toBeTruthy();
      expect(typeof c.rows).toBe('number');
    });
  });
});
