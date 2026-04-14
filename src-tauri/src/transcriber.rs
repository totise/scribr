use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tauri::{AppHandle, Emitter, Manager};
use tokio::sync::{oneshot, Mutex};

use crate::models::{ModelCatalogue, ModelState};

// ── State events emitted to frontend/tray ────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", tag = "type")]
pub enum TranscriberState {
    Idle,
    Loading { model_id: String },
    Ready { model_id: String },
    Transcribing,
    Error { message: String },
}

// ── Result event ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TranscriptionResult {
    pub text: String,
    pub model_id: String,
}

// ── Inner worker ─────────────────────────────────────────────────────────────

struct Inner {
    context: Option<whisper_rs::WhisperContext>,
    active_model_id: Option<String>,
}

impl Inner {
    fn new() -> Self {
        Self {
            context: None,
            active_model_id: None,
        }
    }
}

// ── Public Transcriber handle ────────────────────────────────────────────────

pub struct Transcriber {
    inner: Arc<Mutex<Inner>>,
}

impl Transcriber {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            inner: Arc::new(Mutex::new(Inner::new())),
        })
    }

    /// Load (or switch to) a model.  Unloads any currently loaded model first.
    pub async fn load_model(&self, app: &AppHandle, model_id: &str) -> Result<()> {
        let catalogue = app.state::<Arc<ModelCatalogue>>();

        // Get the path for this model
        let state = catalogue.get_state(model_id).await;
        let path = match state {
            ModelState::Ready { path, .. } | ModelState::Active { path, .. } => path,
            _ => {
                return Err(anyhow::anyhow!(
                    "Model '{model_id}' is not downloaded"
                ));
            }
        };

        // Emit Loading state
        emit_transcriber_state(app, TranscriberState::Loading {
            model_id: model_id.to_string(),
        });

        // Mark any previously active model as Ready
        {
            let inner = self.inner.lock().await;
            if let Some(prev_id) = &inner.active_model_id {
                if prev_id != model_id {
                    let prev_state = catalogue.get_state(prev_id).await;
                    if let ModelState::Active { path: prev_path, size_on_disk } = prev_state {
                        catalogue
                            .set_state(prev_id, ModelState::Ready {
                                path: prev_path,
                                size_on_disk,
                            })
                            .await;
                    }
                }
            }
        }

        // Load the new model on a blocking thread (whisper.cpp blocks).
        // Capture the tokio Handle here (in async context) so the spawned OS
        // thread can use block_on to drive async locks.
        let rt = tokio::runtime::Handle::current();
        let model_id_owned = model_id.to_string();
        let path_owned = path.clone();
        let inner_arc = self.inner.clone();

        let (tx, rx) = oneshot::channel::<Result<()>>();

        std::thread::spawn(move || {
            let result = (|| {
                let params = whisper_rs::WhisperContextParameters::default();
                let ctx = whisper_rs::WhisperContext::new_with_params(&path_owned, params)
                    .map_err(|e| anyhow::anyhow!("Failed to load model: {:?}", e))?;

                let mut inner = rt.block_on(inner_arc.lock());
                inner.context = Some(ctx);
                inner.active_model_id = Some(model_id_owned.clone());

                Ok(())
            })();

            let _ = tx.send(result);
        });

        match rx.await? {
            Ok(()) => {
                // Update catalogue state
                let info = catalogue.info(model_id).cloned();
                if let Some(info) = info {
                    let path_obj = ModelCatalogue::model_path(app, &info.filename);
                    let size = std::fs::metadata(&path_obj).map(|m| m.len()).unwrap_or(0);
                    catalogue
                        .set_state(
                            model_id,
                            ModelState::Active {
                                path,
                                size_on_disk: size,
                            },
                        )
                        .await;
                }

                // Persist active model to settings
                let mut settings = crate::config::load(app).unwrap_or_default();
                settings.active_model = Some(model_id.to_string());
                let _ = crate::config::save(app, &settings);

                emit_transcriber_state(app, TranscriberState::Ready {
                    model_id: model_id.to_string(),
                });
                crate::models::emit_models_changed(app).await;
                crate::tray::update_tray(app).await;
                Ok(())
            }
            Err(e) => {
                emit_transcriber_state(
                    app,
                    TranscriberState::Error { message: e.to_string() },
                );
                Err(e)
            }
        }
    }

    /// Transcribe a 16kHz mono f32 PCM buffer.
    /// Emits `transcription-result` and injects text.
    pub async fn transcribe(&self, app: &AppHandle, pcm: Vec<f32>) -> Result<()> {
        emit_transcriber_state(app, TranscriberState::Transcribing);

        let catalogue = app.state::<Arc<ModelCatalogue>>();
        let inner_arc = self.inner.clone();

        let (tx, rx) = oneshot::channel::<Result<String>>();

        // Determine language for the active model (before leaving async context)
        let active_id = {
            let inner = inner_arc.lock().await;
            inner.active_model_id.clone()
        };
        let language = if let Some(ref id) = active_id {
            catalogue.get_language(id).await
        } else {
            "auto".to_string()
        };

        // Capture tokio Handle before spawning OS thread
        let rt = tokio::runtime::Handle::current();

        std::thread::spawn(move || {
            let result = (|| {
                let mut inner = rt.block_on(inner_arc.lock());

                let ctx = inner
                    .context
                    .as_mut()
                    .ok_or_else(|| anyhow::anyhow!("No model loaded"))?;

                let mut state = ctx
                    .create_state()
                    .map_err(|e| anyhow::anyhow!("State error: {:?}", e))?;

                let mut params =
                    whisper_rs::FullParams::new(whisper_rs::SamplingStrategy::Greedy {
                        best_of: 1,
                    });

                // Language
                match language.as_str() {
                    "auto" => params.set_language(None),
                    lang => params.set_language(Some(lang)),
                }

                params.set_print_special(false);
                params.set_print_progress(false);
                params.set_print_realtime(false);
                params.set_print_timestamps(false);
                params.set_single_segment(false);
                params.set_no_context(true);

                state
                    .full(params, &pcm)
                    .map_err(|e| anyhow::anyhow!("Inference error: {:?}", e))?;

                let num_segments = state
                    .full_n_segments()
                    .map_err(|e| anyhow::anyhow!("{:?}", e))?;

                let mut text = String::new();
                for i in 0..num_segments {
                    let seg = state
                        .full_get_segment_text(i)
                        .map_err(|e| anyhow::anyhow!("{:?}", e))?;
                    text.push_str(seg.trim());
                    text.push(' ');
                }

                Ok(text.trim().to_string())
            })();

            let _ = tx.send(result);
        });

        match rx.await? {
            Ok(text) => {
                let model_id = active_id.unwrap_or_default();

                if !text.is_empty() {
                    // Emit result to frontend
                    let _ = app.emit(
                        "transcription-result",
                        TranscriptionResult {
                            text: text.clone(),
                            model_id,
                        },
                    );

                    // Inject into focused window
                    let settings = crate::config::load(app).unwrap_or_default();
                    let injector = app.state::<Arc<crate::injector::Injector>>();
                    injector.type_text(&text, settings.injection_delay_ms).await;
                }

                emit_transcriber_state(
                    app,
                    TranscriberState::Ready {
                        model_id: {
                            let inner = inner_arc.lock().await;
                            inner.active_model_id.clone().unwrap_or_default()
                        },
                    },
                );
                crate::tray::update_tray(app).await;
                Ok(())
            }
            Err(e) => {
                emit_transcriber_state(
                    app,
                    TranscriberState::Error { message: e.to_string() },
                );
                Err(e)
            }
        }
    }

    pub async fn active_model_id(&self) -> Option<String> {
        self.inner.lock().await.active_model_id.clone()
    }
}

fn emit_transcriber_state(app: &AppHandle, state: TranscriberState) {
    let _ = app.emit("transcriber-state", &state);
}
