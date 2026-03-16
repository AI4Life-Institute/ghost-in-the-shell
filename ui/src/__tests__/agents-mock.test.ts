import { describe, it, expect } from 'vitest';
import { AGENTS } from '../data/agents';

describe('AGENTS mock data', () => {
  it('has browser profiles', () => {
    expect(AGENTS.browser.profiles.length).toBeGreaterThan(0);
  });

  it('has loop agents', () => {
    expect(AGENTS.loop.length).toBeGreaterThan(0);
  });

  it('has reactive agents', () => {
    expect(AGENTS.reactive.length).toBeGreaterThan(0);
  });

  it('browser agents have required fields', () => {
    AGENTS.browser.profiles.forEach(profile => {
      expect(profile.id).toBeTruthy();
      expect(profile.label).toBeTruthy();
      profile.agents.forEach(agent => {
        expect(agent.id).toBeTruthy();
        expect(agent.name).toBeTruthy();
        expect(agent.status).toBeTruthy();
        expect(agent.detail).toBeDefined();
        expect(Array.isArray(agent.detail.log)).toBe(true);
      });
    });
  });

  it('loop agents have all required fields', () => {
    AGENTS.loop.forEach(agent => {
      expect(agent.id).toBeTruthy();
      expect(agent.name).toBeTruthy();
      expect(['running', 'done', 'idle', 'waiting']).toContain(agent.status);
    });
  });

  it('reactive agents have valid statuses', () => {
    AGENTS.reactive.forEach(agent => {
      expect(['listening', 'waiting', 'running', 'idle', 'done']).toContain(agent.status);
    });
  });
});
