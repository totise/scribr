import { useState, useEffect } from "react";
import type { Settings } from "../types";
import { defaultSettings } from "../types";
import { getSettings, saveSettings, checkAccessibility, openAccessibilitySettings } from "../api";
import ModelList from "../components/ModelList";
import HotkeyPicker from "../components/HotkeyPicker";
import GeneralSettings from "../components/GeneralSettings";

type Tab = "models" | "hotkeys" | "general";

const TAB_LABELS: Record<Tab, string> = {
  models: "Models",
  hotkeys: "Hotkeys",
  general: "General",
};

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
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-2.5 flex items-center gap-3 text-xs text-amber-800">
          <span className="font-medium">Accessibility access required for text injection.</span>
          <button
            onClick={() => openAccessibilitySettings()}
            className="underline font-semibold hover:text-amber-900 transition-colors"
          >
            Open System Settings →
          </button>
        </div>
      )}

      {/* Tab bar */}
      <div className="flex items-center border-b border-gray-200 bg-white px-4 h-11">
        <div className="flex gap-1">
          {(["models", "hotkeys", "general"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-3.5 py-1.5 text-sm font-medium rounded-md transition-colors ${
                tab === t
                  ? "bg-gray-100 text-gray-900"
                  : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
              }`}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>
        {saved && (
          <span className="ml-auto text-xs text-green-600 font-medium">
            Saved
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
    <div className="space-y-3 max-w-lg">
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide px-0.5">
        Keyboard Shortcuts
      </h2>

      <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100">
        <div className="p-4">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-800">Record</p>
              <p className="text-xs text-gray-400 mt-0.5">Hold to record, release to transcribe and type.</p>
            </div>
            <div className="w-44 flex-shrink-0">
              <HotkeyPicker
                value={settings.recordHotkey}
                onChange={(v) => onSave({ ...settings, recordHotkey: v })}
              />
            </div>
          </div>
        </div>

        <div className="p-4">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-800">Switch model</p>
              <p className="text-xs text-gray-400 mt-0.5">Cycles through downloaded models.</p>
            </div>
            <div className="w-44 flex-shrink-0">
              <HotkeyPicker
                value={settings.switchHotkey}
                onChange={(v) => onSave({ ...settings, switchHotkey: v })}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
