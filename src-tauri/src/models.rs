use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::{Mutex, RwLock};

// ── Catalogue definition ─────────────────────────────────────────────────────

/// Static description of a Whisper model available for download.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelInfo {
    pub id: String,
    pub display_name: String,
    pub filename: String,
    pub url: String,
    /// Approximate size in bytes
    pub size_bytes: u64,
    pub languages: String,
    pub description: String,
}

/// Runtime state of a model entry (download + selection).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", tag = "type")]
pub enum ModelState {
    NotDownloaded,
    Downloading { progress: f32 },
    Ready { path: String, size_on_disk: u64 },
    Active { path: String, size_on_disk: u64 },
    Error { message: String },
}

/// Combined view sent to the frontend.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ModelEntry {
    #[serde(flatten)]
    pub info: ModelInfo,
    pub state: ModelState,
    pub language: String,
}

// ── Download progress event ──────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DownloadProgressEvent {
    pub model_id: String,
    pub bytes_downloaded: u64,
    pub total_bytes: u64,
    pub progress: f32,
}

// ── Catalogue ────────────────────────────────────────────────────────────────

fn build_catalogue() -> Vec<ModelInfo> {
    vec![
        ModelInfo {
            id: "tiny".into(),
            display_name: "Tiny".into(),
            filename: "ggml-tiny.bin".into(),
            url: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin".into(),
            size_bytes: 75_000_000,
            languages: "All languages (99)".into(),
            description: "Fastest model, lowest accuracy. Good for quick tests.".into(),
        },
        ModelInfo {
            id: "base".into(),
            display_name: "Base".into(),
            filename: "ggml-base.bin".into(),
            url: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin".into(),
            size_bytes: 142_000_000,
            languages: "All languages (99)".into(),
            description: "Fast with acceptable accuracy.".into(),
        },
        ModelInfo {
            id: "small".into(),
            display_name: "Small".into(),
            filename: "ggml-small.bin".into(),
            url: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin".into(),
            size_bytes: 466_000_000,
            languages: "All languages (99)".into(),
            description: "Good quality/speed tradeoff.".into(),
        },
        ModelInfo {
            id: "medium".into(),
            display_name: "Medium".into(),
            filename: "ggml-medium.bin".into(),
            url: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin".into(),
            size_bytes: 1_500_000_000,
            languages: "All languages (99)".into(),
            description: "High accuracy, slower.".into(),
        },
        ModelInfo {
            id: "large-v3-turbo".into(),
            display_name: "Large v3 Turbo".into(),
            filename: "ggml-large-v3-turbo.bin".into(),
            url: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin".into(),
            size_bytes: 1_600_000_000,
            languages: "All languages (99)".into(),
            description: "Best quality, reasonable speed. Recommended.".into(),
        },
        ModelInfo {
            id: "large-v3-turbo-q5".into(),
            display_name: "Large v3 Turbo (quantized)".into(),
            filename: "ggml-large-v3-turbo-q5_0.bin".into(),
            url: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin".into(),
            size_bytes: 547_000_000,
            languages: "All languages (99) — incl. Danish".into(),
            description: "Best quality quantized (Q5). 547 MB. Recommended for Danish.".into(),
        },
    ]
}

// ── Managed state ────────────────────────────────────────────────────────────

pub struct ModelCatalogue {
    infos: Vec<ModelInfo>,
    /// Per-model runtime state
    states: RwLock<HashMap<String, ModelState>>,
    /// Per-model language setting (persisted via config)
    languages: RwLock<HashMap<String, String>>,
    /// Cancel tokens for in-progress downloads
    cancel_tokens: Mutex<HashMap<String, tokio_util::sync::CancellationToken>>,
}

