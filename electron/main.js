/**
 * electron/main.js
 * Ghost — Electron main process.
 *
 * Responsibilities:
 *  - Create the native macOS window with frosted-glass vibrancy
 *  - Spawn the Python backend via PythonBridge
 *  - Bridge IPC between the renderer and Python (JSON-over-stdout protocol)
 */

'use strict';

const {
  app,
  BrowserWindow,
  ipcMain,
  systemPreferences,
  shell,
} = require('electron');
const path = require('path');
const bridge = require('./bridge');

// ── Constants ────────────────────────────────────────────────────────────────

const PROJECT_ROOT = path.join(__dirname, '..');
const UI_INDEX = path.join(PROJECT_ROOT, 'ui', 'index.html');
const IS_DEV = process.argv.includes('--dev');

// ── Window management ────────────────────────────────────────────────────────

/** @type {BrowserWindow | null} */
let win = null;

function createWindow() {
  win = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,

    // macOS: inset traffic-light buttons inside the window chrome
    titleBarStyle: 'hiddenInset',

    // Transparent background — the renderer aurora blobs handle visuals
    backgroundColor: '#00000000',
    transparent: true,

    // Prevent a white flash before the page paints
    show: false,

    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // needed so preload can use require()
      preload: path.join(__dirname, 'preload.js'),
      devTools: IS_DEV,
    },
  });

  // macOS frosted-glass vibrancy behind the transparent window
  if (process.platform === 'darwin') {
    win.setVibrancy('under-window');
  }

  // Load the UI
  win.loadFile(UI_INDEX);

  // Show the window once the page is ready to avoid a blank flash
  win.once('ready-to-show', () => {
    win.show();
    if (IS_DEV) {
      win.webContents.openDevTools({ mode: 'detach' });
    }
  });

  // Open external links in the default browser, not inside Electron
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  win.on('closed', () => {
    win = null;
  });
}

// ── Python bridge wiring ─────────────────────────────────────────────────────

function startPythonBridge() {
  bridge.start(PROJECT_ROOT);

  // Forward every JSON message from Python → renderer
  bridge.on('message', (data) => {
    if (win && !win.isDestroyed()) {
      win.webContents.send('python-event', data);
    }
  });

  bridge.on('error', (err) => {
    console.error('[main] Python bridge error:', err);
    // Optionally forward errors to the renderer so the UI can display them
    if (win && !win.isDestroyed()) {
      win.webContents.send('python-event', { event: 'bridge-error', ...err });
    }
  });

  bridge.on('exit', ({ code, signal }) => {
    if (win && !win.isDestroyed()) {
      win.webContents.send('python-event', {
        event: 'bridge-exit',
        code,
        signal,
      });
    }
  });
}

// Renderer → Python: handle 'python-cmd' IPC calls from preload/renderer
ipcMain.handle('python-cmd', async (_event, { cmd, payload }) => {
  const ok = bridge.send({ cmd, payload });
  return { ok };
});

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.setName('Ghost');

app.whenReady().then(() => {
  createWindow();
  startPythonBridge();

  // macOS: re-create window when dock icon is clicked and no windows exist
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

// Quit when all windows are closed (except on macOS)
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// Clean up the Python process on quit
app.on('before-quit', () => {
  bridge.stop();
});

// Handle uncaught exceptions gracefully — don't let a bug take down the app
process.on('uncaughtException', (err) => {
  console.error('[main] uncaughtException:', err);
});
