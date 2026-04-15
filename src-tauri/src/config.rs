use anyhow::Result;
use serde::{Deserialize, Serialize};
use tauri::AppHandle;
use tauri_plugin_store::StoreExt;

const STORE_FILE: &str = "settings.json";

/// All persisted application settings.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Settings {
    /// Key of the currently active model (e.g. "large-v3-turbo-q5").
    pub active_model: Option<String>,

    /// Hotkey string for push-to-talk record (e.g. "alt+space").
    /// Parsed by tauri-plugin-global-shortcut.
    /// Format: modifier(s) + key, e.g. "ctrl+shift+KeyD", "alt+F1".
    pub record_hotkey: String,

    /// Hotkey string for cycling models (e.g. "ctrl+shift+KeyM").
    pub switch_hotkey: String,

    /// Per-model language override.  Key = model_id, value = language code
    /// ("auto", "en", "da", etc.).
    pub model_languages: std::collections::HashMap<String, String>,

    /// Milliseconds to wait before injecting text after releasing the record key.
    pub injection_delay_ms: u64,

    /// Whether to launch Scribr at login.
    pub launch_at_login: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            active_model: None,
            // ctrl+shift+D — unlikely to conflict with system shortcuts on macOS.
            // Users can change this in Settings. alt+space is reserved by input
            // method switching on many systems.
            record_hotkey: "ctrl+shift+KeyD".to_string(),
            // ctrl+shift+M — cycles through downloaded models
            switch_hotkey: "ctrl+shift+KeyM".to_string(),
            model_languages: std::collections::HashMap::new(),
            injection_delay_ms: 150,
            launch_at_login: false,
        }
    }
}

pub fn load(app: &AppHandle) -> Result<Settings> {
    let store = app.store(STORE_FILE)?;

    let settings: Settings = match store.get("settings") {
        Some(v) => serde_json::from_value(v).unwrap_or_default(),
        None => Settings::default(),
    };

    Ok(settings)
}

pub fn save(app: &AppHandle, settings: &Settings) -> Result<()> {
    let store = app.store(STORE_FILE)?;
    store.set("settings", serde_json::to_value(settings)?);
    store.save()?;
    Ok(())
}
