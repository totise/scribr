// ── Settings ─────────────────────────────────────────────────────────────────

export interface Settings {
  activeModel: string | null;
  recordHotkey: string;
  switchHotkey: string;
  modelLanguages: Record<string, string>;
  injectionDelayMs: number;
  launchAtLogin: boolean;
}

export const defaultSettings: Settings = {
  activeModel: null,
  recordHotkey: "alt+space",
  switchHotkey: "ctrl+shift+space",
  modelLanguages: {},
  injectionDelayMs: 150,
  launchAtLogin: false,
};

// ── Models ────────────────────────────────────────────────────────────────────

export interface ModelInfo {
  id: string;
  displayName: string;
  filename: string;
  url: string;
  sizeBytes: number;
  languages: string;
  description: string;
}

export type ModelState =
  | { type: "NotDownloaded" }
  | { type: "Downloading"; progress: number }
  | { type: "Ready"; path: string; sizeOnDisk: number }
  | { type: "Active"; path: string; sizeOnDisk: number }
  | { type: "Error"; message: string };

export interface ModelEntry extends ModelInfo {
  state: ModelState;
  language: string;
}

// ── Events ────────────────────────────────────────────────────────────────────

export interface DownloadProgressEvent {
  modelId: string;
  bytesDownloaded: number;
  totalBytes: number;
  progress: number;
}

export type TranscriberState =
  | { type: "Idle" }
  | { type: "Loading"; modelId: string }
  | { type: "Ready"; modelId: string }
  | { type: "Transcribing" }
  | { type: "Error"; message: string };

export type AudioState =
  | { type: "Idle" }
  | { type: "Recording" }
  | { type: "Processing" };