impl ModelCatalogue {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            infos: build_catalogue(),
            states: RwLock::new(HashMap::new()),
            languages: RwLock::new(HashMap::new()),
            cancel_tokens: Mutex::new(HashMap::new()),
        })
    }

    pub fn infos(&self) -> &[ModelInfo] {
        &self.infos
    }

    pub fn info(&self, id: &str) -> Option<&ModelInfo> {
        self.infos.iter().find(|m| m.id == id)
    }

    pub async fn entries(&self) -> Vec<ModelEntry> {
        let states = self.states.read().await;
        let languages = self.languages.read().await;
        self.infos
            .iter()
            .map(|info| {
                let state = states
                    .get(&info.id)
                    .cloned()
                    .unwrap_or(ModelState::NotDownloaded);
                let language = languages
                    .get(&info.id)
                    .cloned()
                    .unwrap_or_else(|| "auto".to_string());
                ModelEntry {
                    info: info.clone(),
                    state,
                    language,
                }
            })
            .collect()
    }

    pub async fn set_state(&self, id: &str, state: ModelState) {
        self.states.write().await.insert(id.to_string(), state);
    }

    pub async fn get_state(&self, id: &str) -> ModelState {
        self.states
            .read()
            .await
            .get(id)
            .cloned()
            .unwrap_or(ModelState::NotDownloaded)
    }

    pub async fn set_language(&self, id: &str, lang: String) {
        self.languages.write().await.insert(id.to_string(), lang);
    }

    pub async fn get_language(&self, id: &str) -> String {
        self.languages
            .read()
            .await
            .get(id)
            .cloned()
            .unwrap_or_else(|| "auto".to_string())
    }

    pub async fn cancel_download(&self, id: &str) {
        if let Some(token) = self.cancel_tokens.lock().await.remove(id) {
            token.cancel();
        }
    }

    /// Returns the path where a model's .bin file lives.
    pub fn model_path(app: &AppHandle, filename: &str) -> PathBuf {
        app.path()
            .app_data_dir()
            .expect("app data dir")
            .join("models")
            .join(filename)
    }

    /// Seed initial state from disk and restore per-model language settings.
    /// Called once at startup after settings are loaded.
    pub async fn init_from_disk(&self, app: &AppHandle, settings: &crate::config::Settings) {
        // Restore persisted language overrides
        {
            let mut langs = self.languages.write().await;
            for (id, lang) in &settings.model_languages {
                langs.insert(id.clone(), lang.clone());
            }
        }

        // Scan disk for already-downloaded model files
        for info in &self.infos {
            let path = Self::model_path(app, &info.filename);
            if path.exists() {
                let size = std::fs::metadata(&path)
                    .map(|m| m.len())
                    .unwrap_or(0);
                self.set_state(
                    &info.id,
                    ModelState::Ready {
                        path: path.to_string_lossy().to_string(),
                        size_on_disk: size,
                    },
                )
                .await;
            }
        }
    }
}

// Allow Tauri to manage it as a state — no Deref needed, Arc<ModelCatalogue> is the managed type

// ── Download ─────────────────────────────────────────────────────────────────

