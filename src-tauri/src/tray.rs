use anyhow::Result;
use std::sync::Arc;
use tauri::{
    image::Image,
    menu::{Menu, MenuItem, PredefinedMenuItem, Submenu},
    tray::TrayIconBuilder,
    AppHandle, Manager,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

use crate::config::Settings;
use crate::models::{ModelCatalogue, ModelState};
use crate::transcriber::Transcriber;

const TRAY_ID: &str = "main";

// Embedded icon PNG (32x32 RGBA) — guaranteed to exist at compile time.
// Using include_bytes! avoids any runtime file-not-found panic and also
// avoids the crash from calling .unwrap() on default_window_icon() which
// returns None for LSUIElement (no-Dock) apps on some macOS versions.
const ICON_BYTES: &[u8] = include_bytes!("../../icons/32x32.png");

fn tray_icon() -> Image<'static> {
    Image::from_bytes(ICON_BYTES)
        .expect("icons/32x32.png embedded in binary is corrupt — regenerate it")
}

/// Called once at app startup to create the tray icon.
pub fn setup(app: &AppHandle, settings: &Settings) -> Result<()> {
    let menu = build_menu(app, settings)?;

    TrayIconBuilder::with_id(TRAY_ID)
        .icon(tray_icon())
        .icon_as_template(true)
        .menu(&menu)
        .show_menu_on_left_click(true)
        .tooltip("Scribr")
        .title("Scribr")
        .on_menu_event(handle_menu_event)
        .on_tray_icon_event(|_tray, _event| {})
        .build(app)?;

    Ok(())
}

fn build_menu(app: &AppHandle, _settings: &Settings) -> Result<Menu<tauri::Wry>> {
    let status = MenuItem::with_id(app, "status", "Idle", false, None::<&str>)?;

    let switch_submenu = Submenu::with_id_and_items(
        app,
        "switch-model",
        "Switch Model",
        true,
        &[],
    )?;

    let separator1 = PredefinedMenuItem::separator(app)?;
    let settings_item = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let separator2 = PredefinedMenuItem::separator(app)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit Scribr", true, None::<&str>)?;

    let menu = Menu::with_items(
        app,
        &[
            &status,
            &switch_submenu,
            &separator1,
            &settings_item,
            &separator2,
            &quit_item,
        ],
    )?;

    Ok(menu)
}

fn handle_menu_event(app: &AppHandle, event: tauri::menu::MenuEvent) {
    match event.id().as_ref() {
        "quit" => {
            app.exit(0);
        }
        "settings" => {
            show_settings_window(app);
        }
        id if id.starts_with("model:") => {
            let model_id = id.strip_prefix("model:").unwrap_or("").to_string();
            let handle = app.clone();
            tauri::async_runtime::spawn(async move {
                let transcriber = handle.state::<Arc<Transcriber>>();
                if let Err(e) = transcriber.load_model(&handle, &model_id).await {
                    log::error!("Switch model failed: {e}");
                }
            });
        }
        _ => {}
    }
}

fn show_settings_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("settings") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Register (or re-register) both configurable hotkeys.
/// Individual registration failures are logged but do NOT propagate —
/// a bad/conflicting hotkey string must not crash the app.
pub fn apply_hotkeys(app: &AppHandle, settings: &Settings) -> Result<()> {
    // Unregister all existing shortcuts first (ignore errors — nothing registered yet on first call)
    let _ = app.global_shortcut().unregister_all();

    let record_hotkey = settings.record_hotkey.clone();
    let switch_hotkey = settings.switch_hotkey.clone();

    // Record hotkey: press = start recording, release = stop + transcribe
    match app.global_shortcut().on_shortcut(
        record_hotkey.as_str(),
        move |app, _shortcut, event| {
            let handle = app.clone();
            match event.state() {
                ShortcutState::Pressed => {
                    tauri::async_runtime::spawn(async move {
                        let audio = handle.state::<Arc<crate::audio::AudioManager>>();
                        if let Err(e) = audio.start_recording(&handle).await {
                            log::error!("start_recording: {e}");
                        }
                    });
                }
                ShortcutState::Released => {
                    tauri::async_runtime::spawn(async move {
                        let audio = handle.state::<Arc<crate::audio::AudioManager>>();
                        if let Some(pcm) = audio.stop_recording(&handle).await {
                            let transcriber = handle.state::<Arc<Transcriber>>();
                            if let Err(e) = transcriber.transcribe(&handle, pcm).await {
                                log::error!("transcribe: {e}");
                            }
                        }
                    });
                }
            }
        },
    ) {
        Ok(_) => log::info!("Record hotkey registered: {record_hotkey}"),
        Err(e) => log::warn!("Could not register record hotkey '{record_hotkey}': {e}"),
    }

    // Model-switch hotkey: press only
    match app.global_shortcut().on_shortcut(
        switch_hotkey.as_str(),
        move |app, _shortcut, event| {
            if event.state() == ShortcutState::Pressed {
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    if let Err(e) = crate::models::cycle_active(&handle).await {
                        log::error!("cycle_model: {e}");
                    }
                });
            }
        },
    ) {
        Ok(_) => log::info!("Switch hotkey registered: {switch_hotkey}"),
        Err(e) => log::warn!("Could not register switch hotkey '{switch_hotkey}': {e}"),
    }

    Ok(())
}

