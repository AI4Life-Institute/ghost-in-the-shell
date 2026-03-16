// Ghost desktop app — Tauri v2 library entry point.

use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{Arc, Mutex},
};

use base64::engine::general_purpose::STANDARD as B64;
use portable_pty::{native_pty_system, PtySize};
use tauri::{AppHandle, Emitter};

// ── Python bridge state ────────────────────────────────────────────────────

pub struct PythonState {
    pub stdin: Arc<Mutex<Option<ChildStdin>>>,
}

// ── PTY store ──────────────────────────────────────────────────────────────

struct PtyEntry {
    writer: Box<dyn Write + Send>,
    master: Box<dyn portable_pty::MasterPty + Send>,
    _child: Box<dyn portable_pty::Child + Send + Sync>,
}

pub struct PtyStore {
    ptys: Mutex<HashMap<String, PtyEntry>>,
}

impl PtyStore {
    pub fn new() -> Self {
        Self {
            ptys: Mutex::new(HashMap::new()),
        }
    }
}

// ── Tauri commands ─────────────────────────────────────────────────────────

mod commands {
    use super::{PtyEntry, PtySize, PtyStore, PythonState, B64};
    use base64::Engine as _;
    use portable_pty::CommandBuilder;
    use std::io::{Read, Write};
    use tauri::{AppHandle, Emitter, State};

    #[tauri::command]
    pub fn python_cmd(
        state: State<PythonState>,
        cmd: String,
        payload: Option<serde_json::Value>,
    ) -> Result<serde_json::Value, String> {
        use std::io::Write as _;
        let msg = serde_json::json!({
            "cmd": cmd,
            "payload": payload.unwrap_or(serde_json::Value::Object(Default::default()))
        });
        let line = serde_json::to_string(&msg).map_err(|e| e.to_string())?;
        let mut guard = state.stdin.lock().map_err(|e| e.to_string())?;
        if let Some(ref mut stdin) = *guard {
            writeln!(stdin, "{}", line).map_err(|e| e.to_string())?;
            Ok(serde_json::json!({ "ok": true }))
        } else {
            Err("Python process not running".to_string())
        }
    }

