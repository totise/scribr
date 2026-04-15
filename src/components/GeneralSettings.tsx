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
    <div className="space-y-3 max-w-lg">
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide px-0.5">
        General
      </h2>

      <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100">
        {/* Launch at login */}
        <div className="flex items-center justify-between px-4 py-3.5">
          <div>
            <p className="text-sm font-medium text-gray-800">Launch at login</p>
            <p className="text-xs text-gray-400 mt-0.5">Start Scribr automatically when you log in.</p>
          </div>
          <Toggle checked={autostartEnabled} onChange={toggleAutostart} />
        </div>

        {/* Injection delay */}
        <div className="flex items-center justify-between px-4 py-3.5">
          <div className="flex-1 min-w-0 pr-4">
            <p className="text-sm font-medium text-gray-800">Injection delay</p>
            <p className="text-xs text-gray-400 mt-0.5">
              Milliseconds to wait before typing. Increase if text lands in the wrong window.
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <input
              type="number"
              min={0}
              max={2000}
              step={50}
              value={settings.injectionDelayMs}
              onChange={(e) =>
                onSave({ ...settings, injectionDelayMs: parseInt(e.target.value, 10) || 0 })
              }
              className="w-20 border border-gray-200 rounded-lg px-2.5 py-1.5 text-sm text-right focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            <span className="text-xs text-gray-400 w-5">ms</span>
          </div>
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
      className={`relative inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
        checked ? "bg-blue-500" : "bg-gray-200"
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
