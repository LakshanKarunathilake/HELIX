"""
LLM re-ranking (RAG disambiguation) for HELIX.

Hybrid retrieval (dense + lexical) supplies the candidate set; a Claude model
then acts as the disambiguator: given the mention, its document context, and the
top-N retrieved candidates, it selects the best match and returns a rationale +
confidence. The model can only choose from the ids we hand it (enforced via a
strict enum), so it can never invent a KB id that wasn't retrieved.

Design notes
------------
* Grounded: the ``select_entity`` tool's ``entity_id`` is an enum of the provided
  candidate ids — the response is always one of them.
* Cheap + reproducible: a fixed system prompt is prompt-cached, and every result
  is memoised on disk keyed by (model, mention, context, candidate set) so paper
  runs are deterministic and re-runs are free.
* Safe: any failure (no API key, network error, timeout, malformed response)
  raises ``LLMRerankError`` — callers fall back to the retrieval top-1 and never
  block the annotator.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5"   # throughput default; compare vs Sonnet/Opus in eval
DEFAULT_TOP_N = 10
MAX_CONTEXT_CHARS = 4000             # trim long documents before sending

_CACHE_DIR = Path(os.environ.get("HELIX_LLM_CACHE_DIR",
                                 Path.home() / ".cache" / "helix_llm_rerank"))

DEFAULT_SYSTEM_PROMPT = (
    "You are an expert biomedical/entity-linking annotator. You are given an entity "
    "mention, the document it appears in, and a numbered list of candidate knowledge-base "
    "entries retrieved for that mention. Your job is to choose the single candidate that "
    "the mention refers to.\n\n"
    "Rules:\n"
    "- You MUST pick exactly one candidate from the provided list, by its id.\n"
    "- Use the surrounding document context to disambiguate — the same surface form can map "
    "to different concepts depending on context.\n"
    "- Prefer the candidate whose label/aliases and semantic type best fit the mention in context.\n"
    "- If no candidate is a good match, still pick the closest one but report low confidence.\n"
    "- Give a one-sentence rationale and a calibrated confidence in [0, 1].\n"
    "Always answer by calling the select_entity tool."
)


class LLMRerankError(RuntimeError):
    """Raised when re-ranking cannot be completed; callers fall back to retrieval top-1."""


@dataclass
class RerankResult:
    entity_id: str
    rationale: str
    confidence: float
    ranking: list[str]          # candidate ids, best-first (best == entity_id)
    model: str
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "ranking": self.ranking,
            "model": self.model,
            "cached": self.cached,
        }


def _candidate_dict(c: Any) -> dict[str, Any]:
    """Accept either a Candidate dataclass or a plain dict."""
    if isinstance(c, dict):
        get = c.get
    else:
        get = lambda k, d=None: getattr(c, k, d)  # noqa: E731
    return {
        "id": str(get("id", "")),
        "label": str(get("label", "") or ""),
        "aliases": list(get("aliases", []) or [])[:8],
        "semantic_groups": str(get("semantic_groups", "") or ""),
        "semantic_types": str(get("semantic_types", "") or ""),
        "definition": (str(get("definition", "") or ""))[:400],
        "score": get("score", None),
    }


def _render_candidates(cands: list[dict[str, Any]]) -> str:
    lines = []
    for i, c in enumerate(cands, 1):
        parts = [f"[{i}] id={c['id']}  label={c['label']}"]
        if c["semantic_groups"] or c["semantic_types"]:
            parts.append(f"    type: {c['semantic_groups']} / {c['semantic_types']}")
        if c["aliases"]:
            parts.append("    aliases: " + ", ".join(c["aliases"]))
        if c["definition"]:
            parts.append(f"    def: {c['definition']}")
        lines.append("\n".join(parts))
    return "\n".join(lines)


class LLMReranker:
    """Claude-backed re-ranker over a retrieved candidate set."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        top_n: int = DEFAULT_TOP_N,
        api_key: str | None = None,
        system_prompt: str | None = None,
        use_cache: bool = True,
        timeout: float = 30.0,
    ):
        self.model = model or DEFAULT_MODEL
        self.top_n = top_n or DEFAULT_TOP_N
        self.timeout = timeout
        self.use_cache = use_cache
        self.system_prompt = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None  # lazily created

    # ── public API ───────────────────────────────────────────────────────────
    def rank(
        self,
        mention: str,
        context: str | None,
        candidates: Sequence[Any],
    ) -> RerankResult:
        cands = [_candidate_dict(c) for c in candidates][: self.top_n]
        if not cands:
            raise LLMRerankError("No candidates to re-rank.")

        ctx = (context or "").strip()
        if len(ctx) > MAX_CONTEXT_CHARS:
            ctx = ctx[:MAX_CONTEXT_CHARS] + " …[truncated]"

        cache_key = self._cache_key(mention, ctx, cands)
        if self.use_cache:
            hit = self._cache_read(cache_key)
            if hit is not None:
                return RerankResult(**{**hit, "cached": True})

        result = self._call_api(mention, ctx, cands)

        if self.use_cache:
            self._cache_write(cache_key, result.to_dict())
        return result

    # ── internals ────────────────────────────────────────────────────────────
    def _client_lazy(self):
        if self._client is None:
            if not self._api_key:
                raise LLMRerankError(
                    "ANTHROPIC_API_KEY is not set — cannot run LLM re-ranking."
                )
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise LLMRerankError("anthropic SDK is not installed.") from e
            self._client = anthropic.Anthropic(api_key=self._api_key, timeout=self.timeout)
        return self._client

    def _call_api(self, mention: str, ctx: str, cands: list[dict[str, Any]]) -> RerankResult:
        client = self._client_lazy()
        ids = [c["id"] for c in cands]

        tool = {
            "name": "select_entity",
            "description": "Record the chosen knowledge-base entity for the mention.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "enum": ids,
                        "description": "The id of the single best-matching candidate.",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Calibrated confidence in [0, 1].",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One-sentence justification.",
                    },
                    "ranking": {
                        "type": "array",
                        "items": {"type": "string", "enum": ids},
                        "description": "All candidate ids ordered best-first.",
                    },
                },
                "required": ["entity_id", "confidence", "rationale"],
                "additionalProperties": False,
            },
        }

        user_text = (
            f"Entity mention: {mention!r}\n\n"
            f"Document context:\n{ctx or '(no context provided)'}\n\n"
            f"Candidates:\n{_render_candidates(cands)}\n\n"
            "Select the candidate the mention refers to."
        )

        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=[tool],
                tool_choice={"type": "tool", "name": "select_entity"},
                messages=[{"role": "user", "content": user_text}],
            )
        except Exception as e:  # network, auth, rate limit, etc.
            raise LLMRerankError(f"LLM request failed: {e}") from e

        tool_use = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
        if tool_use is None:
            raise LLMRerankError("Model did not return a tool_use block.")
        data = tool_use.input or {}

        entity_id = str(data.get("entity_id", "")).strip()
        if entity_id not in ids:
            raise LLMRerankError(
                f"Model chose id {entity_id!r} outside the candidate set."
            )
        ranking = [str(x) for x in (data.get("ranking") or []) if str(x) in ids]
        if entity_id not in ranking:
            ranking = [entity_id] + [i for i in ranking if i != entity_id]
        # append any candidates the model omitted, preserving retrieval order
        ranking += [i for i in ids if i not in ranking]

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        return RerankResult(
            entity_id=entity_id,
            rationale=str(data.get("rationale", "")).strip(),
            confidence=confidence,
            ranking=ranking,
            model=self.model,
        )

    # ── on-disk cache ────────────────────────────────────────────────────────
    def _cache_key(self, mention: str, ctx: str, cands: list[dict[str, Any]]) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": self.system_prompt,
                "mention": mention,
                "context": ctx,
                "candidates": [{"id": c["id"], "label": c["label"]} for c in cands],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return _CACHE_DIR / f"{key}.json"

    def _cache_read(self, key: str) -> dict[str, Any] | None:
        path = self._cache_path(key)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _cache_write(self, key: str, value: dict[str, Any]) -> None:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._cache_path(key).write_text(
                json.dumps(value, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:  # pragma: no cover
            logger.warning("Failed to write LLM cache: %s", e)