    /// Open a PTY attached to an existing tmux session:window.
    #[tauri::command]
    pub fn open_pty(
        app: AppHandle,
        state: State<PtyStore>,
        channel_id: String,
        tmux_session: String,
        window_id: String,
        rows: u16,
        cols: u16,
    ) -> Result<(), String> {
        {
            let ptys = state.ptys.lock().map_err(|e| e.to_string())?;
            if ptys.contains_key(&channel_id) {
                return Ok(()); // already open
            }
        }

        let pty_system = super::native_pty_system();
        let pair = pty_system
            .openpty(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| e.to_string())?;

        let target = format!("{}:{}", tmux_session, window_id);
        let mut cmd = CommandBuilder::new("tmux");
        cmd.args(&["attach-session", "-t", &target]);

        let child = pair
            .slave
            .spawn_command(cmd)
            .map_err(|e| e.to_string())?;

        let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
        let mut reader = pair
            .master
            .try_clone_reader()
            .map_err(|e| e.to_string())?;

        // Background thread: stream PTY output → frontend event
        let app_clone = app.clone();
        let cid = channel_id.clone();
        std::thread::spawn(move || {
            let mut buf = [0u8; 4096];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) | Err(_) => {
                        let _ = app_clone.emit(
                            "pty-output",
                            serde_json::json!({ "channel_id": cid, "closed": true }),
                        );
                        break;
                    }
                    Ok(n) => {
                        let encoded = B64.encode(&buf[..n]);
                        let _ = app_clone.emit(
                            "pty-output",
                            serde_json::json!({ "channel_id": cid, "data": encoded }),
                        );
                    }
                }
            }
        });

        let mut ptys = state.ptys.lock().map_err(|e| e.to_string())?;
        ptys.insert(
            channel_id,
            PtyEntry {
                writer,
                master: pair.master,
                _child: child,
            },
        );

        Ok(())
    }

    /// Send base64-encoded input to a PTY.
    #[tauri::command]
    pub fn pty_input(
        state: State<PtyStore>,
        channel_id: String,
        data: String,
    ) -> Result<(), String> {
        let decoded = B64.decode(&data).map_err(|e| e.to_string())?;
        let mut ptys = state.ptys.lock().map_err(|e| e.to_string())?;
        if let Some(entry) = ptys.get_mut(&channel_id) {
            entry.writer.write_all(&decoded).map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    /// Resize a PTY.
    #[tauri::command]
    pub fn resize_pty(
        state: State<PtyStore>,
        channel_id: String,
        rows: u16,
        cols: u16,
    ) -> Result<(), String> {
        let ptys = state.ptys.lock().map_err(|e| e.to_string())?;
        if let Some(entry) = ptys.get(&channel_id) {
            entry
                .master
                .resize(PtySize {
                    rows,
                    cols,
                    pixel_width: 0,
                    pixel_height: 0,
                })
                .map_err(|e| e.to_string())?;
        }
        Ok(())
    }

    /// Close a PTY (drops child + writer + master).
    #[tauri::command]
    pub fn close_pty(state: State<PtyStore>, channel_id: String) -> Result<(), String> {
        let mut ptys = state.ptys.lock().map_err(|e| e.to_string())?;
        ptys.remove(&channel_id);
        Ok(())
    }
}

// ── Python process helpers ─────────────────────────────────────────────────

pub fn find_uv() -> String {
    for candidate in &[
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
        "/Users/weiliu/.cargo/bin/uv",
        "uv",
    ] {
        if std::path::Path::new(candidate).exists() || *candidate == "uv" {
            return candidate.to_string();
        }
    }
    "uv".to_string()
}

pub fn spawn_python(app: AppHandle, stdin_arc: Arc<Mutex<Option<ChildStdin>>>) {
    let project_root = if std::env::var("TAURI_DEV").is_ok() {
        std::env::current_dir().unwrap()
    } else {
        std::env::current_exe()
            .unwrap()
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .to_path_buf()
    };

    let uv_path = find_uv();

    let mut child: Child = Command::new(&uv_path)
        .args(["run", "python", "-m", "gits", "desktop"])
        .current_dir(&project_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("Failed to spawn Python backend — is `uv` in PATH?");

    {
        let mut guard = stdin_arc.lock().unwrap();
        *guard = child.stdin.take();
    }

    let stdout = child.stdout.take().expect("Python stdout not captured");
    let stderr = child.stderr.take().expect("Python stderr not captured");

    let app_clone = app.clone();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(text) => {
                    let trimmed = text.trim().to_string();
                    if trimmed.is_empty() {
                        continue;
                    }
                    match serde_json::from_str::<serde_json::Value>(&trimmed) {
                        Ok(json) => {
                            let _ = app_clone.emit("python-event", json);
                        }
                        Err(_) => {
                            eprintln!("[python stdout] {}", trimmed);
                        }
                    }
                }
                Err(e) => {
                    eprintln!("[bridge] stdout read error: {}", e);
                    break;
                }
            }
        }
        let exit_code = child.wait().map(|s| s.code()).unwrap_or(None);
        let _ = app_clone.emit(
            "python-event",
            serde_json::json!({ "event": "bridge-exit", "code": exit_code }),
        );
    });

    std::thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().flatten() {
            eprintln!("[python] {}", line);
        }
    });
}

// ── App entry point ────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let stdin_arc: Arc<Mutex<Option<ChildStdin>>> = Arc::new(Mutex::new(None));
    let stdin_arc_for_setup = stdin_arc.clone();

    tauri::Builder::default()
        .manage(PythonState { stdin: stdin_arc })
        .manage(PtyStore::new())
        .setup(move |app| {
            let handle = app.handle().clone();
            spawn_python(handle, stdin_arc_for_setup);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::python_cmd,
            commands::open_pty,
            commands::pty_input,
            commands::resize_pty,
            commands::close_pty,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}
