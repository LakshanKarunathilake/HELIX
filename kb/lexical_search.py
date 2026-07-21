#!/usr/bin/env python3
"""
LexicalSearcher — BM25 + character n-gram entity search for short queries.

Built at server startup from the metadata already loaded by SAPBERTIndexSearcher.
No separate index file or build step required.

Two internal indexes:
  1. BM25Okapi over alias documents (one doc per entity = all aliases joined)
  2. Character n-gram inverted index (bigrams + trigrams over every alias)

Results from both are fused via Reciprocal Rank Fusion and returned in the
same dict shape as SAPBERTIndexSearcher.search() so server.py can use them
interchangeably.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

import numpy as np

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    raise ImportError("rank-bm25 is required: pip install rank-bm25")

if TYPE_CHECKING:
    from search_sapbert_index import SAPBERTIndexSearcher

logger = logging.getLogger(__name__)

_RRF_K_DEFAULT   = 60
_NGRAM_DEFAULT   = [2, 3]


class LexicalSearcher:
    """BM25 + character n-gram search over entity aliases."""

    def __init__(self, sapbert: "SAPBERTIndexSearcher", cfg: dict) -> None:
        self._ngram_sizes: list[int] = cfg.get("ngram_sizes", _NGRAM_DEFAULT)
        self._rrf_k: int             = int(cfg.get("rrf_k", _RRF_K_DEFAULT))

        # entity_id → {primary_alias, all_aliases}
        self._entity_meta: dict[str, dict] = {}

        # BM25 parallel structures
        self._bm25_ids: list[str] = []
        self._bm25: BM25Okapi | None = None

        # n-gram inverted index: ngram → list[entity_id]
        self._ngram_index: dict[str, list[str]] = defaultdict(list)

        logger.info("LexicalSearcher: building indexes from metadata …")
        self._build(sapbert)
        logger.info(
            f"LexicalSearcher ready — {len(self._entity_meta):,} entities | "
            f"{len(self._ngram_index):,} unique n-grams | "
            f"n-gram sizes: {self._ngram_sizes}"
        )

    # ── build ────────────────────────────────────────────────────────────────

    def _build(self, sapbert: "SAPBERTIndexSearcher") -> None:
        corpus: list[list[str]] = []

        for entry in sapbert._iter_metadata():
            eid          = entry.get("entity_id", "")
            primary      = entry.get("primary_alias", "") or ""
            all_aliases  = entry.get("all_aliases") or []

            if not eid:
                continue

            # Accumulate entity meta (may be visited multiple times if per-alias indexed)
            if eid not in self._entity_meta:
                self._entity_meta[eid] = {
                    "primary_alias":  primary,
                    "all_aliases":    list(all_aliases),
                    "semantic_groups": entry.get("semantic_groups") or "",
                    "semantic_types":  entry.get("semantic_types") or "",
                    "definition":      entry.get("definition") or "",
                }
            else:
                # Merge aliases from additional entries
                existing = self._entity_meta[eid]["all_aliases"]
                for a in all_aliases:
                    if a not in existing:
                        existing.append(a)

        # Build BM25 + n-gram from de-duplicated entity_meta
        for eid, meta in self._entity_meta.items():
            aliases     = meta["all_aliases"]
            doc_text    = " ".join(aliases).lower()
            tokens      = doc_text.split()

            # BM25
            self._bm25_ids.append(eid)
            corpus.append(tokens)

            # N-gram index — each entity_id added once per n-gram
            seen_ngrams: set[str] = set()
            for alias in aliases:
                for ng in self._extract_ngrams(alias.lower()):
                    if ng not in seen_ngrams:
                        self._ngram_index[ng].append(eid)
                        seen_ngrams.add(ng)

        self._bm25 = BM25Okapi(corpus)

    def _extract_ngrams(self, text: str) -> list[str]:
        """Return all character n-grams for configured sizes."""
        ngrams = []
        for n in self._ngram_sizes:
            ngrams.extend(text[i: i + n] for i in range(len(text) - n + 1))
        return ngrams

    # ── per-source search ────────────────────────────────────────────────────

    def _search_bm25(self, query: str, top_k: int) -> list[str]:
        """Return ranked list of entity_ids from BM25."""
        tokens = query.lower().split()
        if not tokens:
            return []

        scores    = self._bm25.get_scores(tokens)
        max_score = scores.max()
        if max_score <= 0:
            return []

        ranked_idx = np.argsort(scores)[::-1][:top_k]
        return [
            self._bm25_ids[i]
            for i in ranked_idx
            if scores[i] > 0
        ]

    def _search_ngram(self, query: str, top_k: int) -> list[str]:
        """Return ranked list of entity_ids from character n-gram overlap."""
        query_ngrams = self._extract_ngrams(query.lower())
        if not query_ngrams:
            return []

        overlap: dict[str, int] = defaultdict(int)
        for ng in query_ngrams:
            for eid in self._ngram_index.get(ng, []):
                overlap[eid] += 1

        if not overlap:
            return []

        total = len(query_ngrams)
        ranked = sorted(overlap.items(), key=lambda x: x[1] / total, reverse=True)
        return [eid for eid, _ in ranked[:top_k]]

    # ── RRF fusion ───────────────────────────────────────────────────────────

    def _apply_rrf(self, ranked_lists: list[list[str]], top_k: int) -> list[tuple[str, float]]:
        scores: dict[str, float] = defaultdict(float)
        k = self._rrf_k
        for ranked in ranked_lists:
            for rank, eid in enumerate(ranked):
                scores[eid] += 1.0 / (k + rank + 1)

        sorted_entities = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_entities[:top_k]

    # ── public API ───────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 10) -> list[dict]:
        """
        Search for entities matching query using BM25 + n-gram fusion.
        Returns a list of dicts in the same shape as SAPBERTIndexSearcher.search():
          entity_id, primary_alias, all_aliases, processed_text, similarity_score, rank
        """
        bm25_ranked  = self._search_bm25(query, top_k=k * 2)
        ngram_ranked = self._search_ngram(query, top_k=k * 2)

        fused = self._apply_rrf([bm25_ranked, ngram_ranked], top_k=k)

        results = []
        for rank, (eid, rrf_score) in enumerate(fused, start=1):
            meta = self._entity_meta.get(eid, {})
            primary = meta.get("primary_alias", eid)
            all_aliases = meta.get("all_aliases", [primary])

            # Find the alias that best matches the query (for matched_alias field)
            q_lower = query.lower()
            matched = next(
                (a for a in all_aliases if a.lower() == q_lower),
                primary,
            )

            results.append({
                "entity_id":        eid,
                "primary_alias":    primary,
                "all_aliases":      all_aliases,
                "processed_text":   matched,
                "similarity_score": round(rrf_score, 6),
                "rank":             rank,
                "semantic_groups":  meta.get("semantic_groups", ""),
                "semantic_types":   meta.get("semantic_types", ""),
                "definition":       meta.get("definition", ""),
            })

        return results

    def get_stats(self) -> dict:
        return {
            "entity_count": len(self._entity_meta),
            "ngram_count":  len(self._ngram_index),
            "ngram_sizes":  self._ngram_sizes,
            "rrf_k":        self._rrf_k,
        }
