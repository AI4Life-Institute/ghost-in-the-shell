/**
 * electron/bridge.js
 * Manages the Python subprocess and provides a clean event-emitter interface.
 */

const { EventEmitter } = require('events');
const { spawn } = require('child_process');
const path = require('path');

class PythonBridge extends EventEmitter {
  constructor() {
    super();
    this._proc = null;
    this._buf = '';
    this._ready = false;
  }

  /**
   * Start the Python backend.
   * @param {string} projectRoot - Absolute path to the project root (cwd for uv).
   */
  start(projectRoot) {
    if (this._proc) return;

    console.log('[bridge] spawning Python backend in', projectRoot);

    this._proc = spawn('uv', ['run', 'python', '-m', 'gits', 'desktop'], {
      cwd: projectRoot,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env },
    });

    this._ready = true;

    // --- stdout: buffer by newline, parse each line as JSON ---
    this._proc.stdout.on('data', (chunk) => {
      this._buf += chunk.toString();
      const lines = this._buf.split('\n');
      // Keep any incomplete trailing fragment in the buffer
      this._buf = lines.pop();

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const parsed = JSON.parse(trimmed);
          this.emit('message', parsed);
        } catch (err) {
          // Not valid JSON — treat as a plain log line
          console.log('[python stdout]', trimmed);
        }
      }
    });

    // --- stderr: log lines but only emit as error for actual errors ---
    this._proc.stderr.on('data', (chunk) => {
      const text = chunk.toString().trimEnd();
      const isError = /ERROR|CRITICAL|Traceback|Exception/.test(text);
      if (isError) {
        console.error('[python stderr]', text);
        this.emit('error', { type: 'stderr', text });
      } else {
        console.log('[python]', text);
      }
    });

    this._proc.on('error', (err) => {
      console.error('[bridge] failed to spawn Python process:', err.message);
      this._ready = false;
      this.emit('error', { type: 'spawn', message: err.message });
    });

    this._proc.on('exit', (code, signal) => {
      console.log(`[bridge] Python process exited — code=${code} signal=${signal}`);
      this._proc = null;
      this._ready = false;
      this.emit('exit', { code, signal });
    });
  }

  /**
   * Send a JSON-serialisable object to the Python process via stdin.
   * @param {object} obj
   */
  send(obj) {
    if (!this._proc || !this._ready) {
      console.warn('[bridge] send() called but Python process is not running');
      return false;
    }
    try {
      this._proc.stdin.write(JSON.stringify(obj) + '\n');
      return true;
    } catch (err) {
      console.error('[bridge] failed to write to Python stdin:', err.message);
      return false;
    }
  }

  /**
   * Gracefully shut down the Python process.
   */
  stop() {
    if (!this._proc) return;
    console.log('[bridge] stopping Python process');
    try {
      this._proc.stdin.end();
      this._proc.kill('SIGTERM');
    } catch (err) {
      console.error('[bridge] error stopping process:', err.message);
    }
  }

  get isRunning() {
    return this._proc !== null && this._ready;
  }
}

// Export a singleton so main.js and any other module share the same instance.
module.exports = new PythonBridge();
