import { describe, it, expect } from 'vitest';
import { state } from '../state';

describe('initial state', () => {
  it('starts with no active session', () => {
    expect(state.activeSessId).toBeNull();
  });
  it('starts with 2 empty panes', () => {
    expect(state.panes).toHaveLength(2);
    state.panes.forEach(p => {
      expect(p.channelId).toBeNull();
      expect(p.terminal).toBeNull();
    });
  });
  it('starts in build mode', () => {
    expect(state.curMode).toBe('build');
  });
  it('has default tmux session name', () => {
    expect(state.tmuxSession).toBe('ghost');
  });
  it('starts with no runner agents', () => {
    expect(Object.keys(state.runnerAgents)).toHaveLength(0);
  });
});
