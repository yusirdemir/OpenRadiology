//! Desktop shell for OpenRadiology.
//!
//! The shell owns three things and deliberately nothing else: the window, the
//! lifetime of the Python sidecar, and the handshake between them. All
//! radiology lives in the sidecar, so this file can stay small enough to audit.
//!
//! Startup order matters. The sidecar is launched first and its handshake line
//! is read before the window exists, so the port and token can be injected as
//! an initialisation script rather than exposed as a Tauri command. The web
//! view therefore never has a code path that could ask for a token it does not
//! already have, and a page that somehow reloads cannot request a new one.

use std::sync::Mutex;

use serde::Deserialize;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// The single line the sidecar prints on stdout once it is listening.
#[derive(Debug, Deserialize)]
struct Handshake {
    port: u16,
    token: String,
    #[serde(default)]
    version: String,
}

/// Kept so the sidecar can be stopped when the last window closes.
struct Sidecar(Mutex<Option<CommandChild>>);

/// How long to wait for the sidecar to report a port before giving up.
const HANDSHAKE_TIMEOUT_SECS: u64 = 45;

fn spawn_sidecar(app: &tauri::AppHandle) -> Result<(Handshake, CommandChild), String> {
    let parent = std::process::id().to_string();

    // A packaged build carries the engine as a PyInstaller one-dir tree under
    // Resources. One-file was rejected: it re-extracts eighty megabytes on
    // every launch, which shows up as several seconds of dead time before the
    // first window. A developer checkout usually has not built it at all, so
    // fall back to the interpreter and the repository -- same entry point,
    // same behaviour, same commands.
    let bundled = app
        .path()
        .resource_dir()
        .ok()
        .map(|dir| dir.join("engine").join(if cfg!(windows) { "openrad-server.exe" } else { "openrad-server" }))
        .filter(|path| path.is_file());

    let (mut rx, child) = match bundled {
        Some(path) => app
            .shell()
            .command(path)
            .args(["--parent-pid", parent.as_str()])
            .spawn()
            .map_err(|e| format!("Could not start the bundled engine: {e}"))?,
        None => {
            let interpreter =
                std::env::var("OPENRAD_PYTHON").unwrap_or_else(|_| "python3".to_string());
            let repo = std::env::current_dir()
                .map_err(|e| e.to_string())?
                .parent()
                .ok_or("no parent directory")?
                .to_path_buf();
            app.shell()
                .command(interpreter)
                .args(["-m", "openrad.server", "--parent-pid", parent.as_str()])
                .current_dir(repo)
                .spawn()
                .map_err(|e| format!("No bundled engine and no interpreter fallback: {e}"))?
        }
    };

    // One task owns the event stream for the whole life of the process: it
    // reports the handshake once, then keeps draining stderr so the pipe never
    // fills and stalls the engine in the middle of a render.
    let (tx, ready) = std::sync::mpsc::channel::<Result<Handshake, String>>();
    tauri::async_runtime::spawn(async move {
        let mut diagnostics = String::new();
        let mut announced = false;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    let text = String::from_utf8_lossy(&line);
                    // Only the handshake is JSON carrying a port; anything else
                    // on stdout is ignored rather than assumed to be it.
                    if !announced {
                        if let Ok(handshake) = serde_json::from_str::<Handshake>(text.trim()) {
                            announced = true;
                            let _ = tx.send(Ok(handshake));
                        }
                    }
                }
                CommandEvent::Stderr(line) => {
                    let text = String::from_utf8_lossy(&line);
                    eprintln!("[engine] {}", text.trim_end());
                    if !announced {
                        diagnostics.push_str(text.trim_end());
                        diagnostics.push('\n');
                    }
                }
                CommandEvent::Terminated(status) => {
                    if !announced {
                        let _ = tx.send(Err(format!(
                            "The engine exited before it was ready (code {:?}).\n{diagnostics}",
                            status.code
                        )));
                    }
                    break;
                }
                _ => {}
            }
        }
    });

    match ready.recv_timeout(std::time::Duration::from_secs(HANDSHAKE_TIMEOUT_SECS)) {
        Ok(Ok(handshake)) => Ok((handshake, child)),
        Ok(Err(message)) => Err(message),
        Err(_) => {
            let _ = child.kill();
            Err(format!(
                "The engine did not report a port within {HANDSHAKE_TIMEOUT_SECS} seconds."
            ))
        }
    }
}

/// Hand the web view its connection details before any of its code runs.
fn injection(handshake: &Handshake) -> String {
    format!(
        "window.__OPENRAD__ = Object.freeze({{ url: 'http://127.0.0.1:{}', token: {}, version: {} }});",
        handshake.port,
        serde_json::to_string(&handshake.token).unwrap_or_else(|_| "''".into()),
        serde_json::to_string(&handshake.version).unwrap_or_else(|_| "''".into()),
    )
}

fn failure_page(message: &str) -> String {
    // Deliberately a data URL with no scripting: if the engine failed to start,
    // the application says so plainly instead of showing an empty window.
    let escaped = message
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;");
    format!(
        "<!doctype html><meta charset=utf-8><style>\
         body{{background:#08090b;color:#eceae5;font:13px -apple-system,system-ui,sans-serif;\
         display:grid;place-items:center;height:100vh;margin:0;padding:2rem;text-align:center}}\
         h1{{font-size:15px;font-weight:600;margin:0 0 .6rem}}\
         pre{{color:#868c96;font-size:11px;white-space:pre-wrap;max-width:56ch;text-align:left}}\
         </style><div><h1>Motor başlatılamadı</h1><pre>{escaped}</pre></div>"
    )
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            let mut builder = WebviewWindowBuilder::new(&handle, "main", WebviewUrl::default())
                .title("OpenRadiology")
                .inner_size(1440.0, 900.0)
                .min_inner_size(1040.0, 680.0)
                .background_color(tauri::window::Color(8, 9, 11, 255))
                .visible(false);

            match spawn_sidecar(&handle) {
                Ok((handshake, child)) => {
                    builder = builder.initialization_script(&injection(&handshake));
                    app.state::<Sidecar>().0.lock().unwrap().replace(child);
                }
                Err(message) => {
                    let url = format!(
                        "data:text/html;charset=utf-8,{}",
                        urlencode(&failure_page(&message))
                    );
                    builder = WebviewWindowBuilder::new(
                        &handle,
                        "main",
                        WebviewUrl::External(url.parse().map_err(|e| format!("{e}"))?),
                    )
                    .title("OpenRadiology")
                    .inner_size(640.0, 420.0)
                    .visible(false);
                }
            }

            let window = builder.build()?;
            window.show()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while starting OpenRadiology")
        .run(|app, event| {
            // A sidecar that outlives its window would keep several hundred
            // megabytes of decoded volume resident with nothing to show it in.
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(child) = app.state::<Sidecar>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
        });
}

/// Percent-encode everything a data URL cannot carry literally.
fn urlencode(input: &str) -> String {
    let mut out = String::with_capacity(input.len() * 2);
    for byte in input.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}
