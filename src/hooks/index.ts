import { useState, useEffect, useCallback } from "react";
import { listen } from "@tauri-apps/api/event";
import type { ModelEntry, TranscriberState, AudioState, DownloadProgressEvent } from "../types";
import { getModels } from "../api";

// ── useModels ─────────────────────────────────────────────────────────────────

export function useModels() {
  const [models, setModels] = useState<ModelEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const m = await getModels();
      setModels(m);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    // Listen for backend model-state changes
    const unlisten = listen<ModelEntry[]>("models-changed", (e) => {
      setModels(e.payload);
    });
    return () => {
      unlisten.then((fn) => fn());
    };
  }, [refresh]);

  return { models, loading, refresh };
}

// ── useTranscriberState ───────────────────────────────────────────────────────

export function useTranscriberState() {
  const [state, setState] = useState<TranscriberState>({ type: "Idle" });

  useEffect(() => {
    const unlisten = listen<TranscriberState>("transcriber-state", (e) => {
      setState(e.payload);
    });
    return () => {
      unlisten.then((fn) => fn());
    };
  }, []);

  return state;
}

// ── useAudioState ─────────────────────────────────────────────────────────────

export function useAudioState() {
  const [state, setState] = useState<AudioState>({ type: "Idle" });

  useEffect(() => {
    const unlisten = listen<AudioState>("audio-state", (e) => {
      setState(e.payload);
    });
    return () => {
      unlisten.then((fn) => fn());
    };
  }, []);

  return state;
}

// ── useDownloadProgress ───────────────────────────────────────────────────────

export function useDownloadProgress() {
  const [progress, setProgress] = useState<Record<string, number>>({});

  useEffect(() => {
    const unlisten = listen<DownloadProgressEvent>("download-progress", (e) => {
      setProgress((prev) => ({
        ...prev,
        [e.payload.modelId]: e.payload.progress,
      }));
    });
    return () => {
      unlisten.then((fn) => fn());
    };
  }, []);

  return progress;
}
