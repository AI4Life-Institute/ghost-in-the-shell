import { describe, it, expect, beforeEach } from 'vitest';
import { _sessLabel, _shortDir, _projectName, _groupDir, buildSessionTree, recentSessions } from '../views/build';
import { state } from '../state';
import type { Session } from '../types';

// Minimal session factory
function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    channel_id: 'ch_' + Math.random().toString(36).slice(2),
    window_name: undefined,
    work_dir: '/Users/weiliu/projects/ghost',
    coding_cli: 'claude',
    platform: 'desktop',
    alive: true,
    created_at: '2026-03-15T10:00:00',
    ...overrides,
  };
}

describe('_sessLabel()', () => {
  it('uses window_name when non-numeric and set', () => {
    expect(_sessLabel(makeSession({ window_name: 'my-project' }))).toBe('my-project');
  });
  it('falls back to last segment of work_dir', () => {
    expect(_sessLabel(makeSession({ window_name: undefined }))).toBe('ghost');
  });
  it('ignores numeric window_name', () => {
    expect(_sessLabel(makeSession({ window_name: '1' }))).toBe('ghost');
  });
  it('falls back to coding_cli when no work_dir', () => {
    expect(_sessLabel(makeSession({ work_dir: undefined, coding_cli: 'codex' }))).toBe('codex');
  });
});

describe('_shortDir()', () => {
  it('replaces home dir with ~', () => {
    expect(_shortDir('/Users/weiliu/projects')).toBe('~/projects');
  });
  it('strips /Volumes/DriveName prefix', () => {
    expect(_shortDir('/Volumes/Crucial_8T/src/ai4life')).toBe('/src/ai4life');
  });
  it('returns ~ for empty input', () => {
    expect(_shortDir(undefined)).toBe('~');
    expect(_shortDir('')).toBe('~');
  });
  it('returns / when drive root becomes empty', () => {
    expect(_shortDir('/Volumes/Crucial_8T')).toBe('/');
  });
});

describe('_projectName()', () => {
  it('returns last 2 path segments', () => {
    expect(_projectName('/Users/weiliu/ai4life/ghost-in-the-shell')).toBe('ai4life/ghost-in-the-shell');
  });
  it('returns last 2 segments even for paths under home dir', () => {
    // /Users/weiliu/myproject → parts=['Users','weiliu','myproject'] → last 2 = 'weiliu/myproject'
    expect(_projectName('/Users/weiliu/myproject')).toBe('weiliu/myproject');
  });
  it('handles undefined', () => {
    expect(_projectName(undefined)).toBe('~');
  });
});

describe('_groupDir()', () => {
  it('uses up to 3 path segments for grouping', () => {
    const dir = _groupDir('/Users/weiliu/a/b/c/d');
    expect(dir.split('/').length).toBeLessThanOrEqual(3);
  });
  it('same shallow project groups together', () => {
    // Paths with same 3-segment prefix after home-dir shortening group together
    const d1 = _groupDir('/Users/weiliu/ai4life/ghost');
    const d2 = _groupDir('/Users/weiliu/ai4life/ghost');
    expect(d1).toBe(d2);
  });
});

describe('buildSessionTree()', () => {
  it('separates alive from dead sessions', () => {
    const sessions: Session[] = [
      makeSession({ channel_id: 'alive1', alive: true }),
      makeSession({ channel_id: 'dead1', alive: false }),
    ];
    const { dead } = buildSessionTree(sessions);
    expect(dead).toHaveLength(1);
    expect(dead[0].channel_id).toBe('dead1');
  });

  it('groups sessions by work_dir', () => {
    const sessions: Session[] = [
      makeSession({ channel_id: 'a', work_dir: '/Users/weiliu/proj1' }),
      makeSession({ channel_id: 'b', work_dir: '/Users/weiliu/proj1' }),
      makeSession({ channel_id: 'c', work_dir: '/Users/weiliu/proj2' }),
    ];
    const { byDir } = buildSessionTree(sessions);
    expect(byDir.size).toBe(2);
  });

  it('nests child sessions under parent', () => {
    const parent = makeSession({ channel_id: 'parent' });
    const child = makeSession({ channel_id: 'child', parent_channel_id: 'parent', work_dir: parent.work_dir });
    const { byDir } = buildSessionTree([parent, child]);
    const group = byDir.values().next().value;
    expect(group.roots).toHaveLength(1);
    expect(group.roots[0].channel_id).toBe('parent');
    expect(group.childMap.get('parent')).toHaveLength(1);
  });

  it('sorts desktop sessions before others', () => {
    const sessions: Session[] = [
      makeSession({ channel_id: 'web', platform: 'web', created_at: '2026-03-15T12:00:00' }),
      makeSession({ channel_id: 'desk', platform: 'desktop', created_at: '2026-03-15T10:00:00' }),
    ];
    const { byDir } = buildSessionTree(sessions);
    const group = byDir.values().next().value;
    expect(group.roots[0].channel_id).toBe('desk');
  });

  it('handles empty input', () => {
    const { byDir, dead } = buildSessionTree([]);
    expect(byDir.size).toBe(0);
    expect(dead).toHaveLength(0);
  });
});

describe('recentSessions()', () => {
  beforeEach(() => {
    state.allSessions = [];
    state.panes = [
      { channelId: null, terminal: null, fitAddon: null, ro: null },
      { channelId: null, terminal: null, fitAddon: null, ro: null },
    ];
  });

  it('returns alive sessions not in any pane', () => {
    const s1 = makeSession({ channel_id: 'ch1' });
    const s2 = makeSession({ channel_id: 'ch2' });
    state.allSessions = [s1, s2];
    state.panes[0].channelId = 'ch1';
    const recent = recentSessions();
    expect(recent).toHaveLength(1);
    expect(recent[0].channel_id).toBe('ch2');
  });

  it('excludes dead sessions', () => {
    state.allSessions = [makeSession({ channel_id: 'dead', alive: false })];
    expect(recentSessions()).toHaveLength(0);
  });

  it('returns at most 5', () => {
    state.allSessions = Array.from({ length: 10 }, (_, i) =>
      makeSession({ channel_id: 'ch' + i, created_at: `2026-03-15T${String(i).padStart(2,'0')}:00:00` })
    );
    expect(recentSessions()).toHaveLength(5);
  });
});
