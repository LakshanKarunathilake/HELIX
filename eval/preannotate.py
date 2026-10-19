#!/usr/bin/env python3
"""Mode C: have a local LLM pre-annotate a review_mode session and route each row.

Run:  python eval/preannotate.py --session NAME --model openai/gpt-oss-120b \
        --base-url http://127.0.0.1:18000/v1 --api-key-file ~/.helix_llm_key
Rerun to retry rows that failed; already-routed rows are kept.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from eltool.llm_annotator import LLMAnnotator  # noqa: E402
from eltool.review import DEFAULT_SEED, preannotate  # noqa: E402
from helix_api import HelixAPI  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--provider", default="openai")
    ap.add_argument("--api-key-file", default=None)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--helix", default="http://127.0.0.1:8000")
    a = ap.parse_args(argv)
    key = Path(a.api_key_file).expanduser().read_text().strip() if a.api_key_file else None
    annotator = LLMAnnotator(model=a.model, provider=a.provider, base_url=a.base_url, api_key=key)
    try:
        stats = preannotate(a.session, annotator, HelixAPI(a.helix), seed=a.seed)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"{a.session}  {dict(stats)}")
    if stats.get("error"):
        print(f"{stats['error']} rows failed — rerun to retry them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
