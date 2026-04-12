"""
config.py — Load and manage scribr configuration from ~/.config/scribr/config.toml.

Model registry structure (from TOML):
  [models.<key>]
  model_id = "nvidia/..."
  label    = "English"
  icon     = "EN"
  enabled  = true
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "scribr"
CONFIG_PATH = CONFIG_DIR / "config.toml"
EXAMPLE_CONFIG = Path(__file__).parent.parent / "config.toml.example"

DEFAULT_SELECTOR_HOTKEY = "<ctrl>+<shift>+<space>"


@dataclass
class ModelConfig:
    key: str  # TOML table key, e.g. "english"
    model_id: str  # HuggingFace / NeMo model ID
    label: str  # Human-readable name, e.g. "English"
    icon: str  # Short string shown in menu bar, e.g. "EN"
    enabled: bool = True
    strategy: str = "batch"  # "batch" or "chunked"
    chunk_seconds: float = 2.5  # chunk length (chunked mode only)
    overlap_seconds: float = 0.5  # overlap between consecutive chunks


@dataclass
class Config:
    active_model: str
    selector_hotkey: str
    models: dict[str, ModelConfig] = field(default_factory=dict)

    @property
    def active(self) -> ModelConfig | None:
        return self.models.get(self.active_model)

    @property
    def enabled_models(self) -> list[ModelConfig]:
        return [m for m in self.models.values() if m.enabled]


def _ensure_config_exists() -> None:
    """Copy the example config to ~/.config/scribr/config.toml on first run."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        if EXAMPLE_CONFIG.exists():
            shutil.copy(EXAMPLE_CONFIG, CONFIG_PATH)
        else:
            # Write a minimal built-in default if the example file is missing
            CONFIG_PATH.write_text(
                'active_model = "english"\n'
                'selector_hotkey = "<ctrl>+<shift>+<space>"\n\n'
                "[models.english]\n"
                'model_id = "nvidia/parakeet-tdt-0.6b-v2"\n'
                'label = "English"\n'
                'icon = "EN"\n'
                "enabled = true\n\n"
                "[models.danish]\n"
                'model_id = "nvidia/parakeet-rnnt-110m-da-dk"\n'
                'label = "Danish"\n'
                'icon = "DA"\n'
                "enabled = true\n"
            )


def load() -> Config:
    """Load and return the current configuration. Creates defaults on first run."""
    _ensure_config_exists()

    with CONFIG_PATH.open("rb") as fh:
        raw = tomllib.load(fh)

    models: dict[str, ModelConfig] = {}
    for key, val in raw.get("models", {}).items():
        models[key] = ModelConfig(
            key=key,
            model_id=val["model_id"],
            label=val.get("label", key.capitalize()),
            icon=val.get("icon", key[:2].upper()),
            enabled=val.get("enabled", True),
            strategy=val.get("strategy", "batch"),
            chunk_seconds=float(val.get("chunk_seconds", 2.5)),
            overlap_seconds=float(val.get("overlap_seconds", 0.5)),
        )

    active = raw.get("active_model", next(iter(models), "english"))
    # Fall back to first enabled model if the stored active key is unknown/disabled
    if active not in models or not models[active].enabled:
        enabled = [k for k, m in models.items() if m.enabled]
        active = enabled[0] if enabled else active

    return Config(
        active_model=active,
        selector_hotkey=raw.get("selector_hotkey", DEFAULT_SELECTOR_HOTKEY),
        models=models,
    )


def save_active_model(key: str) -> None:
    """Persist the active_model key back to config.toml."""
    _ensure_config_exists()
    text = CONFIG_PATH.read_text()
    lines = text.splitlines()
    new_lines: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("active_model"):
            new_lines.append(f'active_model = "{key}"')
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.insert(0, f'active_model = "{key}"')
    CONFIG_PATH.write_text("\n".join(new_lines) + "\n")
