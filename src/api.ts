import { invoke } from "@tauri-apps/api/core";
import type { Settings, ModelEntry } from "./types";

export async function getSettings(): Promise<Settings> {
  return invoke<Settings>("get_settings");
}

export async function saveSettings(settings: Settings): Promise<void> {
  return invoke("save_settings", { settings });
}

export async function getModels(): Promise<ModelEntry[]> {
  return invoke<ModelEntry[]>("get_models");
}

export async function downloadModel(modelId: string): Promise<void> {
  return invoke("download_model", { modelId });
}

export async function cancelDownload(modelId: string): Promise<void> {
  return invoke("cancel_download", { modelId });
}

export async function deleteModel(modelId: string): Promise<void> {
  return invoke("delete_model", { modelId });
}

export async function switchModel(modelId: string): Promise<void> {
  return invoke("switch_model", { modelId });
}

export async function checkAccessibility(): Promise<boolean> {
  return invoke<boolean>("check_accessibility");
}

export async function openAccessibilitySettings(): Promise<void> {
  return invoke("open_accessibility_settings");
}
