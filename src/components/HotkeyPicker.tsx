import { useState, useEffect, useRef } from "react";

interface Props {
  value: string;
  onChange: (value: string) => void;
}

/**
 * A hotkey picker input.
 * Click it to enter capture mode — the next key combo pressed becomes the value.
 * The value is a string in tauri-plugin-global-shortcut format: "alt+space", "ctrl+shift+f12", etc.
 */
export default function HotkeyPicker({ value, onChange }: Props) {
  const [capturing, setCapturing] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!capturing) return;

    const onKeyDown = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();

      // Ignore bare modifier-only presses
      if (["Control", "Shift", "Alt", "Meta", "OS"].includes(e.key)) return;

      const parts: string[] = [];
      if (e.ctrlKey) parts.push("ctrl");
      if (e.shiftKey) parts.push("shift");
      if (e.altKey) parts.push("alt");
      if (e.metaKey) parts.push("super");

      // Map key to tauri shortcut format
      const key = mapKey(e.code, e.key);
      parts.push(key);

      onChange(parts.join("+"));
      setCapturing(false);
    };

    const onBlur = () => setCapturing(false);

    window.addEventListener("keydown", onKeyDown, true);
    ref.current?.addEventListener("blur", onBlur);

    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      ref.current?.removeEventListener("blur", onBlur);
    };
  }, [capturing, onChange]);

  const display = formatHotkey(value);

  return (
    <button
      ref={ref}
      onClick={() => {
        setCapturing(true);
        ref.current?.focus();
      }}
      className={`
        w-full text-left px-2.5 py-1.5 rounded-lg border text-sm font-mono transition-colors
        ${
          capturing
            ? "border-blue-500 ring-2 ring-blue-200 bg-blue-50 text-blue-700"
            : "border-gray-200 bg-white text-gray-800 hover:border-gray-300"
        }
      `}
    >
      {capturing ? (
        <span className="text-blue-600 animate-pulse">Press a key combination…</span>
      ) : (
        display || <span className="text-gray-400">Click to set shortcut</span>
      )}
    </button>
  );
}

/** Format a tauri shortcut string for display with symbols. */
function formatHotkey(value: string): string {
  if (!value) return "";
  return value
    .split("+")
    .map((part) => {
      switch (part.toLowerCase()) {
        case "ctrl":
        case "control":
          return "⌃";
        case "shift":
          return "⇧";
        case "alt":
        case "option":
          return "⌥";
        case "super":
        case "meta":
        case "cmd":
          return "⌘";
        case "space":
          return "Space";
        case "return":
        case "enter":
          return "↩";
        case "tab":
          return "⇥";
        case "escape":
        case "esc":
          return "⎋";
        case "backspace":
          return "⌫";
        case "delete":
          return "⌦";
        case "up":
          return "↑";
        case "down":
          return "↓";
        case "left":
          return "←";
        case "right":
          return "→";
        default:
          return part.length === 1 ? part.toUpperCase() : part;
      }
    })
    .join(" ");
}

/** Map a browser KeyboardEvent to tauri shortcut key name. */
function mapKey(code: string, key: string): string {
  // Function keys
  if (/^F\d+$/.test(key)) return key.toLowerCase();

  // Special keys
  switch (code) {
    case "Space":
      return "space";
    case "Enter":
      return "return";
    case "Tab":
      return "tab";
    case "Escape":
      return "escape";
    case "Backspace":
      return "backspace";
    case "Delete":
      return "delete";
    case "ArrowUp":
      return "up";
    case "ArrowDown":
      return "down";
    case "ArrowLeft":
      return "left";
    case "ArrowRight":
      return "right";
    case "Home":
      return "home";
    case "End":
      return "end";
    case "PageUp":
      return "pageup";
    case "PageDown":
      return "pagedown";
    case "Insert":
      return "insert";
    case "CapsLock":
      return "capslock";
    default:
      // Regular key — use the key value, lowercased
      return key.toLowerCase();
  }
}
