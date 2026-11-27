from __future__ import annotations

from pathlib import Path

SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"


def session_dir(name: str) -> Path:
    return SESSIONS_DIR / name


def session_config_path(name: str) -> Path:
    return session_dir(name) / "session.json"


def output_csv_path(name: str, user_id: int | None = None) -> Path:
    """The annotation layer: the legacy shared ``annotated.csv`` without a user,
    else that user's private layer ``annotations/<user_id>.csv``."""
    if user_id is None:
        return session_dir(name) / "annotated.csv"
    return session_dir(name) / "annotations" / f"{int(user_id)}.csv"


def output_snapshot_path(name: str, stage: str, user_id: int | None = None) -> Path:
    """Per-step output snapshot, e.g. annotated.retrieval.csv / annotated.graph.csv,
    or annotated.<stage>.<user_id>.csv for a user's private layer."""
    if user_id is None:
        return session_dir(name) / f"annotated.{stage}.csv"
    return session_dir(name) / f"annotated.{stage}.{int(user_id)}.csv"


def reflection_path(name: str, user_id: int | None = None) -> Path:
    """End-of-session reflection (hint ranking + comment): one per session,
    or reflection.<user_id>.json per user."""
    if user_id is None:
        return session_dir(name) / "reflection.json"
    return session_dir(name) / f"reflection.{int(user_id)}.json"


def input_copy_path(name: str, original_name: str) -> Path:
    return session_dir(name) / f"input_{original_name}"


def list_sessions() -> list[str]:
    if not SESSIONS_DIR.exists():
        return []
    return sorted(p.name for p in SESSIONS_DIR.iterdir() if p.is_dir() and (p / "session.json").exists())
