#!/usr/bin/env python
"""Create the ``demo`` annotation session that invited evaluators work through.

Runs inside the api container, with the Neo4j password in NEO4J_PASSWORD:

    python scripts/seed_demo_session.py

Writes the mentions in scripts/demo_data/documents.json to input.csv, one context
file per document and session.json. Saved answers are keyed by input row, so an
existing session is never seeded again; --update-config rewrites its session.json
only. session.json is written last, so a seed that stops part-way is put right by
running the script again.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eltool.config import GraphConnection, KBConfig, SessionConfig  # noqa: E402
from eltool.session import session_config_path, session_dir  # noqa: E402

DOCUMENTS = Path(__file__).resolve().parent / "demo_data" / "documents.json"
SESSION = "demo"
MIN_PASSWORD_LENGTH = 8  # Neo4j 5 refuses shorter passwords by default


class SeedError(RuntimeError):
    """The demo session cannot be written as asked."""


def load_documents() -> list[dict]:
    """The demo documents: ``{"id", "text", "mentions"}``, mentions in reading order."""
    return json.loads(DOCUMENTS.read_text(encoding="utf-8"))


def build_rows(documents: list[dict]) -> list[dict]:
    """Input rows ``{mention, document_id, span}``; span is ``start-end``, end exclusive.

    A mention matches whole words only, and one listed again in the same document
    takes the next occurrence.
    """
    rows: list[dict] = []
    for document in documents:
        resume: dict[str, int] = {}  # where each mention's next search starts
        for mention in document["mentions"]:
            pattern = re.compile(rf"(?<!\w){re.escape(mention)}(?!\w)")
            found = mention and pattern.search(document["text"], resume.get(mention, 0))
            if not found:
                raise SeedError(f"Document {document['id']} has no further whole-word "
                                f"occurrence of the mention '{mention}'.")
            resume[mention] = found.end()
            rows.append({"mention": mention, "document_id": document["id"],
                         "span": f"{found.start()}-{found.end()}"})
    return rows


def _config(folder: Path, graph_password: str, kb_url: str, graph_uri: str) -> SessionConfig:
    return SessionConfig(
        session_name=SESSION,
        annotator=SESSION,
        input_path=str(folder / "input.csv"),
        label_column="mention",
        doc_id_col="document_id",
        span_col="span",
        context_folder=str(folder / "context"),
        kbs=[KBConfig(name="MeSH", endpoint_url=kb_url, response_path="results")],
        enable_graph=True,
        graph=GraphConnection(uri=graph_uri, user="neo4j", password=graph_password),
        enable_llm_rerank=False,
    )


def _save_config(config: SessionConfig, path: Path) -> None:
    """Save to ``<path>.tmp``, then rename, so ``path`` is never half written."""
    tmp = Path(f"{path}.tmp")
    try:
        config.save(tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def seed(documents: list[dict], *, graph_password: str,
         kb_url: str = "http://kb:8765/query", graph_uri: str = "bolt://neo4j:7687",
         update_config: bool = False) -> Path:
    """Write the demo session and return its folder.

    With ``update_config`` only session.json of the existing session is rewritten,
    and ``documents`` is not used.
    """
    if len(graph_password) < MIN_PASSWORD_LENGTH:
        raise SeedError(f"Set NEO4J_PASSWORD to the Neo4j password "
                        f"(at least {MIN_PASSWORD_LENGTH} characters).")
    folder = session_dir(SESSION)
    config_path = session_config_path(SESSION)
    input_path = folder / "input.csv"
    config = _config(folder, graph_password, kb_url, graph_uri)

    if update_config:
        if not config_path.exists():
            raise SeedError(f"There is no demo session in {folder} to update; "
                            f"run without --update-config.")
        if not input_path.is_file():
            raise SeedError(f"{folder} has no input.csv, so this script did not make that "
                            f"session; its session.json is unchanged.")
        _save_config(config, config_path)
        return folder

    if config_path.exists():
        raise SeedError(f"The demo session already exists in {folder}; use --update-config "
                        f"to rewrite its session.json only.")
    if any(folder.glob("annotations/*.csv")) or any(folder.glob("annotated*.csv")):
        raise SeedError(f"{folder} holds saved answers but no session.json; its input.csv "
                        f"must not change, so restore session.json or move the folder away.")
    rows = build_rows(documents)  # before the first write

    context = folder / "context"
    context.mkdir(parents=True, exist_ok=True)
    for document in documents:
        (context / f"{document['id']}.txt").write_text(document["text"], encoding="utf-8")
    pd.DataFrame(rows).to_csv(input_path, index=False)
    _save_config(config, config_path)  # last: the app lists a session only once it has one
    return folder


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update-config", action="store_true",
                        help="rewrite only session.json of the existing demo session, e.g. "
                             "after the Neo4j password changed (undoes settings edited in the app)")
    args = parser.parse_args(argv)

    try:
        folder = seed(load_documents(), graph_password=os.environ.get("NEO4J_PASSWORD", ""),
                      update_config=args.update_config)
    except SeedError as error:
        raise SystemExit(str(error)) from None
    except OSError as error:
        raise SystemExit(f"Could not write the demo session: {error}") from None
    if args.update_config:
        print(f"Rewrote session.json in {folder}")
    else:
        print(f"Seeded session '{SESSION}' in {folder}")


if __name__ == "__main__":
    main()
