"""Protocol annotator: an LLM that answers with the same protocol human
annotators use (entity or NIL, main hint, confidence level, rationale), plus an
end-of-session reflection ranking the hints."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .feedback import CONFIDENCE, HINTS
from .llm_reranker import (MAX_CONTEXT_CHARS, LLMRerankError, LLMReranker,
                           _candidate_dict)

PROTOCOL_VERSION = "helix-protocol-v1"

AI_HINTS: list[tuple[str, str]] = [(c, l) for c, l in HINTS if c != "external_lookup"]
AI_HINT_CODES: list[str] = [c for c, _ in AI_HINTS]
LEVELS: list[str] = [c for c, _ in CONFIDENCE]
NIL = "NIL"

PROTOCOL_SYSTEM_PROMPT = (
    "You are one annotator in a biomedical entity-linking study. For each mention you "
    "see the document context and a ranked list of candidate knowledge-base entries. "
    "Answer from the listed candidates, or answer NIL if none of them fits.\n\n"
    "Report the single main hint that decided your answer, using one of these codes:\n"
    + "\n".join(f"- {c}: {l}" for c, l in AI_HINTS)
    + "\n\nReport a confidence level (sure, fairly_sure or unsure) and a number in [0, 1]. "
    "Give a one-sentence rationale. You have no external tools: you cannot look anything "
    "up outside what is shown. Always answer by calling the provided tool."
)

REFLECTION_PROMPT_HEAD = (
    "The annotation session is over. Below is how often you relied on each hint. "
    "Rank the hints from most to least useful for deciding correctly, and add a short "
    "comment. Accuracy is unknown to you."
)


@dataclass
class AnnotationDecision:
    entity_id: str
    hint: str
    confidence_level: str
    confidence: float
    rationale: str
    model: str
    provider: str
    latency_ms: int
    cached: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _render(cands: list[dict[str, Any]]) -> str:
    lines = []
    for i, c in enumerate(cands, 1):
        score = c["score"]
        head = f"[{i}] {c['id']}  {c['label']}"
        if isinstance(score, (int, float)):
            head += f"  (score {score:.3f})"
        parts = [head]
        if c["semantic_groups"] or c["semantic_types"]:
            parts.append(f"    types: {c['semantic_groups']} / {c['semantic_types']}")
        if c["aliases"]:
            parts.append("    aliases: " + ", ".join(c["aliases"]))
        if c["definition"]:
            parts.append(f"    definition: {c['definition']}")
        lines.append("\n".join(parts))
    return "\n".join(lines)


class LLMAnnotator(LLMReranker):
    CACHE_NAMESPACE = PROTOCOL_VERSION

    def __init__(self, *args, temperature: float | None = 0, **kwargs):
        kwargs.setdefault("system_prompt", PROTOCOL_SYSTEM_PROMPT)
        super().__init__(*args, temperature=temperature, **kwargs)

    def annotate(self, mention: str, context: str | None, candidates: Sequence[Any], *,
                 suggested_id: str | None = None,
                 graph_evidence: str = "") -> AnnotationDecision:
        cands = [_candidate_dict(c) for c in candidates][: self.top_n]
        if not cands:
            raise LLMRerankError("No candidates to annotate.")
        ctx = (context or "").strip()
        if len(ctx) > MAX_CONTEXT_CHARS:
            ctx = ctx[:MAX_CONTEXT_CHARS] + " …[truncated]"
        ids = [c["id"] for c in cands]
        key = self._cache_key(mention, ctx, cands,
                              extra={"suggested": suggested_id or "", "graph": graph_evidence})

        def call() -> dict[str, Any]:
            text = self._protocol_text(mention, ctx, cands, suggested_id, graph_evidence)
            t0 = time.perf_counter()
            data = self._request(text, self._protocol_schema(ids))
            latency = int((time.perf_counter() - t0) * 1000)
            d = self._finalize_protocol(data, ids)
            d.latency_ms = latency
            return d.to_dict()

        value, cached = self._cached_call(key, call)
        value = dict(value)
        value["cached"] = cached
        if cached:
            value["latency_ms"] = 0
        return AnnotationDecision(**value)

    def reflect(self, decisions: list[AnnotationDecision]) -> dict[str, Any]:
        counts = {c: 0 for c in AI_HINT_CODES}
        for d in decisions:
            if d.hint in counts:
                counts[d.hint] += 1
        table = "\n".join(f"{c}\t{l}\t{counts[c]}" for c, l in AI_HINTS)
        text = (f"{REFLECTION_PROMPT_HEAD}\n\nhint\tlabel\tcount\n{table}\n\n"
                f"Decisions made: {len(decisions)}")
        schema = {
            "type": "object",
            "properties": {
                # min/maxItems: guided decoders (vLLM) often ignore uniqueItems,
                # so at least pin the length; the permutation check below stays.
                "ranking": {"type": "array", "uniqueItems": True,
                            "minItems": len(AI_HINT_CODES), "maxItems": len(AI_HINT_CODES),
                            "items": {"type": "string", "enum": AI_HINT_CODES},
                            "description": "All hint codes, most useful first."},
                "comment": {"type": "string"},
            },
            "required": ["ranking", "comment"],
            "additionalProperties": False,
        }
        data = self._request(text, schema, "record_reflection")
        ranking = [str(x) for x in (data.get("ranking") or [])]
        if sorted(ranking) != sorted(AI_HINT_CODES):
            raise LLMRerankError("Reflection ranking is not a permutation of the hint codes.")
        return {"ranking": ranking + ["external_lookup"],
                "comment": str(data.get("comment", "")).strip()}

    # ── internals ────────────────────────────────────────────────────────────
    def _protocol_schema(self, ids: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "enum": ids + [NIL],
                              "description": "Chosen candidate id, or NIL if none fits."},
                "hint": {"type": "string", "enum": AI_HINT_CODES,
                         "description": "The single main hint that decided the answer."},
                "confidence_level": {"type": "string", "enum": LEVELS},
                "confidence": {"type": "number", "description": "Confidence in [0, 1]."},
                "rationale": {"type": "string", "description": "One-sentence justification."},
            },
            "required": ["entity_id", "hint", "confidence_level", "confidence", "rationale"],
            "additionalProperties": False,
        }

    def _protocol_text(self, mention, ctx, cands, suggested_id, graph_evidence) -> str:
        out = [f"Entity mention: {mention!r}", "",
               f"Document context:\n{ctx or '(no context provided)'}", "",
               f"Candidates:\n{_render(cands)}"]
        ids = [c["id"] for c in cands]
        if suggested_id and suggested_id in ids:
            out += ["", f"Suggested by the tool: [{ids.index(suggested_id) + 1}] {suggested_id}"]
        if graph_evidence and graph_evidence.strip():
            out += ["", f"Graph links:\n{graph_evidence.strip()}"]
        out += ["", "Choose the entity (or NIL) and report your hint and confidence."]
        return "\n".join(out)

    def _finalize_protocol(self, data: dict[str, Any], ids: list[str]) -> AnnotationDecision:
        entity_id = str(data.get("entity_id", "")).strip()
        if entity_id not in ids + [NIL]:
            raise LLMRerankError(f"Model chose id {entity_id!r} outside the candidate set.")
        hint = str(data.get("hint", "")).strip()
        if hint not in AI_HINT_CODES:
            raise LLMRerankError(f"Unknown hint {hint!r}.")
        level = str(data.get("confidence_level", "")).strip()
        if level not in LEVELS:
            raise LLMRerankError(f"Unknown confidence level {level!r}.")
        try:
            conf = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        return AnnotationDecision(entity_id, hint, level, conf,
                                  str(data.get("rationale", "")).strip(),
                                  self.model, self.provider, 0, False)
