from __future__ import annotations

from pathlib import Path

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"


def session_dir(name: str) -> Path:
    return SESSIONS_DIR / name


def session_config_path(name: str) -> Path:
    return session_dir(name) / "session.json"


def output_csv_path(name: str) -> Path:
    return session_dir(name) / "annotated.csv"


def input_copy_path(name: str, original_name: str) -> Path:
    return session_dir(name) / f"input_{original_name}"


def list_sessions() -> list[str]:
    if not SESSIONS_DIR.exists():
        return []
    return sorted(p.name for p in SESSIONS_DIR.iterdir() if p.is_dir() and (p / "session.json").exists())
