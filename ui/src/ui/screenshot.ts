import { showToast } from './toast';

declare global {
  interface Window {
    __TAURI__?: any;
    ghost?: any;
    html2canvas?: any;
  }
}

export async function _autoScreenshot(): Promise<void> {
  if (!window.__TAURI__) return;
  const invoke = window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke;
  const dbg = (m: string) => invoke('debug_log', { msg: '[auto-ss] ' + m }).catch(() => {});
  await dbg('start');
  try {
    if (!window.html2canvas) {
      await dbg('loading html2canvas...');
      await new Promise<void>((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'lib/html2canvas.min.js';  // local copy
        s.onload = () => { dbg('html2canvas loaded OK'); resolve(); };
        s.onerror = (e) => { dbg('html2canvas load ERROR: ' + e); reject(e); };
        document.head.appendChild(s);
      });
    }
    await dbg('calling html2canvas...');
    const canvas = await window.html2canvas(document.body, {
      backgroundColor: null, scale: 1, logging: false, useCORS: true,
    });
    await dbg('canvas done, saving...');
    const dataUrl = canvas.toDataURL('image/png');
    await invoke('take_screenshot', { data: dataUrl, path: '/tmp/ghost-auto.png' });
    await dbg('saved to /tmp/ghost-auto.png');
  } catch(e) {
    await dbg('ERROR: ' + e);
  }
}

export async function takeScreenshot(): Promise<void> {
  if (!window.__TAURI__) { showToast('Screenshot only available in the desktop app'); return; }
  const invoke = window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke;
  showToast('📸 Capturing…');

  // Load html2canvas from CDN if not already loaded
  if (!window.html2canvas) {
    await new Promise<void>((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
      s.onload = () => resolve(); s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  try {
    const canvas = await window.html2canvas(document.body, {
      backgroundColor: null,
      scale: window.devicePixelRatio || 1,
      logging: false,
      useCORS: true,
    });
    const dataUrl = canvas.toDataURL('image/png');
    const path = await invoke('take_screenshot', { data: dataUrl });
    showToast(`📸 Saved: ${path}`);
    console.log('Screenshot saved to', path);
  } catch (err) {
    showToast(`Screenshot failed: ${err}`);
    console.error('Screenshot error:', err);
  }
}
