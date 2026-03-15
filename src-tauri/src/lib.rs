// Ghost desktop app — Tauri v2 library entry point.
// This lib target (staticlib + cdylib) is required for Tauri iOS/Android builds.
// Desktop builds use src/main.rs directly.

use std::{
    io::{BufRead, BufReader},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{Arc, Mutex},
};

use tauri::{AppHandle, Emitter};

pub struct PythonState {
    pub stdin: Arc<Mutex<Option<ChildStdin>>>,
}

mod commands {
    use super::PythonState;
    use std::io::Write;
    use tauri::State;

    #[tauri::command]
    pub fn python_cmd(
        state: State<PythonState>,
        cmd: String,
        payload: Option<serde_json::Value>,
    ) -> Result<serde_json::Value, String> {
        let msg = serde_json::json!({
            "cmd": cmd,
            "payload": payload.unwrap_or(serde_json::Value::Object(Default::default()))
        });
        let line = serde_json::to_string(&msg).map_err(|e: serde_json::Error| e.to_string())?;
        let mut guard = state.stdin.lock().map_err(|e| e.to_string())?;
        if let Some(ref mut stdin) = *guard {
            writeln!(stdin, "{}", line).map_err(|e: std::io::Error| e.to_string())?;
            Ok(serde_json::json!({ "ok": true }))
        } else {
            Err("Python process not running".to_string())
        }
    }
}

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let stdin_arc: Arc<Mutex<Option<ChildStdin>>> = Arc::new(Mutex::new(None));
    let stdin_arc_for_setup = stdin_arc.clone();

    tauri::Builder::default()
        .manage(PythonState { stdin: stdin_arc })
        .setup(move |app| {
            let handle = app.handle().clone();
            spawn_python(handle, stdin_arc_for_setup);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![commands::python_cmd])
        .run(tauri::generate_context!())
        .expect("error while running Tauri application");
}
