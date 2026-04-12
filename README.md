# Scribr

macOS menu-bar dictation app powered by NVIDIA Parakeet ASR models.

Hold **right Option** to record. Release to transcribe. Text is typed into whatever window has focus.

---

## Requirements

- macOS 12+
- Python 3.11+
- ~6 GB free RAM (for the English 0.6B model)
- Microphone access
- Accessibility access (for global hotkeys and text injection)

---

## Installation

### 1. Install PyTorch

Install PyTorch first (NeMo requires it as a pre-requisite):

```bash
pip install torch torchaudio
```

For Apple Silicon you can also enable MPS (Metal Performance Shaders) — NeMo will use it automatically if available.

### 2. Install NeMo and app dependencies

```bash
pip install "nemo_toolkit[asr]>=2.5.0"
pip install sounddevice numpy pynput rumps
```

### 3. Install Scribr

```bash
cd /path/to/scribr
pip install -e .
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
| `EN ●` | Recording |
| `EN …` | Transcribing |
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

[models.danish]
model_id = "nvidia/parakeet-rnnt-110m-da-dk"
label = "Danish"
icon = "DA"
enabled = true
```

### Adding a model

Add a new `[models.<key>]` block, then use **Reload Config** from the menu:

```toml
[models.multilingual]
model_id = "nvidia/parakeet-tdt-0.6b-v3"
label = "Multilingual"
icon = "ML"
enabled = true
```

Any model from the `nvidia/parakeet-*` family on HuggingFace can be used.

---

## Notes

- **Model switching takes ~20 seconds** — the model must be fully unloaded from RAM and the new one loaded. The icon shows `⟳` while loading. The record hotkey is disabled during this time.
- Models are downloaded to `~/.cache/huggingface/` on first use and cached locally.
- Only one model is loaded at a time to conserve RAM.
- Logs are written to stderr. Run from a terminal to see them.
