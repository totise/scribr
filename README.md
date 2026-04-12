# Scribr

macOS menu-bar dictation app powered by NVIDIA Parakeet ASR models.

Hold **right Option** to record. Release to transcribe. Text is typed into whatever window has focus.

Two transcription strategies are available per model:

- **Batch** — hold key, release, single inference, text appears. Best accuracy.
- **Chunked** — text is typed as you speak (pseudo-streaming). Audio is split into overlapping chunks and transcribed in parallel with recording. Best perceived latency.

---

## Requirements

- macOS 12+
- Python 3.11+
- ~6 GB free RAM (for the English 0.6B model), ~2 GB for the Danish 110M model
- Microphone access
- Accessibility access (for global hotkeys and text injection)

---

## Installation

### One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/totise/scribr/main/install.sh | bash
```

This will:
1. Create a self-contained virtualenv at `~/.scribr/venv`
2. Install PyTorch, NeMo ASR, and all dependencies into it
3. Write a `scribr` launcher and symlink it to `/usr/local/bin`

Models (~2.5 GB for English, ~450 MB for Danish) are downloaded from HuggingFace on first use and cached in `~/.cache/huggingface/`.

### Manual install

Clone the repo and run the install script directly:

```bash
git clone https://github.com/totise/scribr.git
cd scribr
./install.sh
```

### Developer install

To install in editable mode from your local clone:

```bash
git clone https://github.com/totise/scribr.git
cd scribr
./install.sh --dev
```

### Manual step-by-step

If you prefer full control:

```bash
python3.11 -m venv ~/.scribr/venv
source ~/.scribr/venv/bin/activate
pip install torch torchaudio
pip install "nemo_toolkit[asr]>=2.5.0"
pip install -e /path/to/scribr
```

---

## Running

```bash
scribr
```

Or directly:

```bash
python -m scribr.main
```

On first run:
- A default `~/.config/scribr/config.toml` is created.
- A prompt will appear if Accessibility access has not been granted. Open **System Settings → Privacy & Security → Accessibility** and enable your terminal or Python binary.

---

## Usage

| Action | How |
|---|---|
| Start recording | Hold **right Option** |
| Stop + transcribe | Release **right Option** |
| Switch language | **Ctrl+Shift+Space** (opens model selector dialog) |
| Switch via menu | Click the menu-bar icon → Switch Model |

The menu-bar icon reflects the active language and state:

| Icon | Meaning |
|---|---|
| `EN` | English — ready |
| `EN ⟳` | Loading model (~20s) |
| `EN ●` | Recording (batch) |
| `EN ●…` | Recording + transcribing in parallel (chunked) |
| `EN …` | Transcribing final chunk / full batch |
| `EN ✕` | Error |

---

## Configuration

Edit `~/.config/scribr/config.toml`:

```toml
active_model = "english"
selector_hotkey = "<ctrl>+<shift>+<space>"

[models.english]
model_id = "nvidia/parakeet-tdt-0.6b-v2"
label = "English"
icon = "EN"
enabled = true
strategy = "batch"         # single inference after key release

[models.danish]
model_id = "nvidia/parakeet-rnnt-110m-da-dk"
label = "Danish"
icon = "DA"
enabled = true
strategy = "chunked"       # text typed as you speak
chunk_seconds = 2.5        # length of each audio chunk sent to the model
overlap_seconds = 0.5      # overlap between chunks for boundary stitching
```

After editing, use **Reload Config** from the menu — no restart needed.

### Transcription strategies

| Strategy | How it works | Best for |
|---|---|---|
| `batch` | Full audio sent as one inference after key release | Accuracy, short recordings |
| `chunked` | Audio split into overlapping chunks; results typed as they arrive | Perceived speed, longer dictation |

In chunked mode, consecutive chunks share a 0.5 s overlap window. A stitcher compares the tail of each chunk result against the head of the next and removes duplicated words before typing.

### Adding a model

Add a new `[models.<key>]` block, then use **Reload Config** from the menu:

```toml
[models.multilingual]
model_id = "nvidia/parakeet-tdt-0.6b-v3"
label = "Multilingual"
icon = "ML"
enabled = true
strategy = "batch"
```

Any model from the `nvidia/parakeet-*` family on HuggingFace can be used.

---

## Notes

- **Model switching takes ~20 seconds** — the model must be fully unloaded from RAM and the new one loaded. The icon shows `⟳` while loading. The record hotkey is disabled during this time.
- Models are downloaded to `~/.cache/huggingface/` on first use and cached locally.
- Only one model is loaded at a time to conserve RAM.
- **Chunked mode latency** — expect ~1–2 s lag between speech and text appearing, depending on CPU speed. The 110M Danish model is faster per chunk than the 600M English model.
- Logs are written to stderr. Run from a terminal to see them.
