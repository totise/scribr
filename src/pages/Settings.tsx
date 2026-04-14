import { useState, useEffect } from "react";
import type { Settings } from "../types";
import { defaultSettings } from "../types";
import { getSettings, saveSettings, checkAccessibility, openAccessibilitySettings } from "../api";
import ModelList from "../components/ModelList";
import HotkeyPicker from "../components/HotkeyPicker";
import GeneralSettings from "../components/GeneralSettings";

type Tab = "models" | "hotkeys" | "general";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("models");
  const [settings, setSettings] = useState<Settings>(defaultSettings);
  const [saved, setSaved] = useState(false);
  const [accessibilityOk, setAccessibilityOk] = useState(true);

  useEffect(() => {
    getSettings().then(setSettings).catch(console.error);
    checkAccessibility().then(setAccessibilityOk).catch(() => setAccessibilityOk(false));
  }, []);

  const save = async (next: Settings) => {
    setSettings(next);
    await saveSettings(next);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {/* Accessibility banner */}
      {!accessibilityOk && (
        <div className="bg-yellow-50 border-b border-yellow-200 px-4 py-2 flex items-center gap-3 text-sm text-yellow-800">
          <span>⚠️ Accessibility access required for text injection.</span>
          <button
            onClick={() => openAccessibilitySettings()}
            className="underline font-medium hover:text-yellow-900"
          >
            Open Settings
          </button>
        </div>
      )}

      {/* Tab bar */}
      <div className="flex border-b border-gray-200 bg-white px-4">
        {(["models", "hotkeys", "general"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-3 text-sm font-medium capitalize border-b-2 transition-colors ${
              tab === t
                ? "border-blue-500 text-blue-600"
                : "border-transparent text-gray-500 hover:text-gray-700"
            }`}
          >
            {t}
          </button>
        ))}
        {saved && (
          <span className="ml-auto self-center text-xs text-green-600 font-medium">
            Saved ✓
          </span>
        )}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-4">
        {tab === "models" && <ModelList settings={settings} onSave={save} />}
        {tab === "hotkeys" && <HotkeySection settings={settings} onSave={save} />}
        {tab === "general" && <GeneralSettings settings={settings} onSave={save} />}
      </div>
    </div>
  );
}

function HotkeySection({
  settings,
  onSave,
}: {
  settings: Settings;
  onSave: (s: Settings) => void;
}) {
  return (
    <div className="space-y-6 max-w-lg">
      <div>
        <h2 className="text-base font-semibold text-gray-900 mb-4">Keyboard Shortcuts</h2>
        <div className="space-y-4">
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Record hotkey
            </label>
            <p className="text-xs text-gray-500 mb-3">
              Hold to record. Release to transcribe and type.
            </p>
            <HotkeyPicker
              value={settings.recordHotkey}
              onChange={(v) => onSave({ ...settings, recordHotkey: v })}
            />
          </div>

          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Switch model hotkey
            </label>
            <p className="text-xs text-gray-500 mb-3">
              Cycles through downloaded models.
            </p>
            <HotkeyPicker
              value={settings.switchHotkey}
              onChange={(v) => onSave({ ...settings, switchHotkey: v })}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
