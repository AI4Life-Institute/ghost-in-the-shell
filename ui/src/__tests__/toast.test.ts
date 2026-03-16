import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { showToast, dismissToast } from '../ui/toast';
import { state } from '../state';

function setupDOM() {
  document.body.innerHTML = `
    <div id="toast">
      <span id="toast-msg"></span>
      <a id="toast-view-link" style="display:none"></a>
    </div>
  `;
}

describe('showToast()', () => {
  beforeEach(() => {
    setupDOM();
    state.toastTimer = null;
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('adds .on class to toast', () => {
    showToast('hello');
    expect(document.getElementById('toast')!.classList.contains('on')).toBe(true);
  });

  it('sets message text', () => {
    showToast('Something happened');
    expect(document.getElementById('toast-msg')!.textContent).toBe('Something happened');
  });

  it('hides view link when no callback', () => {
    showToast('no link');
    expect(document.getElementById('toast-view-link')!.style.display).toBe('none');
  });

  it('shows view link when callback provided', () => {
    showToast('with link', () => {});
    expect(document.getElementById('toast-view-link')!.style.display).toBe('inline');
  });

  it('auto-dismisses after 4 seconds', () => {
    showToast('auto dismiss');
    expect(document.getElementById('toast')!.classList.contains('on')).toBe(true);
    vi.advanceTimersByTime(4000);
    expect(document.getElementById('toast')!.classList.contains('on')).toBe(false);
  });
});

describe('dismissToast()', () => {
  beforeEach(setupDOM);

  it('removes .on class', () => {
    document.getElementById('toast')!.classList.add('on');
    dismissToast();
    expect(document.getElementById('toast')!.classList.contains('on')).toBe(false);
  });
});
