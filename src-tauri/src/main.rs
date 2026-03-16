#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    collections::HashMap,
    io::{Read, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{Arc, Mutex},
};

use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64};
use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use tauri::{AppHandle, Emitter, State};

// ── Python IPC state ─────────────────────────────────────────────────────────

struct PythonState {
    stdin: Arc<Mutex<Option<ChildStdin>>>,
}

// ── PTY state ────────────────────────────────────────────────────────────────

struct PtyHandle {
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
}

struct PtyState {
    ptys: Arc<Mutex<HashMap<String, PtyHandle>>>,
}

// ── Python command ────────────────────────────────────────────────────────────

#[tauri::command]
fn python_cmd(
    state: State<PythonState>,
    cmd: String,
    payload: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
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

// ── PTY commands ──────────────────────────────────────────────────────────────

#[tauri::command]
fn open_pty(
    pty_state: State<PtyState>,
    app: AppHandle,
    channel_id: String,
    tmux_session: String,
    window_id: String,
    rows: u16,
    cols: u16,
) -> Result<(), String> {
    // Close existing PTY for this channel if any
    {
        let mut ptys = pty_state.ptys.lock().unwrap();
        ptys.remove(&channel_id);
    }

    let pty_system = native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| e.to_string())?;

    // Build: tmux attach-session -t <session>:<window>
    let target = format!("{}:{}", tmux_session, window_id);
    let mut cmd = CommandBuilder::new("tmux");
    cmd.args(&["attach-session", "-t", &target]);

    let _child = pair.slave.spawn_command(cmd).map_err(|e| e.to_string())?;
    drop(pair.slave);

    let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
    let mut reader = pair
        .master
        .try_clone_reader()
        .map_err(|e| e.to_string())?;

    // Store handle
    {
        let mut ptys = pty_state.ptys.lock().unwrap();
        ptys.insert(
            channel_id.clone(),
            PtyHandle {
                master: pair.master,
                writer,
            },
        );
    }

    // Spawn reader thread: PTY output → Tauri event
    let cid = channel_id.clone();
    std::thread::spawn(move || {
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(n) => {
                    let encoded = BASE64.encode(&buf[..n]);
                    let _ = app.emit(
                        "pty-output",
                        serde_json::json!({ "channel_id": cid, "data": encoded }),
                    );
                }
            }
        }
        let _ = app.emit("pty-output", serde_json::json!({ "channel_id": cid, "data": null, "closed": true }));
    });

    Ok(())
}

#[tauri::command]
fn pty_input(
    pty_state: State<PtyState>,
    channel_id: String,
    data: String, // base64-encoded bytes
) -> Result<(), String> {
    let bytes = BASE64.decode(&data).map_err(|e| e.to_string())?;
    let mut ptys = pty_state.ptys.lock().unwrap();
    if let Some(handle) = ptys.get_mut(&channel_id) {
        handle.writer.write_all(&bytes).map_err(|e| e.to_string())?;
        handle.writer.flush().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn resize_pty(
    pty_state: State<PtyState>,
    channel_id: String,
    rows: u16,
    cols: u16,
) -> Result<(), String> {
    let mut ptys = pty_state.ptys.lock().unwrap();
    if let Some(handle) = ptys.get_mut(&channel_id) {
        handle
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

#[tauri::command]
fn close_pty(pty_state: State<PtyState>, channel_id: String) -> Result<(), String> {
    let mut ptys = pty_state.ptys.lock().unwrap();
    ptys.remove(&channel_id);
    Ok(())
}

// ── Python subprocess spawning ────────────────────────────────────────────────

fn find_uv() -> String {
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

fn spawn_python(app: AppHandle, stdin_arc: Arc<Mutex<Option<ChildStdin>>>) {
    let project_root = if std::env::var("TAURI_DEV").is_ok() {
        std::env::current_dir().unwrap()
    } else {
        std::env::current_exe()
            .unwrap()
            .parent().unwrap()
            .parent().unwrap()
            .parent().unwrap()
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
        .expect("Failed to spawn Python backend");

    {
        let mut guard = stdin_arc.lock().unwrap();
        *guard = child.stdin.take();
    }

    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();

    let app_clone = app.clone();
    std::thread::spawn(move || {
        use std::io::BufRead;
        let reader = std::io::BufReader::new(stdout);
        for line in reader.lines() {
            match line {
                Ok(text) => {
                    let trimmed = text.trim().to_string();
                    if trimmed.is_empty() { continue; }
                    match serde_json::from_str::<serde_json::Value>(&trimmed) {
                        Ok(json) => {
                            // Debug: append to log file so we can verify events without screenshots
                            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open("/tmp/ghost-ipc.log") {
                                use std::io::Write as _;
                                let _ = writeln!(f, "EMIT python-event: {}", &trimmed[..trimmed.len().min(200)]);
                            }
                            let _ = app_clone.emit("python-event", json);
                        }
                        Err(_) => { eprintln!("[python stdout] {}", trimmed); }
                    }
                }
                Err(e) => { eprintln!("[bridge] stdout read error: {}", e); break; }
            }
        }
        let exit_code = child.wait().map(|s| s.code()).unwrap_or(None);
        let _ = app_clone.emit("python-event", serde_json::json!({ "event": "bridge-exit", "code": exit_code }));
    });

    std::thread::spawn(move || {
        use std::io::BufRead;
        let reader = std::io::BufReader::new(stderr);
        for line in reader.lines().flatten() {
            eprintln!("[python] {}", line);
        }
    });
}

// ── Debug log — JS can write diagnostics to /tmp/ghost-js.log ────────────

#[tauri::command]
fn debug_log(msg: String) {
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open("/tmp/ghost-js.log") {
        use std::io::Write as _;
        let _ = writeln!(f, "{}", msg);
    }
}

// ── Screenshot — save base64 PNG from html2canvas in the WebView ──────────

#[tauri::command]
fn take_screenshot(data: String, path: Option<String>) -> Result<String, String> {
    let out_path = path.unwrap_or_else(|| {
        format!("/tmp/ghost-{}.png",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0))
    });
    // Strip "data:image/png;base64," prefix if present
    let b64 = if let Some(pos) = data.find(',') { &data[pos+1..] } else { &data };
    let bytes = base64::engine::general_purpose::STANDARD.decode(b64)
        .map_err(|e| e.to_string())?;
    std::fs::write(&out_path, bytes).map_err(|e| e.to_string())?;
    Ok(out_path)
}

// ── App entry ────────────────────────────────────────────────────────────────

fn main() {
    let stdin_arc: Arc<Mutex<Option<ChildStdin>>> = Arc::new(Mutex::new(None));
    let stdin_arc_for_setup = stdin_arc.clone();

    tauri::Builder::default()
        .manage(PythonState { stdin: stdin_arc })
        .manage(PtyState {
            ptys: Arc::new(Mutex::new(HashMap::new())),
        })
        .setup(move |app| {
            let handle = app.handle().clone();
            spawn_python(handle, stdin_arc_for_setup);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            python_cmd,
            open_pty,
            pty_input,
            resize_pty,
            close_pty,
            take_screenshot,
            debug_log,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}
