import { useState } from "react";
import type { ModelEntry, Settings } from "../types";
import { useModels } from "../hooks";
import { downloadModel, cancelDownload, deleteModel, switchModel } from "../api";

function formatBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${(bytes / 1e3).toFixed(0)} KB`;
}

const LANGUAGES = [
  { value: "auto", label: "Auto-detect" },
  { value: "en", label: "English" },
  { value: "da", label: "Danish" },
  { value: "de", label: "German" },
  { value: "fr", label: "French" },
  { value: "es", label: "Spanish" },
  { value: "nl", label: "Dutch" },
  { value: "sv", label: "Swedish" },
  { value: "no", label: "Norwegian" },
];

interface Props {
  settings: Settings;
  onSave: (s: Settings) => void;
}

export default function ModelList({ settings, onSave }: Props) {
  const { models, loading } = useModels();

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12 text-sm text-gray-400">
        Loading…
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-xs text-gray-500 pb-1">
        Models are downloaded to your Mac and run entirely offline. Download at least one to get started.
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
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);

  const state = model.state;
  const isActive = state.type === "Active";
  const isReady = state.type === "Ready" || isActive;
  const isDownloading = state.type === "Downloading";
  const progress = isDownloading
    ? Math.round((state as { type: "Downloading"; progress: number }).progress * 100)
    : 0;

  // Downloads are fire-and-forget — the backend streams progress via events.
  // We do NOT await downloadModel() because it only resolves when the download
  // finishes, which would freeze the button for the entire duration.
  const handleDownload = () => {
    setError(null);
    downloadModel(model.id).catch((e) => setError(String(e)));
  };

  const handleCancel = () => {
    cancelDownload(model.id).catch((e) => setError(String(e)));
  };

  const handleSelect = async () => {
    setSwitching(true);
    setError(null);
    try {
      await switchModel(model.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setSwitching(false);
    }
  };

  const handleDelete = () => {
    setError(null);
    deleteModel(model.id).catch((e) => setError(String(e)));
  };

  return (
    <div
      className={`rounded-lg border bg-white transition-all ${
        isActive
          ? "border-blue-400 shadow-sm shadow-blue-100"
          : "border-gray-200"
      }`}
    >
      {/* Main row */}
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Status dot */}
        <div className="flex-shrink-0">
          {isActive && (
            <div className="w-2 h-2 rounded-full bg-blue-500" title="Active" />
          )}
          {isReady && !isActive && (
            <div className="w-2 h-2 rounded-full bg-green-400" title="Downloaded" />
          )}
          {isDownloading && (
            <div className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse" title="Downloading" />
          )}
          {!isReady && !isDownloading && (
            <div className="w-2 h-2 rounded-full bg-gray-200" title="Not downloaded" />
          )}
        </div>

        {/* Name + description */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-900 leading-tight">
              {model.displayName}
            </span>
            {isActive && (
              <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded font-medium leading-none">
                active
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 leading-snug mt-0.5 truncate">
            {model.description}
            {isReady && (
              <span className="text-gray-400">
                {" "}·{" "}
                {formatBytes(
                  (state as { type: string; sizeOnDisk: number }).sizeOnDisk
                )}{" "}
                on disk
              </span>
            )}
            {!isReady && !isDownloading && (
              <span className="text-gray-400"> · {formatBytes(model.sizeBytes)}</span>
            )}
          </p>
        </div>

        {/* Language selector */}
        {isReady && (
          <select
            value={language}
            onChange={(e) => onLanguageChange(e.target.value)}
            className="text-xs border border-gray-200 rounded px-2 py-1 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-blue-400 flex-shrink-0"
          >
            {LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
        )}

        {/* Action buttons */}
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {state.type === "NotDownloaded" && (
            <button
              onClick={handleDownload}
              className="text-xs bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white px-3 py-1.5 rounded-md font-medium transition-colors"
            >
              Download
            </button>
          )}

          {isDownloading && (
            <button
              onClick={handleCancel}
              className="text-xs bg-gray-100 hover:bg-gray-200 text-gray-700 px-3 py-1.5 rounded-md font-medium transition-colors"
            >
              Cancel
            </button>
          )}

          {state.type === "Ready" && (
            <>
              <button
                onClick={handleSelect}
                disabled={switching}
                className="text-xs bg-blue-600 hover:bg-blue-700 active:bg-blue-800 text-white px-3 py-1.5 rounded-md font-medium transition-colors disabled:opacity-50"
              >
                {switching ? "Loading…" : "Use"}
              </button>
              <button
                onClick={handleDelete}
                className="text-xs text-gray-400 hover:text-red-500 px-2 py-1.5 rounded-md transition-colors"
                title="Delete model"
              >
                ✕
              </button>
            </>
          )}

          {isActive && (
            <button
              onClick={handleDelete}
              className="text-xs text-gray-400 hover:text-red-500 px-2 py-1.5 rounded-md transition-colors"
              title="Delete model"
            >
              ✕
            </button>
          )}

          {state.type === "Error" && (
            <button
              onClick={handleDownload}
              className="text-xs bg-red-600 hover:bg-red-700 text-white px-3 py-1.5 rounded-md font-medium transition-colors"
            >
              Retry
            </button>
          )}
        </div>
      </div>

      {/* Progress bar — shown below the row when downloading */}
      {isDownloading && (
        <div className="px-4 pb-3">
          <div className="flex items-center gap-2">
            <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-500 rounded-full transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
            <span className="text-xs text-gray-400 w-8 text-right">{progress}%</span>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="px-4 pb-3">
          <p className="text-xs text-red-600">{error}</p>
        </div>
      )}
    </div>
  );
}
