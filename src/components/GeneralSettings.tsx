import { useEffect, useState } from "react";
import type { Settings } from "../types";
import { enable as enableAutostart, disable as disableAutostart, isEnabled as isAutostartEnabled } from "@tauri-apps/plugin-autostart";

interface Props {
  settings: Settings;
  onSave: (s: Settings) => void;
}

export default function GeneralSettings({ settings, onSave }: Props) {
  const [autostartEnabled, setAutostartEnabled] = useState(settings.launchAtLogin);

  useEffect(() => {
    isAutostartEnabled()
      .then(setAutostartEnabled)
      .catch(() => setAutostartEnabled(false));
  }, []);

  const toggleAutostart = async () => {
    const next = !autostartEnabled;
    try {
      if (next) {
        await enableAutostart();
      } else {
        await disableAutostart();
      }
      setAutostartEnabled(next);
      onSave({ ...settings, launchAtLogin: next });
    } catch (e) {
      console.error("Autostart toggle failed:", e);
    }
  };

  return (
    <div className="space-y-6 max-w-lg">
      <h2 className="text-base font-semibold text-gray-900">General</h2>

      {/* Launch at login */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 flex items-center justify-between">
        <div>
          <p className="text-sm font-medium text-gray-800">Launch at login</p>
          <p className="text-xs text-gray-500 mt-0.5">Start Scribr automatically when you log in.</p>
        </div>
        <Toggle checked={autostartEnabled} onChange={toggleAutostart} />
      </div>

      {/* Injection delay */}
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <label className="block text-sm font-medium text-gray-800 mb-1">
          Text injection delay
        </label>
        <p className="text-xs text-gray-500 mb-3">
          Milliseconds to wait before typing transcribed text. Increase if text lands in the wrong app.
        </p>
        <div className="flex items-center gap-3">
          <input
            type="number"
            min={0}
            max={2000}
            step={50}
            value={settings.injectionDelayMs}
            onChange={(e) =>
              onSave({ ...settings, injectionDelayMs: parseInt(e.target.value, 10) || 0 })
            }
            className="w-24 border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
          <span className="text-sm text-gray-500">ms</span>
        </div>
      </div>
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={onChange}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
        checked ? "bg-blue-600" : "bg-gray-200"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}