pub async fn download(app: &AppHandle, model_id: &str) -> Result<()> {
    use futures_util::StreamExt;
    use tokio_util::sync::CancellationToken;

    let catalogue = app.state::<Arc<ModelCatalogue>>();
    let info = catalogue
        .info(model_id)
        .ok_or_else(|| anyhow::anyhow!("Unknown model: {model_id}"))?
        .clone();

    // Already downloaded?
    match catalogue.get_state(model_id).await {
        ModelState::Ready { .. } | ModelState::Active { .. } => {
            return Ok(());
        }
        ModelState::Downloading { .. } => {
            return Ok(()); // already in progress
        }
        _ => {}
    }

    // Prepare destination
    let dest = ModelCatalogue::model_path(app, &info.filename);
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let token = CancellationToken::new();
    catalogue
        .cancel_tokens
        .lock()
        .await
        .insert(model_id.to_string(), token.clone());

    catalogue
        .set_state(model_id, ModelState::Downloading { progress: 0.0 })
        .await;
    emit_models_changed(app).await;

    let client = reqwest::Client::new();
    let response = client.get(&info.url).send().await?;
    let total = response.content_length().unwrap_or(info.size_bytes);

    let mut stream = response.bytes_stream();
    let mut file = tokio::fs::File::create(&dest).await?;
    let mut downloaded: u64 = 0;

    use tokio::io::AsyncWriteExt;

    loop {
        tokio::select! {
            _ = token.cancelled() => {
                drop(file);
                let _ = tokio::fs::remove_file(&dest).await;
                catalogue.set_state(model_id, ModelState::NotDownloaded).await;
                emit_models_changed(app).await;
                return Ok(());
            }
            chunk = stream.next() => {
                match chunk {
                    None => break,
                    Some(Err(e)) => {
                        let _ = tokio::fs::remove_file(&dest).await;
                        catalogue.set_state(
                            model_id,
                            ModelState::Error { message: e.to_string() },
                        ).await;
                        emit_models_changed(app).await;
                        return Err(e.into());
                    }
                    Some(Ok(bytes)) => {
                        file.write_all(&bytes).await?;
                        downloaded += bytes.len() as u64;
                        let progress = (downloaded as f32 / total as f32).min(1.0);
                        catalogue.set_state(model_id, ModelState::Downloading { progress }).await;
                        let _ = app.emit("download-progress", DownloadProgressEvent {
                            model_id: model_id.to_string(),
                            bytes_downloaded: downloaded,
                            total_bytes: total,
                            progress,
                        });
                    }
                }
            }
        }
    }

    file.flush().await?;
    drop(file);

    catalogue.cancel_tokens.lock().await.remove(model_id);

    let size = tokio::fs::metadata(&dest).await.map(|m| m.len()).unwrap_or(0);
    catalogue
        .set_state(
            model_id,
            ModelState::Ready {
                path: dest.to_string_lossy().to_string(),
                size_on_disk: size,
            },
        )
        .await;

    emit_models_changed(app).await;
    Ok(())
}

/// Delete a downloaded model file.
pub async fn delete(app: &AppHandle, model_id: &str) -> Result<()> {
    let catalogue = app.state::<Arc<ModelCatalogue>>();
    let info = catalogue
        .info(model_id)
        .ok_or_else(|| anyhow::anyhow!("Unknown model: {model_id}"))?
        .clone();

    // Cancel any ongoing download first
    catalogue.cancel_download(model_id).await;

    let path = ModelCatalogue::model_path(app, &info.filename);
    if path.exists() {
        tokio::fs::remove_file(&path).await?;
    }

    catalogue
        .set_state(model_id, ModelState::NotDownloaded)
        .await;
    emit_models_changed(app).await;
    Ok(())
}

/// Cycle to the next Ready/Active model.
pub async fn cycle_active(app: &AppHandle) -> Result<()> {
    let catalogue = app.state::<Arc<ModelCatalogue>>();
    let entries = catalogue.entries().await;

    let ready: Vec<_> = entries
        .iter()
        .filter(|e| {
            matches!(
                e.state,
                ModelState::Ready { .. } | ModelState::Active { .. }
            )
        })
        .collect();

    if ready.len() < 2 {
        return Ok(());
    }

    // Find the current active index among ready models
    let current_idx = ready
        .iter()
        .position(|e| matches!(e.state, ModelState::Active { .. }))
        .unwrap_or(0);

    let next = &ready[(current_idx + 1) % ready.len()];
    let next_id = next.info.id.clone();

    let transcriber = app.state::<Arc<crate::transcriber::Transcriber>>();
    transcriber.load_model(app, &next_id).await?;
    Ok(())
}

/// Emit a `models-changed` event to all windows so the frontend can refresh.
pub async fn emit_models_changed(app: &AppHandle) {
    let catalogue = app.state::<Arc<ModelCatalogue>>();
    let entries = catalogue.entries().await;
    let _ = app.emit("models-changed", entries);
}
