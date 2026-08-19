"""Small per-user settings store for the Windows GUI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def settings_path() -> Path:
    app_data = os.environ.get("APPDATA")
    base = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
    return base / "ArtTools" / "uniform_uv_gui.json"


def load_settings() -> dict[str, Any]:
    path = settings_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_settings(values: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