/// Update tray title and Switch Model submenu to reflect current state.
pub async fn update_tray(app: &AppHandle) {
    let transcriber = app.state::<Arc<Transcriber>>();
    let catalogue = app.state::<Arc<ModelCatalogue>>();

    let active_id = transcriber.active_model_id().await;
    let entries = catalogue.entries().await;

    // Build tray title
    let title = if let Some(ref id) = active_id {
        model_abbrev(id).to_string()
    } else {
        "—".to_string()
    };

    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        let _ = tray.set_title(Some(&title));
        let _ = tray.set_tooltip(Some(&format!("Scribr — {title}")));

        // Rebuild menu with updated Switch Model submenu
        if let Ok(menu) = build_dynamic_menu(app, &entries, &active_id) {
            let _ = tray.set_menu(Some(menu));
        }
    }
}

fn build_dynamic_menu(
    app: &AppHandle,
    entries: &[crate::models::ModelEntry],
    active_id: &Option<String>,
) -> Result<Menu<tauri::Wry>> {
    let state_label = active_id
        .as_deref()
        .map(|id| format!("Model: {}", model_abbrev(id)))
        .unwrap_or_else(|| "No model loaded".to_string());

    let status = MenuItem::with_id(app, "status", &state_label, false, None::<&str>)?;

    // Build Switch Model submenu entries
    let ready_entries: Vec<_> = entries
        .iter()
        .filter(|e| {
            matches!(
                e.state,
                ModelState::Ready { .. } | ModelState::Active { .. }
            )
        })
        .collect();

    let switch_items: Vec<Box<dyn tauri::menu::IsMenuItem<tauri::Wry>>> = ready_entries
        .iter()
        .map(|e| {
            let label = if Some(&e.info.id) == active_id.as_ref() {
                format!("• {}", e.info.display_name)
            } else {
                format!("  {}", e.info.display_name)
            };
            let id = format!("model:{}", e.info.id);
            let item: Box<dyn tauri::menu::IsMenuItem<tauri::Wry>> =
                Box::new(MenuItem::with_id(app, &id, &label, true, None::<&str>).unwrap());
            item
        })
        .collect();

    let switch_submenu = if switch_items.is_empty() {
        let placeholder =
            MenuItem::with_id(app, "no-models", "No models downloaded", false, None::<&str>)?;
        Submenu::with_id_and_items(
            app,
            "switch-model",
            "Switch Model",
            true,
            &[&placeholder],
        )?
    } else {
        let refs: Vec<&dyn tauri::menu::IsMenuItem<tauri::Wry>> =
            switch_items.iter().map(|i| i.as_ref()).collect();
        Submenu::with_id_and_items(app, "switch-model", "Switch Model", true, &refs)?
    };

    let sep1 = PredefinedMenuItem::separator(app)?;
    let settings_item = MenuItem::with_id(app, "settings", "Settings…", true, None::<&str>)?;
    let sep2 = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Scribr", true, None::<&str>)?;

    Ok(Menu::with_items(
        app,
        &[&status, &switch_submenu, &sep1, &settings_item, &sep2, &quit],
    )?)
}

fn model_abbrev(id: &str) -> &str {
    match id {
        "tiny" => "TI",
        "base" => "BA",
        "small" => "SM",
        "medium" => "MD",
        "large-v3-turbo" => "LG",
        "large-v3-turbo-q5" => "LQ",
        _ => "??",
    }
}
