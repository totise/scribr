import { useState } from "react";
import type { ModelEntry, Settings } from "../types";
import { useModels } from "../hooks";
import { downloadModel, cancelDownload, deleteModel, switchModel } from "../api";

function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${(bytes / 1e3).toFixed(0)} KB`;
}

interface Props {
  settings: Settings;
  onSave: (s: Settings) => void;
}

export default function ModelList({ settings, onSave }: Props) {
  const { models, loading } = useModels();

  if (loading) {
    return <div className="text-sm text-gray-500 p-4">Loading models…</div>;
  }

  return (
    <div className="space-y-3">
      <h2 className="text-base font-semibold text-gray-900">Models</h2>
      <p className="text-xs text-gray-500">
        All models support 99 languages including Danish. No models are included — download
        what you need.
      </p>
      {models.map((model) => (
        <ModelCard
          key={model.id}
          model={model}
          language={settings.modelLanguages[model.id] ?? "auto"}
          onLanguageChange={(lang) =>
            onSave({
              ...settings,
              modelLanguages: { ...settings.modelLanguages, [model.id]: lang },
            })
          }
        />
      ))}
    </div>
  );
}

function ModelCard({
  model,
  language,
  onLanguageChange,
}: {
  model: ModelEntry;
  language: string;
  onLanguageChange: (lang: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const state = model.state;
  const isActive = state.type === "Active";
  const isReady = state.type === "Ready" || isActive;
  const isDownloading = state.type === "Downloading";

  const handle = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={`bg-white rounded-lg border p-4 transition-colors ${
        isActive ? "border-blue-400 ring-1 ring-blue-200" : "border-gray-200"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold text-gray-900">{model.displayName}</span>
            {isActive && (
              <span className="text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium">
                Active
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 mb-1">{model.description}</p>
          <p className="text-xs text-gray-400">
            {model.languages} · {formatBytes(model.sizeBytes)}
          </p>
          {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
        </div>

        {/* Language selector (when ready) */}
        {isReady && (
          <select
            value={language}
            onChange={(e) => onLanguageChange(e.target.value)}
            className="text-xs border border-gray-200 rounded px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400"
            title="Language"
          >
            <option value="auto">Auto</option>
            <option value="en">English</option>
            <option value="da">Danish</option>
            <option value="de">German</option>
            <option value="fr">French</option>
            <option value="es">Spanish</option>
            <option value="nl">Dutch</option>
            <option value="sv">Swedish</option>
            <option value="no">Norwegian</option>
          </select>
        )}
      </div>

      {/* Progress bar */}
      {isDownloading && (
        <div className="mt-3">
          <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
            <div
              className="h-full bg-blue-500 rounded-full transition-all duration-200"
              style={{
                width: `${Math.round((state as { type: "Downloading"; progress: number }).progress * 100)}%`,
              }}
            />
          </div>
          <p className="text-xs text-gray-500 mt-1">
            {Math.round((state as { type: "Downloading"; progress: number }).progress * 100)}%
          </p>
        </div>
      )}

      {/* Disk usage */}
      {isReady && (
        <p className="text-xs text-gray-400 mt-2">
          On disk:{" "}
          {formatBytes(
            (state as { type: "Ready" | "Active"; sizeOnDisk: number }).sizeOnDisk
          )}
        </p>
      )}

      {/* Action buttons */}
      <div className="flex gap-2 mt-3">
        {state.type === "NotDownloaded" && (
          <button
            onClick={() => handle(() => downloadModel(model.id))}
            disabled={busy}
            className="text-xs bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded font-medium disabled:opacity-50"
          >
            Download
          </button>
        )}

        {isDownloading && (
          <button
            onClick={() => handle(() => cancelDownload(model.id))}
            disabled={busy}
            className="text-xs bg-gray-200 hover:bg-gray-300 text-gray-700 px-3 py-1.5 rounded font-medium disabled:opacity-50"
          >
            Cancel
          </button>
        )}

        {state.type === "Ready" && (
          <>
            <button
              onClick={() => handle(() => switchModel(model.id))}
              disabled={busy}
              className="text-xs bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded font-medium disabled:opacity-50"
            >
              {busy ? "Loading…" : "Select"}
            </button>
            <button
              onClick={() => handle(() => deleteModel(model.id))}
              disabled={busy}
              className="text-xs bg-white hover:bg-red-50 text-red-600 border border-red-200 px-3 py-1.5 rounded font-medium disabled:opacity-50"
            >
              Delete
            </button>
          </>
        )}

        {isActive && (
          <button
            onClick={() => handle(() => deleteModel(model.id))}
            disabled={busy}
            className="text-xs bg-white hover:bg-red-50 text-red-600 border border-red-200 px-3 py-1.5 rounded font-medium disabled:opacity-50"
          >
            Delete
          </button>
        )}

        {state.type === "Error" && (
          <button
            onClick={() => handle(() => downloadModel(model.id))}
            disabled={busy}
            className="text-xs bg-red-600 hover:bg-red-700 text-white px-3 py-1.5 rounded font-medium disabled:opacity-50"
          >
            Retry
          </button>
        )}
      </div>
    </div>
  );
}
