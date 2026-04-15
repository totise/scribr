mod audio;
mod config;
mod injector;
mod models;
mod transcriber;
mod tray;

use std::sync::Arc;
use tauri::Manager;
use tauri_plugin_store::StoreExt;

pub use config::Settings;
pub use models::{ModelCatalogue, ModelState};
pub use transcriber::TranscriberState;

use audio::AudioManager;
use injector::Injector;
use transcriber::Transcriber;

// ── Tauri commands ────────────────────────────────────────────────────────────

#[tauri::command]
async fn get_settings(app: tauri::AppHandle) -> Result<Settings, String> {
    config::load(&app).map_err(|e| e.to_string())
}

#[tauri::command]
async fn save_settings(app: tauri::AppHandle, settings: Settings) -> Result<(), String> {
    config::save(&app, &settings).map_err(|e| e.to_string())?;
    crate::tray::apply_hotkeys(&app, &settings).map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
async fn get_models(app: tauri::AppHandle) -> Result<Vec<models::ModelEntry>, String> {
    let catalogue = app.state::<Arc<ModelCatalogue>>();
    Ok(catalogue.entries().await)
}

#[tauri::command]
async fn download_model(app: tauri::AppHandle, model_id: String) -> Result<(), String> {
    models::download(&app, &model_id)
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn cancel_download(app: tauri::AppHandle, model_id: String) -> Result<(), String> {
    let catalogue = app.state::<Arc<ModelCatalogue>>();
    catalogue.cancel_download(&model_id).await;
    Ok(())
}

#[tauri::command]
async fn delete_model(app: tauri::AppHandle, model_id: String) -> Result<(), String> {
    models::delete(&app, &model_id)
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn switch_model(app: tauri::AppHandle, model_id: String) -> Result<(), String> {
    let transcriber = app.state::<Arc<Transcriber>>();
    transcriber
        .load_model(&app, &model_id)
        .await
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn cycle_model(app: tauri::AppHandle) -> Result<(), String> {
    models::cycle_active(&app).await.map_err(|e| e.to_string())
}

#[tauri::command]
fn check_accessibility() -> bool {
    injector::is_accessibility_trusted()
}

#[tauri::command]
async fn open_accessibility_settings(app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    app.opener()
        .open_url(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            None::<&str>,
        )
        .map_err(|e| e.to_string())
}

// ── App entry point ───────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    tauri::Builder::default()
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(|app| {
            // Initialise persistent store
            let _store = app.store("settings.json")?;

            // Load settings (fall back to defaults on first run)
            let settings = config::load(app.handle()).unwrap_or_default();

            // Register managed state — all wrapped in Arc so .state() is consistent
            app.manage(ModelCatalogue::new()); // Arc<ModelCatalogue>
            app.manage(Transcriber::new()); // Arc<Transcriber>
            app.manage(AudioManager::new()); // Arc<AudioManager>
            app.manage(Injector::new()); // Arc<Injector>

            // Seed model states from disk and restore language settings
            {
                let handle = app.handle().clone();
                let settings_clone = settings.clone();
                tauri::async_runtime::spawn(async move {
                    let catalogue = handle.state::<Arc<ModelCatalogue>>();
                    catalogue.init_from_disk(&handle, &settings_clone).await;
                    models::emit_models_changed(&handle).await;
                });
            }

            // Build tray icon + menu
            tray::setup(app.handle(), &settings)?;

            // Register hotkeys
            tray::apply_hotkeys(app.handle(), &settings)?;

            // Restore previously active model
            if let Some(active_id) = settings.active_model.clone() {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let transcriber = handle.state::<Arc<Transcriber>>();
                    if let Err(e) = transcriber.load_model(&handle, &active_id).await {
                        log::error!("Failed to restore active model '{active_id}': {e}");
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_settings,
            save_settings,
            get_models,
            download_model,
            cancel_download,
            delete_model,
            switch_model,
            cycle_model,
            check_accessibility,
            open_accessibility_settings,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Scribr");
}
