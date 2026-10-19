#!/usr/bin/env python3
"""Mode B prewarm: fill the LLM cache for C4 sessions before annotators start.

C4 sessions run with ``llm_cache_only`` so a human never waits on the model.
This script walks every row exactly as the annotator's browser will — same
label, same context, same KB retrieval settings, same candidate fusion — and
asks the session's LLM for its pick with the cache enabled, so the live
/candidates endpoint later finds every answer on disk.

Needs HELIX running (for retrieval) and the LLM reachable. Prompts for nothing;
each session's own llm_* settings are used (API key from the session config,
else the provider's env var, as the reranker does).

Run:  python eval/prewarm_llm.py --prefix study3 [--helix http://127.0.0.1:8000]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import requests  # noqa: E402
import eltool.session as session_mod  # noqa: E402  (module attr read at call time)
from eltool.config import SessionConfig  # noqa: E402
from eltool.llm_reranker import LLMRerankError  # noqa: E402
from api.main import _fuse_candidates, _get_reranker  # noqa: E402
from helix_api import HelixAPI  # noqa: E402


def _session_config(session: str, api: HelixAPI) -> SessionConfig:
    """Read from disk when local (GET masks the API key); else over HTTP."""
    path = session_mod.session_config_path(session)
    if path.exists():
        return SessionConfig.load(path)
    return SessionConfig(**api.config(session))


def _fused(resp: dict) -> list[SimpleNamespace]:
    """The endpoint's own fusion over the serialised candidates. Wrapping the
    dicts keeps `_fuse_candidates` (attribute access) as the single definition
    of the order, so the cache key matches the live endpoint's."""
    dense = [SimpleNamespace(**c) for c in resp.get("dense_candidates") or []]
    lexical = [SimpleNamespace(**c) for c in resp.get("lexical_candidates") or []]
    return _fuse_candidates(dense, lexical)


def prewarm_session(session: str, api: HelixAPI) -> Counter:
    cfg = _session_config(session, api)
    stats: Counter = Counter({"cached": 0, "computed": 0, "failed": 0})
    if not cfg.enable_llm_rerank:
        return stats
    reranker = _get_reranker(
        cfg.llm_model, cfg.llm_top_n, cfg.llm_prompt, cfg.llm_api_key,
        provider=cfg.llm_provider, base_url=cfg.llm_base_url, cache_only=False,
    )
    total = api.row(session, 0)["total"]
    for i in range(total):
        try:
            row = api.row(session, i)
            context = row.get("context_text")
            for kb in cfg.kbs:
                fused = _fused(api.candidates(session, row["label"], kb.name, context))
                if not fused:      # the endpoint skips the LLM too
                    continue
                result = reranker.rank(row["label"], context, fused)
                stats["cached" if result.cached else "computed"] += 1
        except (LLMRerankError, requests.RequestException) as e:
            print(f"  {session} row {i}: {e}", file=sys.stderr)
            stats["failed"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--prefix", required=True, help="session folder prefix, e.g. study3")
    ap.add_argument("--helix", default="http://127.0.0.1:8000")
    a = ap.parse_args(argv)

    api = HelixAPI(a.helix)
    root = session_mod.SESSIONS_DIR
    sessions = sorted(p.name for p in root.glob(a.prefix + "*")
                      if p.is_dir() and (p / "session.json").exists())
    failed = 0
    for name in sessions:
        cfg = json.loads((root / name / "session.json").read_text())
        if not cfg.get("enable_llm_rerank"):
            continue
        stats = prewarm_session(name, api)
        failed += stats["failed"]
        print(f"{name:24s} {dict(stats)}")
    if failed:
        print(f"{failed} rows failed — rerun to retry them (cached rows are skipped)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
