# Scribr

A macOS menu-bar dictation app powered by [whisper.cpp](https://github.com/ggerganov/whisper.cpp). Hold a hotkey to record, release to transcribe, and the text is typed into whatever app is in focus.

Built with [Tauri v2](https://tauri.app) (Rust + React + TypeScript). No Python. No cloud. Everything runs locally.

## Features

- **Hold-to-record** — hold the hotkey while speaking, release to transcribe
- **Instant text injection** — transcribed text is pasted into the focused window via the clipboard (works with any app, supports Unicode/Danish ÆØÅ)
- **6 Whisper models** — from Tiny (75 MB) to Large v3 Turbo Q5 (547 MB); download only what you need
- **Per-model language** — set a language per model (Auto, English, Danish, German, French, Spanish, Dutch, Swedish, Norwegian)
- **Model-switch hotkey** — cycle through downloaded models without opening Settings
- **Configurable hotkeys** — click to capture any key combo
- **Launch at login** — optional via macOS LaunchAgent
- **Apple Silicon GPU** — uses Metal via whisper.cpp for fast inference
- **No Dock icon** — lives entirely in the menu bar

## Requirements

- macOS 13 Ventura or later
- Apple Silicon (M1/M2/M3/M4)

## Installation

Download the latest `.dmg` from [Releases](https://github.com/totise/scribr/releases), mount it, and drag Scribr to Applications.

On first launch macOS will ask for:
- **Microphone access** — required for recording
- **Accessibility access** — required for text injection (Settings → Privacy & Security → Accessibility)

## Usage

1. Open **Settings** from the menu-bar icon → **Models** tab → download a model
2. Click **Select** on the downloaded model to load it
3. Hold **⌥ Space** (default) while speaking — release to transcribe and type
4. Use **⌃⇧ Space** (default) to cycle between downloaded models

All hotkeys and settings are configurable in the Settings window.

## Building from source

### Prerequisites

- [Rust](https://rustup.rs) 1.77+
- [Node.js](https://nodejs.org) 20+
- Xcode Command Line Tools (`xcode-select --install`)
- [Tauri CLI](https://tauri.app/start/): `npm install -g @tauri-apps/cli`

### Build

```bash
git clone https://github.com/totise/scribr.git
cd scribr
npm install
cargo tauri build
```

The `.dmg` will be at `src-tauri/target/release/bundle/dmg/Scribr_*.dmg`.

### Development

```bash
npm install
cargo tauri dev
```

## Models

Models are downloaded from [huggingface.co/ggerganov/whisper.cpp](https://huggingface.co/ggerganov/whisper.cpp) and stored in `~/Library/Application Support/ai.scribr.app/models/`.

| Model | Size | Notes |
|---|---|---|
| Tiny | 75 MB | Fastest, lowest accuracy |
| Base | 142 MB | Fast, acceptable accuracy |
| Small | 466 MB | Good quality/speed tradeoff |
| Medium | 1.5 GB | High accuracy |
| Large v3 Turbo | 1.6 GB | Best quality |
| Large v3 Turbo Q5 | 547 MB | Best quality quantized — recommended for Danish |

## License

MIT
