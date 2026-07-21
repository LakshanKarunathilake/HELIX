#!/usr/bin/env python3
"""
SAPBERT KB REST server for ELTool.

QUICK START (run from the ELTool root):

    python kb/server.py

Configuration is read from kb/config.yaml. CLI flags and environment
variables override the file values when supplied.

CLI overrides:
    python kb/server.py --config kb/config.yaml   # explicit config path
    python kb/server.py --index_path /other/index --port 9000

Environment variable overrides:
    KB_CONFIG       path to a different config file
    KB_INDEX_PATH   index prefix path
    KB_ADAPTER_PATH local adapter directory
    KB_ADAPTER_NAME HF Hub adapter name
    KB_HOST         bind host
    KB_PORT         port

─────────────────────────────────────────────────────────────────────────────
HTTP API

  POST /query
      Body:    {"label": "<mention>", "top_k": <int>}
      Returns: {"results": [{"id": "...", "label": "...", "score": 0.97,
                             "aliases": [...], "rank": 1}, ...]}

  GET  /health       — liveness + index stats
  GET  /entity/<id>  — look up one entity by id

─────────────────────────────────────────────────────────────────────────────
ELTool KB config (Configure → Add KB):
    Endpoint URL      http://127.0.0.1:8765/query
    Method            POST
    request_template  {"label": "{label}", "top_k": {top_k}}
    response_path     results
    id_field          id
    label_field       label
    score_field       score
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required: pip install pyyaml")

# ── importable from any cwd ─────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "kb"))

from search_sapbert_index import SAPBERTIndexSearcher  # noqa: E402
from lexical_search import LexicalSearcher             # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
searcher: SAPBERTIndexSearcher | None = None
lexical:  LexicalSearcher | None = None
LEX_THRESHOLD = 8  # used only in main() to log info; /query always uses FAISS

_DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.yaml"


# ── config loading ───────────────────────────────────────────────────────────

def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _resolve_config(args: argparse.Namespace) -> dict:
    """Merge config.yaml → env vars → CLI flags (highest priority wins)."""
    config_path = Path(args.config) if args.config else Path(os.environ.get("KB_CONFIG", _DEFAULT_CONFIG))
    cfg = _load_yaml(config_path)
    logger.info(f"Loaded config from {config_path}")

    # Resolved values: file → env → CLI
    index_path   = (args.index_path
                    or os.environ.get("KB_INDEX_PATH")
                    or cfg.get("index", {}).get("path", ""))
    adapter_path = (args.adapter_path
                    or os.environ.get("KB_ADAPTER_PATH")
                    or cfg.get("adapter", {}).get("path"))
    adapter_name = (args.adapter_name
                    or os.environ.get("KB_ADAPTER_NAME")
                    or cfg.get("adapter", {}).get("name"))
    host         = (args.host
                    or os.environ.get("KB_HOST")
                    or cfg.get("server", {}).get("host", "127.0.0.1"))
    port         = (args.port
                    or int(os.environ.get("KB_PORT", 0))
                    or cfg.get("server", {}).get("port", 8765))

    return dict(
        index_path=index_path,
        adapter_path=adapter_path or None,
        adapter_name=adapter_name or None,
        host=host,
        port=int(port),
    )


# ── helpers ──────────────────────────────────────────────────────────────────

def _result_to_eltool(result: dict) -> dict:
    """Normalise one search result; include full alias list for disambiguation."""
    all_aliases   = result.get("all_aliases") or result.get("aliases") or []
    primary_alias = result.get("primary_alias") or (all_aliases[0] if all_aliases else "")
    matched_alias = result.get("processed_text") or primary_alias  # the variant that was embedded
    label         = primary_alias or result.get("entity_id", "")
    return {
        "id":              result.get("entity_id", ""),
        "label":           label,
        "score":           result.get("similarity_score"),
        "matched_alias":   matched_alias,
        "aliases":         all_aliases,
        "semantic_groups": result.get("semantic_groups") or "",
        "semantic_types":  result.get("semantic_types") or "",
        "definition":      result.get("definition") or "",
        "rank":            result.get("rank"),
    }


# ── routes ───────────────────────────────────────────────────────────────────

@app.post("/query")
def query():
    body  = request.get_json(force=True, silent=True) or {}
    label = str(body.get("label", "")).strip()
    print("searching for:", label)
    top_k = int(body.get("top_k", 10))

    if not label:
        return jsonify({"error": "label is required"}), 400
    if not (1 <= top_k <= 100):
        return jsonify({"error": "top_k must be 1–100"}), 400

    try:
        raw = searcher.search(label, k=top_k, return_scores=True)
        return jsonify({"results": [_result_to_eltool(r) for r in raw]})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Search failed")
        return jsonify({"error": str(exc)}), 500


@app.post("/query/dense")
def query_dense():
    """Always uses FAISS dense search regardless of query length."""
    body  = request.get_json(force=True, silent=True) or {}
    label = str(body.get("label", "")).strip()
    top_k = int(body.get("top_k", 10))

    if not label:
        return jsonify({"error": "label is required"}), 400
    if not (1 <= top_k <= 100):
        return jsonify({"error": "top_k must be 1–100"}), 400

    try:
        raw = searcher.search(label, k=top_k, return_scores=True)
        return jsonify({"results": [_result_to_eltool(r) for r in raw]})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Dense search failed")
        return jsonify({"error": str(exc)}), 500


@app.post("/query/lexical")
def query_lexical():
    """Always uses lexical search regardless of query length. Useful for testing."""
    body  = request.get_json(force=True, silent=True) or {}
    label = str(body.get("label", "")).strip()
    top_k = int(body.get("top_k", 10))

    if not label:
        return jsonify({"error": "label is required"}), 400
    if not (1 <= top_k <= 100):
        return jsonify({"error": "top_k must be 1–100"}), 400
    if lexical is None:
        return jsonify({"error": "lexical index not initialised"}), 503

    try:
        raw = lexical.search(label, k=top_k)
        return jsonify({"results": [_result_to_eltool(r) for r in raw]})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Lexical search failed")
        return jsonify({"error": str(exc)}), 500


@app.get("/health")
def health():
    if searcher is None or searcher.index is None:
        return jsonify({"status": "not_ready"}), 503
    return jsonify({
        "status": "ok",
        "index":   searcher.get_index_stats(),
        "lexical": lexical.get_stats() if lexical else None,
    })


@app.get("/entity/<entity_id>")
def get_entity(entity_id: str):
    try:
        ent = searcher.get_entity_by_id(entity_id)
        if ent is None:
            return jsonify({"error": f"'{entity_id}' not found"}), 404
        return jsonify(ent)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SAPBERT KB REST server for ELTool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--config",       default=None, metavar="FILE",
                   help=f"Config file path (default: kb/config.yaml). Env: KB_CONFIG")
    p.add_argument("--index_path",   default=None, metavar="PATH",
                   help="Override index.path from config. Env: KB_INDEX_PATH")
    p.add_argument("--adapter_path", default=None, metavar="PATH",
                   help="Override adapter.path from config. Env: KB_ADAPTER_PATH")
    p.add_argument("--adapter_name", default=None, metavar="NAME",
                   help="Override adapter.name from config. Env: KB_ADAPTER_NAME")
    p.add_argument("--host",         default=None,
                   help="Override server.host from config. Env: KB_HOST")
    p.add_argument("--port",         default=None, type=int,
                   help="Override server.port from config. Env: KB_PORT")
    return p.parse_args()


# ── entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    global searcher, lexical, LEX_THRESHOLD
    args = _parse_args()
    cfg  = _resolve_config(args)

    if not cfg["index_path"]:
        sys.exit(
            "ERROR: index path is not set.\n"
            "Edit index.path in kb/config.yaml, or pass --index_path <prefix>."
        )

    logger.info("Initialising SAPBERTIndexSearcher …")
    searcher = SAPBERTIndexSearcher(
        adapter_path=cfg["adapter_path"],
        adapter_name=cfg["adapter_name"],
    )

    logger.info(f"Loading index: {cfg['index_path']}")
    index_cfg = searcher.load_index(cfg["index_path"])
    logger.info(
        f"Index ready — {len(searcher.metadata):,} entities | "
        f"model: {index_cfg.get('model_name')} | "
        f"adapter: {'ON' if searcher.use_adapter else 'OFF'}"
    )

    # Build lexical index from the same metadata (no extra files needed)
    config_path = Path(args.config) if args.config else Path(os.environ.get("KB_CONFIG", _DEFAULT_CONFIG))
    lex_cfg     = _load_yaml(config_path).get("lexical", {})
    if lex_cfg.get("enabled", True):
        logger.info("Building lexical index …")
        lexical = LexicalSearcher(searcher, lex_cfg)
        logger.info("Lexical index ready.")
    else:
        logger.warning("Lexical search is DISABLED in config (lexical.enabled: false). "
                       "POST /query/lexical will return 503.")

    logger.info(f"Server starting on http://{cfg['host']}:{cfg['port']}")
    app.run(host=cfg["host"], port=cfg["port"], debug=False)


if __name__ == "__main__":
    main()
