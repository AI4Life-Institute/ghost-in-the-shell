/**
 * electron/preload.js
 * Runs in an isolated context before the renderer page loads.
 * Exposes a safe, narrow `window.ghost` API to the renderer via contextBridge.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('ghost', {
  /**
   * Send a command to the Python backend.
   * @param {string} cmd   - Command name (e.g. 'list-sessions', 'send-keys')
   * @param {object} payload - Arbitrary JSON payload
   * @returns {Promise<any>} Resolves with the response from main process
   */
  send(cmd, payload = {}) {
    return ipcRenderer.invoke('python-cmd', { cmd, payload });
  },

  /**
   * Subscribe to events forwarded from the Python backend.
   * @param {string}   event - Event name to filter on (or '*' for all)
   * @param {Function} cb    - Called with the parsed JSON object from Python
   * @returns {Function} Unsubscribe function
   */
  on(event, cb) {
    const handler = (_ipcEvent, data) => {
      // If event is '*' or data.event matches, call the callback
      if (event === '*' || data?.event === event) {
        cb(data);
      }
    };
    ipcRenderer.on('python-event', handler);
    // Return the raw handler so callers can pass it to off()
    return handler;
  },

  /**
   * Unsubscribe a previously registered listener.
   * @param {Function} handler - The handler reference returned by on()
   */
  off(handler) {
    ipcRenderer.removeListener('python-event', handler);
  },

  /**
   * Convenience: subscribe to ALL Python events (event === '*').
   * @param {Function} cb
   * @returns {Function} Unsubscribe function
   */
  onAny(cb) {
    const handler = (_ipcEvent, data) => cb(data);
    ipcRenderer.on('python-event', handler);
    return handler;
  },
});
