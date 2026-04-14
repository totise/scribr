use std::sync::Arc;

/// Returns true if this process has macOS Accessibility access.
/// On non-macOS platforms always returns true.
pub fn is_accessibility_trusted() -> bool {
    #[cfg(target_os = "macos")]
    {
        use std::process::Command;
        // Quick check: ask System Events for its properties. Succeeds only if
        // Accessibility is granted; fails (non-zero exit) otherwise.
        let out = Command::new("osascript")
            .args(["-e", "tell application \"System Events\" to get properties"])
            .output();
        match out {
            Ok(o) => o.status.success(),
            Err(_) => false,
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        true
    }
}

pub struct Injector {
    _private: (),
}

impl Injector {
    pub fn new() -> Arc<Self> {
        Arc::new(Self { _private: () })
    }

    /// Type `text` into the currently focused window.
    /// Waits `delay_ms` before injecting to allow focus to restore.
    pub async fn type_text(&self, text: &str, delay_ms: u64) {
        if !is_accessibility_trusted() {
            log::warn!("Accessibility not trusted — skipping text injection");
            return;
        }

        // Append a trailing space so the cursor lands after the inserted text.
        let text_owned = format!("{} ", text);
        tokio::time::sleep(tokio::time::Duration::from_millis(delay_ms)).await;

        #[cfg(target_os = "macos")]
        inject_via_clipboard(&text_owned);

        #[cfg(not(target_os = "macos"))]
        log::info!("Text injection (stub): {text_owned}");
    }
}

/// Use the clipboard + Cmd+V approach for reliable Unicode injection.
/// Direct CGKeyCode synthesis only works for ASCII; for arbitrary Unicode
/// (including Danish ÆØÅ) the clipboard method is the correct approach.
#[cfg(target_os = "macos")]
fn inject_via_clipboard(text: &str) {
    use std::process::Command;

    // Write text to clipboard via pbcopy
    let mut child = match Command::new("pbcopy").stdin(std::process::Stdio::piped()).spawn() {
        Ok(c) => c,
        Err(e) => {
            log::error!("pbcopy failed: {e}");
            return;
        }
    };

    if let Some(stdin) = child.stdin.as_mut() {
        use std::io::Write;
        let _ = stdin.write_all(text.as_bytes());
    }
    let _ = child.wait();

    // Paste via Cmd+V using AppleScript (works in any focused app)
    let _ = Command::new("osascript")
        .args([
            "-e",
            r#"tell application "System Events" to keystroke "v" using {command down}"#,
        ])
        .output();
}
