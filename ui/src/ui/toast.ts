import { state } from '../state';

export function showToast(msg: string, onView?: () => void): void {
  const toast = document.getElementById('toast');
  const msgEl = document.getElementById('toast-msg');
  if (!toast || !msgEl) return;
  msgEl.textContent = msg;
  const viewLink = document.getElementById('toast-view-link');
  if (viewLink) {
    if (onView) {
      viewLink.style.display = 'inline';
      (viewLink as HTMLElement).onclick = onView;
    } else {
      viewLink.style.display = 'none';
    }
  }
  toast.classList.add('on');
  if (state.toastTimer) clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => dismissToast(), 4000);
}

export function dismissToast(): void {
  const el = document.getElementById('toast');
  if (el) el.classList.remove('on');
}
