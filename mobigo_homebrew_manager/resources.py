from __future__ import annotations

from pathlib import Path
import sys


def resource_path(name: str) -> Path:
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return bundle / "assets" / name


def launcher_bytes() -> bytes:
    path = resource_path("HomebrewLauncher.MBA")
    if not path.is_file():
        raise FileNotFoundError(f"bundled launcher is missing: {path}")
    return path.read_bytes()
