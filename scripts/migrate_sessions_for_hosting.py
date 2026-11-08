#!/usr/bin/env python
"""One-off: rewrite a copy of ``sessions/`` so it can move to a hosted HELIX server.

Never modifies ``--src``. Warnings never contain secret values (LLM keys, KB
headers, graph passwords); they only name the session and the kind of secret.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eltool.config import SessionConfig  # noqa: E402

_LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def _copy_context(folder: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for p in folder.iterdir():
        if p.is_file() and (p.suffix == ".txt" or p.suffix == ""):
            shutil.copy2(p, target / p.name)


def _migrate_one(name: str, sdir: Path, dest_dir: Path, kb_map: dict[str, str]) -> list[str]:
    warnings: list[str] = []
    sj = dest_dir / "session.json"
    try:
        cfg = SessionConfig.load(sj)
    except Exception as e:  # validation or JSON error
        return [f"session {name}: session.json is invalid ({type(e).__name__}); copied unchanged"]

    old_input = cfg.input_path
    basename = Path(old_input.replace("\\", "/")).name
    new_input = dest_dir / basename
    if not new_input.exists():
        old = Path(old_input)
        if old.is_file():
            shutil.copy2(old, new_input)
        else:
            warnings.append(f"session {name}: input file {basename} not found")
    cfg.input_path = str(new_input)

    for kb in cfg.kbs:
        for old_prefix, new_prefix in kb_map.items():
            if kb.endpoint_url.startswith(old_prefix):
                kb.endpoint_url = new_prefix + kb.endpoint_url[len(old_prefix):]
                break
        else:
            warnings.append(
                f"session {name}: KB {kb.name} endpoint {kb.endpoint_url} has no --kb-map entry"
            )

    if cfg.context_folder:
        folder = Path(cfg.context_folder)
        if folder.is_dir():
            _copy_context(folder, dest_dir / "context")
            cfg.context_folder = str(dest_dir / "context")
        else:
            warnings.append(f"session {name}: context folder not found, context_folder cleared")
            cfg.context_folder = None

    if cfg.llm_api_key:
        warnings.append(f"session {name}: stores an LLM API key; consider removing it")

    if cfg.graph is not None:
        host = (urlparse(cfg.graph.uri).hostname or "").lower()
        if host in _LOCAL_HOSTS:
            warnings.append(
                f"session {name}: graph uri points at {host}, which is not reachable from the server"
            )
        if cfg.graph.password:
            warnings.append(f"session {name}: stores a graph password")

    if any(kb.headers.strip() not in ("", "{}") for kb in cfg.kbs):
        warnings.append(f"session {name}: stores KB request headers")

    cfg.save(sj)
    return warnings


def migrate(src: Path, dest: Path, kb_map: dict[str, str]) -> list[str]:
    src, dest = Path(src), Path(dest)
    if dest.exists():
        raise FileExistsError(str(dest))
    shutil.copytree(src, dest)
    warnings: list[str] = []
    for sdir in sorted(src.iterdir()):
        if sdir.is_dir() and (sdir / "session.json").is_file():
            warnings += _migrate_one(sdir.name, sdir, dest / sdir.name, kb_map)
    return warnings


def parse_kb_map(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--kb-map expects OLD=NEW, got {item!r}")
        old, new = item.split("=", 1)
        out[old] = new
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--kb-map", action="append", default=[], metavar="OLD=NEW")
    args = ap.parse_args(argv)
    try:
        kb_map = parse_kb_map(args.kb_map)
    except ValueError as e:
        ap.error(str(e))
    if not args.src.is_dir():
        ap.error(f"--src {args.src} is not a directory")
    try:
        warnings = migrate(args.src, args.dest, kb_map)
    except FileExistsError:
        ap.error(f"--dest {args.dest} already exists")
    for w in warnings:
        print(f"WARNING: {w}")
    print(f"done: {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
