use anyhow::Result;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use rubato::{FftFixedInOut, Resampler};
use std::sync::{Arc, Mutex};
use tauri::{AppHandle, Emitter};
use tokio::sync::Mutex as AsyncMutex;

const TARGET_SAMPLE_RATE: u32 = 16_000;

// ── State event ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone, serde::Serialize)]
#[serde(rename_all = "camelCase", tag = "type")]
pub enum AudioState {
    Idle,
    Recording,
    Processing,
}

// ── Manager ───────────────────────────────────────────────────────────────────

struct Inner {
    /// Live PCM samples accumulated during recording (at 16kHz mono).
    /// Wrapped in std Mutex so the cpal audio thread can append without async.
    buffer: Arc<Mutex<Vec<f32>>>,
    /// The active cpal stream (held to keep recording alive).
    /// `cpal::Stream` is !Send on CoreAudio (it owns ObjC objects tied to the
    /// creation thread), but we only ever create, use and drop it from the
    /// single tokio thread that holds the AsyncMutex guard — we never actually
    /// send it across threads.
    stream: Option<cpal::Stream>,
    recording: bool,
}

// SAFETY: Inner is only accessed through AsyncMutex which serialises all
// access.  The cpal::Stream inside is created, played and dropped on the same
// logical "owner" — the async task that holds the lock.  We never send the
// stream value to a different OS thread.
unsafe impl Send for Inner {}

impl Inner {
    fn new() -> Self {
        Self {
            buffer: Arc::new(Mutex::new(Vec::new())),
            stream: None,
            recording: false,
        }
    }
}

pub struct AudioManager {
    inner: AsyncMutex<Inner>,
}

impl AudioManager {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            inner: AsyncMutex::new(Inner::new()),
        })
    }

    /// Start recording from the default input device.
    pub async fn start_recording(&self, app: &AppHandle) -> Result<()> {
        let mut inner = self.inner.lock().await;
        if inner.recording {
            return Ok(());
        }

        let host = cpal::default_host();
        let device = host
            .default_input_device()
            .ok_or_else(|| anyhow::anyhow!("No input device found"))?;

        let supported_config = device.default_input_config()?;
        let sample_rate = supported_config.sample_rate().0;
        let channels = supported_config.channels() as usize;

        log::info!(
            "Recording from '{}': {}Hz {}ch",
            device.name().unwrap_or_default(),
            sample_rate,
            channels
        );

        // Clear the buffer for the new recording session
        {
            let mut buf = inner.buffer.lock().expect("buffer lock");
            buf.clear();
        }

        let needs_resample = sample_rate != TARGET_SAMPLE_RATE;

        // Build resampler (uses std Mutex — safe from audio thread)
        let resampler: Option<Arc<Mutex<FftFixedInOut<f32>>>> = if needs_resample {
            let r = FftFixedInOut::<f32>::new(
                sample_rate as usize,
                TARGET_SAMPLE_RATE as usize,
                1024,
                1,
            )?;
            Some(Arc::new(Mutex::new(r)))
        } else {
            None
        };

        let buffer_ref = Arc::clone(&inner.buffer);

        let stream = device.build_input_stream(
            &supported_config.into(),
            move |data: &[f32], _: &cpal::InputCallbackInfo| {
                // Mix down to mono f32
                let mono: Vec<f32> = data
                    .chunks(channels)
                    .map(|frame| frame.iter().sum::<f32>() / channels as f32)
                    .collect();

                // Resample to 16kHz if needed
                let samples = if let Some(ref r_arc) = resampler {
                    if let Ok(mut r) = r_arc.try_lock() {
                        resample_chunk(&mut *r, &mono).unwrap_or(mono)
                    } else {
                        mono // skip chunk if lock contended
                    }
                } else {
                    mono
                };

                // Append — plain std Mutex is safe to lock from the audio thread
                if let Ok(mut buf) = buffer_ref.lock() {
                    buf.extend_from_slice(&samples);
                }
            },
            |err| log::error!("Audio stream error: {err}"),
            None,
        )?;

        stream.play()?;

        inner.stream = Some(stream);
        inner.recording = true;

        let _ = app.emit("audio-state", AudioState::Recording);
        Ok(())
    }

    /// Stop recording and return the captured 16kHz mono f32 PCM buffer.
    pub async fn stop_recording(&self, app: &AppHandle) -> Option<Vec<f32>> {
        let mut inner = self.inner.lock().await;
        if !inner.recording {
            return None;
        }

        // Drop the stream to stop recording
        inner.stream = None;
        inner.recording = false;

        let pcm: Vec<f32> = inner
            .buffer
            .lock()
            .expect("buffer lock")
            .drain(..)
            .collect();

        if pcm.is_empty() {
            let _ = app.emit("audio-state", AudioState::Idle);
            None
        } else {
            let _ = app.emit("audio-state", AudioState::Processing);
            Some(pcm)
        }
    }

    pub async fn is_recording(&self) -> bool {
        self.inner.lock().await.recording
    }
}

// ── Resampling helper ─────────────────────────────────────────────────────────

fn resample_chunk(resampler: &mut FftFixedInOut<f32>, input: &[f32]) -> Result<Vec<f32>> {
    let chunk_size = resampler.input_frames_next();
    let mut output = Vec::new();
    let mut pos = 0;

    while pos + chunk_size <= input.len() {
        let chunk = vec![input[pos..pos + chunk_size].to_vec()];
        let out = resampler.process(&chunk, None)?;
        output.extend_from_slice(&out[0]);
        pos += chunk_size;
    }

    Ok(output)
}
